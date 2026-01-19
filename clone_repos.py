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
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).parent.absolute()
CSV_FILE = BASE_DIR / "data" / "target_projects.csv"
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

def clone_repository(project_name, git_url, branch, commit_id):
    """Clone repository and checkout specific commit if provided"""
    
    project_path = CLONE_DIR / project_name
    
    # Remove existing directory if it exists
    if project_path.exists():
        print(f"🗑️  Removing existing directory: {project_name}")
        run_command(f'rm -rf "{project_path}"')
    
    print(f"\n🔹 Cloning: {project_name}")
    print(f"   URL: {git_url}")
    print(f"   Branch: {branch}")
    
    # Clone repository with specific branch
    clone_cmd = f'git clone -b {branch} {git_url} "{project_path}"'
    success, _ = run_command(clone_cmd, cwd=CLONE_DIR)
    if not success:
        print(f"❌ Failed to clone {project_name}")
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
    
    print(f"✅ Successfully cloned: {project_name}")
    return True

def main():
    """Main execution function"""
    parser = argparse.ArgumentParser(description="Repository Cloner for SCA Scanner")
    parser.add_argument("--project", help="Name of a specific project to clone")
    parser.add_argument("--list", action="store_true", help="List all project names from CSV")
    args = parser.parse_args()

    # Check if CSV file exists
    if not CSV_FILE.exists():
        print(f"❌ CSV file not found: {CSV_FILE}")
        sys.exit(1)

    if args.list:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('project_name', '').strip()
                if name:
                    print(name)
        return

    # Create clone directory
    CLONE_DIR.mkdir(exist_ok=True)
    
    # Read CSV and process projects
    successful_clones = 0
    failed_clones = 0
    projects_found = 0
    
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            project_name = row.get('project_name', '').strip()
            git_url = row.get('git_url', '').strip()
            branch = row.get('branch', 'main').strip() or 'main'
            commit_id = row.get('commit_id', '').strip()
            
            if not project_name or not git_url:
                continue
                
            # If a specific project is requested, skip others
            if args.project and project_name != args.project:
                continue
            
            projects_found += 1
            if clone_repository(project_name, git_url, branch, commit_id):
                successful_clones += 1
            else:
                failed_clones += 1

    if args.project and projects_found == 0:
        print(f"❌ Project '{args.project}' not found in CSV.")
        sys.exit(1)

    if failed_clones > 0:
        sys.exit(1)

    if not args.project:
        print(f"\n✨ All {successful_clones} repositories cloned successfully!")

if __name__ == "__main__":
    main()
