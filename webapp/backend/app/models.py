# SPDX-License-Identifier: Apache-2.0
"""Pydantic request/response schemas for the scan API."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_URL_RE = re.compile(r"^(https?://|git://|ssh://|git@[\w.\-]+:)", re.IGNORECASE)


class ScanStatus(str, Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    SCANNING = "scanning"
    COMPLETED = "completed"
    FAILED = "failed"


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class ScanCreateRequest(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=200)
    repo_url: str = Field(..., min_length=1, max_length=2000)
    branch: str = Field(default="main", min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, max_length=200)
    assessment_type: Optional[str] = Field(default="Initial", max_length=100)
    commit_id: Optional[str] = Field(default=None, max_length=100)
    git_token: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("project_name", "repo_url", "branch")
    @classmethod
    def _required_no_leading_dash(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        if v.startswith("-"):
            raise ValueError("must not start with '-'")
        return v

    @field_validator("commit_id")
    @classmethod
    def _optional_no_leading_dash(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if v.startswith("-"):
            raise ValueError("must not start with '-'")
        return v

    @field_validator("repo_url")
    @classmethod
    def _valid_scheme(cls, v: str) -> str:
        if not _URL_RE.match(v):
            raise ValueError("must be an http(s), ssh, or git@ URL")
        return v

    @field_validator("author", "assessment_type", "git_token")
    @classmethod
    def _strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None


class ScanRecord(BaseModel):
    scan_id: str
    project_name: str
    repo_url: str
    branch: str
    author: Optional[str] = None
    assessment_type: Optional[str] = None
    commit_id_requested: Optional[str] = None
    commit_resolved: Optional[str] = None
    status: ScanStatus
    error_message: Optional[str] = None
    severity_counts: SeverityCounts = Field(default_factory=SeverityCounts)
    report_available: bool = False
    used_git_token: bool = False
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


class ScanListResponse(BaseModel):
    total: int
    items: list[ScanRecord]


class HealthResponse(BaseModel):
    status: str
    mongo: bool
