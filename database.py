#!/usr/bin/env python3

"""
Database module for SCA Scanner
SQLite storage for scan history and results
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent.absolute()


def get_db_path(config: dict) -> Path:
    """Get database path from config"""
    db_path = config.get("database", {}).get("path", "data/sca_scanner.db")
    return SCRIPT_DIR / db_path


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize database and create tables if they don't exist"""
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    cursor = conn.cursor()
    
    # Create scans table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            started_at TEXT NOT NULL,
            total_projects INTEGER NOT NULL,
            successful INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            duration_seconds INTEGER NOT NULL
        )
    """)
    
    # Create scan_results table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            project_name TEXT NOT NULL,
            git_url TEXT,
            branch TEXT,
            commit_hash TEXT,
            success INTEGER NOT NULL,
            report_path TEXT,
            duration_seconds INTEGER NOT NULL,
            FOREIGN KEY (scan_id) REFERENCES scans(id)
        )
    """)
    
    conn.commit()
    return conn


def save_scan(conn: sqlite3.Connection, scan_date: str, results: list, duration: int) -> int:
    """Save a scan and its results to the database, returns scan_id"""
    cursor = conn.cursor()
    
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count
    
    # Insert scan record
    cursor.execute("""
        INSERT INTO scans (scan_date, started_at, total_projects, successful, failed, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (scan_date, datetime.now().isoformat(), len(results), success_count, fail_count, duration))
    
    scan_id = cursor.lastrowid
    
    # Insert result records
    for result in results:
        cursor.execute("""
            INSERT INTO scan_results (scan_id, project_name, git_url, branch, commit_hash, success, report_path, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scan_id,
            result.get("project_name", "Unknown"),
            result.get("git_url", ""),
            result.get("branch", ""),
            result.get("commit_hash", ""),
            1 if result.get("success") else 0,
            result.get("report_path", ""),
            result.get("duration", 0)
        ))
    
    conn.commit()
    return scan_id


def get_all_scans(conn: sqlite3.Connection) -> list:
    """Get all scan records"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM scans ORDER BY id DESC
    """)
    return [dict(row) for row in cursor.fetchall()]


def get_scan_by_id(conn: sqlite3.Connection, scan_id: int) -> Optional[dict]:
    """Get a scan by its ID"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_scan_results(conn: sqlite3.Connection, scan_id: int) -> list:
    """Get all results for a specific scan"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM scan_results WHERE scan_id = ? ORDER BY id
    """, (scan_id,))
    return [dict(row) for row in cursor.fetchall()]


def get_recent_scans(conn: sqlite3.Connection, limit: int = 10) -> list:
    """Get recent scans with summary"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM scans ORDER BY id DESC LIMIT ?
    """, (limit,))
    return [dict(row) for row in cursor.fetchall()]


def get_all_projects(conn: sqlite3.Connection) -> list:
    """Get list of all unique projects with their last scan status"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            project_name,
            MAX(scan_id) as last_scan_id,
            COUNT(*) as scan_count
        FROM scan_results
        GROUP BY project_name
        ORDER BY project_name
    """)
    projects = []
    for row in cursor.fetchall():
        project = dict(row)
        # Get status of last scan
        last_result = cursor.execute("""
            SELECT success, scan_id, duration_seconds 
            FROM scan_results 
            WHERE project_name = ? AND scan_id = ?
        """, (project["project_name"], project["last_scan_id"])).fetchone()
        
        if last_result:
            project.update(dict(last_result))
            
            # Get scan date
            scan_date = cursor.execute("""
                SELECT scan_date FROM scans WHERE id = ?
            """, (project["last_scan_id"],)).fetchone()
            if scan_date:
                project["last_scan_date"] = scan_date[0]
                
        projects.append(project)
    return projects


def get_project_history(conn: sqlite3.Connection, project_name: str) -> list:
    """Get scan history for a specific project"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            sr.*,
            s.scan_date,
            s.started_at
        FROM scan_results sr
        JOIN scans s ON sr.scan_id = s.id
        WHERE sr.project_name = ?
        ORDER BY s.id DESC
    """, (project_name,))
    return [dict(row) for row in cursor.fetchall()]
