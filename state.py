"""
state.py — Persistent state management for seen jobs.

Stores job metadata in data/seen_jobs.json with a 7-day TTL.
Thread-safe enough for a single-process workflow runner.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

STATE_FILE = Path("data/seen_jobs.json")
TTL_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_raw() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(STATE_FILE)


def load_state() -> dict:
    """Load state and purge expired entries (older than TTL_DAYS)."""
    raw = _load_raw()
    cutoff = _now() - timedelta(days=TTL_DAYS)
    pruned = {
        job_id: entry
        for job_id, entry in raw.items()
        if _parse_dt(entry.get("first_seen")) > cutoff
    }
    if len(pruned) < len(raw):
        removed = len(raw) - len(pruned)
        print(f"[state] Pruned {removed} expired job(s) from state (>{TTL_DAYS} days old).")
        _save_raw(pruned)
    return pruned


def save_state(state: dict) -> None:
    """Persist state to disk."""
    _save_raw(state)


def _parse_dt(value: Optional[str]) -> datetime:
    """Parse ISO datetime string; return epoch if invalid (so it gets pruned)."""
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def is_seen(state: dict, job_id: str) -> bool:
    return str(job_id) in state


def was_alerted(state: dict, job_id: str) -> bool:
    """Returns True if this job was already scored and alerted — never repeat."""
    return state.get(str(job_id), {}).get("alerted", False)


def get_updated_at(state: dict, job_id: str) -> Optional[str]:
    entry = state.get(str(job_id), {})
    return entry.get("updated_at")


def record_job(state: dict, job: dict) -> None:
    """Insert or update a job entry in state."""
    job_id = str(job["id"])
    existing = state.get(job_id, {})
    state[job_id] = {
        "first_seen": existing.get("first_seen") or _now().isoformat(),
        "updated_at": job.get("updated_at", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "alerted": existing.get("alerted", False),  # preserve existing flag
    }


def mark_alerted(state: dict, job_id: str) -> None:
    """Mark a job as scored — prevents re-scoring even if updated_at changes."""
    job_id = str(job_id)
    if job_id in state:
        state[job_id]["alerted"] = True