#!/usr/bin/env python3

"""
SCA Scanner Dashboard
Flask web application for viewing scan history and reports
"""

import os
from pathlib import Path

from flask import Flask, render_template, send_from_directory, abort

import yaml
import yaml
import json
from database import get_db_path, init_db, get_all_scans, get_scan_by_id, get_scan_results, get_all_projects, get_project_history

# === Configuration ===
SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

app = Flask(__name__, template_folder="templates")


def load_config() -> dict:
    """Load configuration from config.yaml"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def get_db_connection():
    """Get database connection"""
    config = load_config()
    db_path = get_db_path(config)
    return init_db(db_path)


@app.route("/")
def index():
    """Dashboard home - show scan history"""
    conn = get_db_connection()
    scans = get_all_scans(conn)
    conn.close()
    return render_template("index.html", scans=scans)


@app.route("/scan/<int:scan_id>")
def scan_detail(scan_id):
    """Show details for a specific scan"""
    conn = get_db_connection()
    scan = get_scan_by_id(conn, scan_id)
    if not scan:
        conn.close()
        abort(404)
    results = get_scan_results(conn, scan_id)
    conn.close()
    return render_template("scan_detail.html", scan=scan, results=results)


@app.route("/projects")
def projects_list():
    """List all projects history"""
    conn = get_db_connection()
    projects = get_all_projects(conn)
    conn.close()
    return render_template("projects.html", projects=projects)


@app.route("/project/<name>")
def project_detail(name):
    """Show history and trend for a specific project"""
    conn = get_db_connection()
    history = get_project_history(conn, name)
    conn.close()
    
    if not history:
        abort(404)
        
    # Prepare data for Chart.js
    chart_labels = []
    chart_data = []
    
    # Process in chronological order (oldest first)
    for scan in reversed(history):
        scan_date = scan['scan_date']
        chart_labels.append(scan_date)
        # 1 for success, 0 for failure
        chart_data.append(1 if scan['success'] else 0)
        
    chart_json = json.dumps({
        "labels": chart_labels,
        "data": chart_data
    })
    
    return render_template("project_detail.html", project_name=name, history=history, chart_data=chart_json)


@app.route("/reports/<path:filename>")
def serve_report(filename):
    """Serve HTML report files"""
    config = load_config()
    reports_dir = SCRIPT_DIR / config.get("paths", {}).get("reports_dir", "reports")
    return send_from_directory(reports_dir, filename)


if __name__ == "__main__":
    print("🌐 Starting SCA Scanner Dashboard...")
    print("📍 Open http://localhost:5000 in your browser")
    app.run(host="0.0.0.0", port=5000, debug=True)
