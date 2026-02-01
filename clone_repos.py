#!/usr/bin/env python3

"""
Repository Cloner Script
Reads project information from CSV and clones repositories with commit handling
"""

import csv
import os
import subprocess
import sys
import argparse
import re
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).parent.absolute()
DEFAULT_CSV_FILE = BASE_DIR / "data" / "target_projects.csv"
CLONE_DIR = BASE_DIR / "cloned_projects"

def run_command(cmd, cwd=None):
    """Execute shell command and return (success, output)"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {cmd}")
        print(f"Error: {e.stderr}")
        return False, e.stderr

def sanitize_name(name):
    """Sanitize project name for directory use"""
    # Replace spaces and special characters with underscores
    sanitized = re.sub(r'[^a-zA-Z0-9\-_]', '_', name)
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_')

def clone_repository(dir_name, git_url, branch, commit_id):
    """Clone repository and checkout specific commit if provided"""
    
    project_path = CLONE_DIR / dir_name
    
    # Remove existing directory if it exists
    if project_path.exists():
        print(f"🗑️  Removing existing directory: {dir_name}")
        run_command(f'rm -rf "{project_path}"')
    
    print(f"\n🔹 Cloning: {dir_name}")
    print(f"   URL: {git_url}")
    print(f"   Branch: {branch}")
    
    # Fast Cloning Optimization
    # If a specific commit is provided, we need the commit history, 
    # so we use blobless clone --filter=blob:none to save bandwidth
    # If no commit is provided, we use a shallow clone --depth 1
    if commit_id and commit_id.strip():
        clone_cmd = f'git clone -b {branch} --filter=blob:none {git_url} "{project_path}"'
    else:
        clone_cmd = f'git clone -b {branch} --depth 1 {git_url} "{project_path}"'
    success, _ = run_command(clone_cmd, cwd=CLONE_DIR)
    if not success:
        print(f"❌ Failed to clone to {dir_name}")
        return False
    
    # Checkout specific commit if provided
    if commit_id and commit_id.strip():
        print(f"   Commit: {commit_id}")
        checkout_cmd = f'git checkout {commit_id}'
        success, _ = run_command(checkout_cmd, cwd=project_path)
        if not success:
            print(f"⚠️  Warning: Failed to checkout commit {commit_id}")
            print(f"   Using latest commit from {branch} branch")
    else:
        # Get latest commit hash for logging
        success, latest_commit = run_command('git rev-parse --short HEAD', cwd=project_path)
        if success:
            print(f"   Commit: {latest_commit} (latest)")
    
    print(f"✅ Successfully cloned: {dir_name}")
    return True

def main():
    """Main execution function"""
    parser = argparse.ArgumentParser(description="Repository Cloner for SCA Scanner")
    parser.add_argument("--project", help="Name of a specific project to clone")
    parser.add_argument("--index-id", type=int, help="Index of a specific project to clone (1-indexed)")
    parser.add_argument("--list", action="store_true", help="List all project names from CSV in structured format")
    parser.add_argument("--csv", help="Custom CSV file path")
    args = parser.parse_args()

    csv_file = Path(args.csv) if args.csv else DEFAULT_CSV_FILE

    # Check if CSV file exists
    if not csv_file.exists():
        print(f"❌ CSV file not found: {csv_file}")
        sys.exit(1)

    # Create clone directory
    CLONE_DIR.mkdir(exist_ok=True)
    
    # Read CSV and process projects
    successful_clones = 0
    failed_clones = 0
    projects_found = 0
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for i, row in enumerate(reader, 1):
            project_name = row.get('project_name', '').strip()
            git_url = row.get('git_url', '').strip()
            branch = row.get('branch', 'main').strip() or 'main'
            commit_id = row.get('commit_id', '').strip()
            
            if not project_name or not git_url:
                continue
            
            # Create a unique directory name using index and sanitized name
            sanitized = sanitize_name(project_name)
            dir_name = f"{sanitized}_{i}"
            
            if args.list:
                # Output format: index|original_name|dir_name
                print(f"{i}|{project_name}|{dir_name}")
                continue

            # Check if this specific project should be cloned
            is_target = False
            if args.index_id:
                if i == args.index_id:
                    is_target = True
            elif args.project:
                if project_name == args.project:
                    is_target = True
            else:
                # If no filters, clone everything
                is_target = True
            
            if not is_target:
                continue
                
            projects_found += 1
            if clone_repository(dir_name, git_url, branch, commit_id):
                successful_clones += 1
            else:
                failed_clones += 1

    if args.list:
        return

    if (args.project or args.index_id) and projects_found == 0:
        target = args.project if args.project else f"index {args.index_id}"
        print(f"❌ Project '{target}' not found in CSV.")
        sys.exit(1)

    if failed_clones > 0:
        sys.exit(1)

    if not args.project and not args.index_id:
        print(f"\n✨ All {successful_clones} repositories cloned successfully!")

if __name__ == "__main__":
    main()
