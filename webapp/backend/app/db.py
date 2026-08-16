# SPDX-License-Identifier: Apache-2.0
"""MongoDB access layer (pymongo, used from route handlers via asyncio.to_thread)."""

from __future__ import annotations

from typing import Optional

from pymongo import DESCENDING, MongoClient
from pymongo.collection import Collection

from . import config

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        # tz_aware=True: return timezone-aware UTC datetimes on read, so
        # JSON responses serialize with an explicit UTC offset instead of a
        # naive timestamp the frontend would otherwise misread as local time.
        _client = MongoClient(config.MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
    return _client


def get_scans_collection() -> Collection:
    return get_client()[config.MONGO_DB]["scans"]


def ensure_indexes() -> None:
    coll = get_scans_collection()
    coll.create_index("scan_id", unique=True)
    coll.create_index([("created_at", DESCENDING)])
    coll.create_index("project_name")
    coll.create_index("status")
