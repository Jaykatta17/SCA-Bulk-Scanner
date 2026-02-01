#!/usr/bin/env python3

"""
SCA Scanner - Unified Bulk Vulnerability Scanner
Clones repositories from CSV, generates SBOMs, and scans for vulnerabilities.
"""

import argparse
import csv
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from database import get_db_path, init_db, save_scan

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# === Base Directories ===
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


# =============================================================================
# Configuration
# =============================================================================

def load_config() -> dict:
    """Load configuration from config.yaml"""
    if not CONFIG_PATH.exists():
        logger.error(f"❌ Configuration file not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# =============================================================================
# Utility Functions
# =============================================================================

def run_command(cmd: str, cwd: Optional[Path] = None, capture: bool = True) -> tuple[bool, str]:
    """Execute shell command and return (success, output)"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=True
        )
        return True, result.stdout.strip() if capture else ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr if capture else str(e)


def sanitize_name(name: str) -> str:
    """Sanitize project name for directory/file use"""
    sanitized = re.sub(r'[^a-zA-Z0-9\-_]', '_', name)
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_')


def get_unique_dir_name(project_name: str, index: int) -> str:
    """Generate a unique directory name for cloning"""
    return f"{sanitize_name(project_name)}_{index}"


def generate_report_id() -> str:
    """Generate an 8-character unique ID for report filenames"""
    return uuid.uuid4().hex[:8]


def format_time(seconds: int) -> str:
    """Format seconds to human-readable time (Xm Ys)"""
    m, s = divmod(seconds, 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# =============================================================================
# Repository Cloning
# =============================================================================

def clone_repository(
    clone_dir: Path,
    project_name: str,
    git_url: str,
    branch: str,
    commit_id: str,
    index: int,
    retries: int = 3,
    backoff_seconds: int = 1
) -> tuple[bool, Optional[Path]]:
    """Clone a repository with retry logic and return (success, project_path)"""
    dir_name = get_unique_dir_name(project_name, index)
    project_path = clone_dir / dir_name

    # Remove existing directory if it exists
    if project_path.exists():
        shutil.rmtree(project_path, ignore_errors=True)

    # Fast cloning: use shallow or blobless clone
    if commit_id and commit_id.strip():
        clone_cmd = f'git clone -b {branch} --filter=blob:none {git_url} "{project_path}"'
    else:
        clone_cmd = f'git clone -b {branch} --depth 1 {git_url} "{project_path}"'

    # Retry loop with exponential backoff
    for attempt in range(1, retries + 1):
        success, error = run_command(clone_cmd, cwd=clone_dir)
        if success:
            break
        if attempt < retries:
            wait_time = backoff_seconds * (2 ** (attempt - 1))
            logger.warning(f"   ⚠️  Clone attempt {attempt}/{retries} failed, retrying in {wait_time}s...")
            time.sleep(wait_time)
            # Clean up failed attempt
            if project_path.exists():
                shutil.rmtree(project_path, ignore_errors=True)
    
    if not success:
        return False, None

    # Checkout specific commit if provided
    if commit_id and commit_id.strip():
        run_command(f"git checkout {commit_id}", cwd=project_path)

    return True, project_path


# =============================================================================
# Project Scanning
# =============================================================================

def process_project(
    config: dict,
    base_dir: Path,
    report_dir: Path,
    template_path: Path,
    scan_date: str,
    project_info: dict,
    scan_id: int,
    total_projects: int
) -> dict:
    """Process a single project: clone, generate SBOM, scan, generate report"""
    project_name = project_info["project_name"]
    git_url = project_info["git_url"]
    branch = project_info.get("branch", "main") or "main"
    commit_id = project_info.get("commit_id", "")
    index = project_info["index"]

    result = {
        "project_name": project_name,
        "scan_id": scan_id,
        "success": False,
        "duration": 0,
        "report_path": None
    }

    start_time = time.time()
    logger.info(f"🚀 [{scan_id} of {total_projects}] Starting: {project_name}")

    # Clone repository with retry logic
    clone_dir = base_dir / config["paths"]["cloned_projects_dir"]
    clone_dir.mkdir(exist_ok=True)
    
    cloning_config = config.get("cloning", {})
    retries = cloning_config.get("retries", 3)
    backoff = cloning_config.get("backoff_seconds", 1)

    success, project_path = clone_repository(
        clone_dir, project_name, git_url, branch, commit_id, index,
        retries=retries, backoff_seconds=backoff
    )

    if not success or project_path is None:
        logger.info(f"   ❌ [{scan_id}] Cloning failed: {project_name}")
        result["duration"] = int(time.time() - start_time)
        return result

    # Check if it's a Git repo
    is_git, _ = run_command("git rev-parse --is-inside-work-tree", cwd=project_path)
    if not is_git:
        logger.info(f"   ⚠️  [{scan_id}] Not a Git repo: {project_name}")
        shutil.rmtree(project_path, ignore_errors=True)
        result["duration"] = int(time.time() - start_time)
        return result

    # Get branch and commit info
    _, current_branch = run_command("git rev-parse --abbrev-ref HEAD", cwd=project_path)
    _, commit_hash = run_command("git rev-parse --short HEAD", cwd=project_path)
    safe_branch = current_branch.replace("/", "_")

    # Docker images
    syft_image = config["docker"]["syft_image"]
    grype_image = config["docker"]["grype_image"]

    # Generate SBOM
    sbom_path = project_path / "sbom.json"
    syft_cmd = (
        f'docker run --rm -e SYFT_CHECK_FOR_APP_UPDATE=false '
        f'--name "SYFT-scanner-{scan_id}" '
        f'-v "{project_path}:/app" '
        f'{syft_image} /app/ -o cyclonedx-json'
    )

    success, sbom_output = run_command(syft_cmd, cwd=project_path)
    if success:
        sbom_path.write_text(sbom_output, encoding="utf-8")
    else:
        logger.info(f"   ❌ [{scan_id}] SBOM failed: {project_name}")
        shutil.rmtree(project_path, ignore_errors=True)
        result["duration"] = int(time.time() - start_time)
        return result

    # Inject variables into template
    temp_template = Path(f"/tmp/html-injected-{scan_id}.tmpl")
    template_content = template_path.read_text(encoding="utf-8")
    template_content = template_content.replace("PROJECT_VAR", project_name)
    template_content = template_content.replace("BRANCH_VAR", current_branch)
    template_content = template_content.replace("COMMIT_VAR", commit_hash)
    temp_template.write_text(template_content, encoding="utf-8")

    # Generate unique report filename
    safe_project_name = sanitize_name(project_name)
    report_id = generate_report_id()
    output_file = report_dir / f"SCA-{safe_project_name}-{safe_branch}_{commit_hash}-{scan_date}-{report_id}.html"

    # Run Grype scan
    grype_cmd = (
        f'docker run --rm -e GRYPE_BY_CVE=true '
        f'--name "grype-scanner-{scan_id}" '
        f'-v "{project_path}:/app" '
        f'-v "{temp_template}:{temp_template}" '
        f'{grype_image} sbom:/app/sbom.json -o template -t "{temp_template}"'
    )

    success, report_output = run_command(grype_cmd, cwd=project_path)

    if success:
        # Clean up location paths in HTML (convert [Location<RealPath="...">] to just the path)
        report_output = re.sub(
            r'<td>\[Location<RealPath="([^"]+)"[^>]*>\]</td>',
            r'<td>\1</td>',
            report_output
        )
        output_file.write_text(report_output, encoding="utf-8")
        duration = int(time.time() - start_time)
        formatted_time = format_time(duration)
        logger.info(f"   ✅ [{scan_id}] Report generated: {project_name} (Time: {formatted_time})")
        result["success"] = True
        result["report_path"] = str(output_file)
    else:
        logger.info(f"   ❌ [{scan_id}] Scan failed: {project_name}")

    # Cleanup
    temp_template.unlink(missing_ok=True)
    shutil.rmtree(project_path, ignore_errors=True)

    result["duration"] = int(time.time() - start_time)
    return result


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_scan(args):
    """Run the bulk SCA scan"""
    json_output = getattr(args, 'json', False)
    
    # Suppress logging if JSON output requested
    if json_output:
        logger.setLevel(logging.ERROR)
    
    start_time = time.time()

    # Load configuration
    config = load_config()

    # Setup paths
    base_dir = SCRIPT_DIR
    report_dir = base_dir / config["paths"]["reports_dir"]
    csv_file = base_dir / config["paths"]["csv_file"]
    template_path = base_dir / config["paths"]["html_template"]
    clone_dir = base_dir / config["paths"]["cloned_projects_dir"]
    scan_date = datetime.now().strftime("%Y-%m-%d")

    # Create reports directory
    report_dir.mkdir(exist_ok=True)

    # Cleanup previous clones
    if clone_dir.exists():
        logger.info("🧹 Cleaning up previous cloned projects...")
        shutil.rmtree(clone_dir, ignore_errors=True)

    logger.info("============================================================")
    logger.info("🚀 SCA Scanner - Bulk Scan Automation")
    logger.info("============================================================")
    logger.info(f"📁 Base directory: {base_dir}")
    logger.info(f"📊 Reports will be saved in: {report_dir}")
    logger.info(f"📄 Using template: {template_path}")
    logger.info("============================================================")

    # Read projects from CSV
    if not csv_file.exists():
        logger.error(f"❌ CSV file not found: {csv_file}")
        sys.exit(1)

    projects = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            project_name = row.get("project_name", "").strip()
            git_url = row.get("git_url", "").strip()
            if project_name and git_url:
                projects.append({
                    "index": i,
                    "project_name": project_name,
                    "git_url": git_url,
                    "branch": row.get("branch", "main").strip() or "main",
                    "commit_id": row.get("commit_id", "").strip()
                })

    total_projects = len(projects)
    logger.info("")
    logger.info(f"🔍 Processing {total_projects} projects in parallel")
    logger.info("============================================================")

    # Process projects in parallel
    max_parallel = config["parallelism"]["max_parallel"]
    results = []

    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {
            executor.submit(
                process_project,
                config, base_dir, report_dir, template_path, scan_date,
                project, scan_id, total_projects
            ): project
            for scan_id, project in enumerate(projects, 1)
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"   ❌ Error processing project: {e}")
                results.append({"success": False, "duration": 0})

    # Final summary
    total_time = int(time.time() - start_time)
    formatted_total_time = format_time(total_time)
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    # JSON output mode
    if json_output:
        json_result = {
            "scan_date": scan_date,
            "total_projects": len(results),
            "successful": success_count,
            "failed": fail_count,
            "duration_seconds": total_time,
            "reports_dir": str(report_dir),
            "results": results
        }
        print(json.dumps(json_result, indent=2))
    else:
        logger.info("")
        logger.info("============================================================")
        logger.info("📊 SCAN SUMMARY")
        logger.info("============================================================")
        logger.info(f"   Total projects processed: {len(results)}")
        logger.info(f"   ✅ Successful scans: {success_count}")
        logger.info(f"   ❌ Failed scans: {fail_count}")
        logger.info(f"   ⏱️  Total time taken: {formatted_total_time}")
        logger.info(f"   📁 Reports location: {report_dir}")
        logger.info("============================================================")
        logger.info("✨ Bulk SCA Scan Complete!")
        logger.info("============================================================")

    # Save to database
    no_db = getattr(args, 'no_db', False)
    if not no_db:
        try:
            db_path = get_db_path(config)
            conn = init_db(db_path)
            scan_id = save_scan(conn, scan_date, results, total_time)
            conn.close()
            if not json_output:
                logger.info(f"   💾 Scan saved to database (ID: {scan_id})")
        except Exception as e:
            logger.warning(f"   ⚠️  Failed to save to database: {e}")

    if fail_count > 0:
        sys.exit(1)


def cmd_list(args):
    """List all projects from CSV"""
    config = load_config()
    csv_file = SCRIPT_DIR / config["paths"]["csv_file"]

    if not csv_file.exists():
        logger.error(f"❌ CSV file not found: {csv_file}")
        sys.exit(1)

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            project_name = row.get("project_name", "").strip()
            git_url = row.get("git_url", "").strip()
            if project_name and git_url:
                dir_name = get_unique_dir_name(project_name, i)
                print(f"{i}|{project_name}|{dir_name}")


def cmd_clone(args):
    """Clone repositories from CSV"""
    config = load_config()
    csv_file = SCRIPT_DIR / config["paths"]["csv_file"]
    clone_dir = SCRIPT_DIR / config["paths"]["cloned_projects_dir"]

    if not csv_file.exists():
        logger.error(f"❌ CSV file not found: {csv_file}")
        sys.exit(1)

    clone_dir.mkdir(exist_ok=True)

    successful = 0
    failed = 0

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            project_name = row.get("project_name", "").strip()
            git_url = row.get("git_url", "").strip()
            branch = row.get("branch", "main").strip() or "main"
            commit_id = row.get("commit_id", "").strip()

            if not project_name or not git_url:
                continue

            # Filter by index if specified
            if args.index and i != args.index:
                continue

            logger.info(f"\n🔹 Cloning: {project_name}")
            logger.info(f"   URL: {git_url}")
            logger.info(f"   Branch: {branch}")

            success, project_path = clone_repository(
                clone_dir, project_name, git_url, branch, commit_id, i
            )

            if success:
                logger.info(f"✅ Successfully cloned: {project_name}")
                successful += 1
            else:
                logger.info(f"❌ Failed to clone: {project_name}")
                failed += 1

    if not args.index:
        logger.info(f"\n✨ Cloning complete: {successful} successful, {failed} failed")

    if failed > 0:
        sys.exit(1)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point with CLI argument parsing"""
    parser = argparse.ArgumentParser(
        description="SCA Scanner - Bulk Vulnerability Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sca_scanner.py scan          # Run bulk scan
  python sca_scanner.py list          # List projects from CSV
  python sca_scanner.py clone         # Clone all repositories
  python sca_scanner.py clone --index 1  # Clone only project at index 1
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Run bulk SCA scan")
    scan_parser.add_argument("--json", action="store_true", help="Output results as JSON")
    scan_parser.add_argument("--no-db", action="store_true", help="Skip saving to database")
    scan_parser.set_defaults(func=cmd_scan)

    # list command
    list_parser = subparsers.add_parser("list", help="List projects from CSV")
    list_parser.set_defaults(func=cmd_list)

    # clone command
    clone_parser = subparsers.add_parser("clone", help="Clone repositories")
    clone_parser.add_argument("--index", type=int, help="Clone only project at this index (1-indexed)")
    clone_parser.set_defaults(func=cmd_clone)

    args = parser.parse_args()

    if args.command is None:
        # Default to scan if no command specified
        args.func = cmd_scan
        cmd_scan(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
