"""
state.py — Job state management.

state.json  : tracks seen jobs (id → {updated_at, title, company, alerted})
pending_queue.json : jobs waiting to be scored (separate file to keep state lean)
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

STATE_FILE = Path("data/seen_jobs.json")
QUEUE_FILE = Path("data/pending_queue.json")

QUEUE_MAX_AGE_DAYS = 2  # drop unscored jobs older than this


# ── state.json helpers ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_seen(state: dict, job_id: str) -> bool:
    return job_id in state


def get_updated_at(state: dict, job_id: str) -> str:
    return state.get(job_id, {}).get("updated_at", "")


def record_job(state: dict, job: dict) -> None:
    job_id = str(job["id"])
    state[job_id] = {
        "updated_at": job.get("updated_at", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "alerted": state.get(job_id, {}).get("alerted", False),
    }


def was_alerted(state: dict, job_id: str) -> bool:
    return state.get(job_id, {}).get("alerted", False)


def mark_alerted(state: dict, job_id: str) -> None:
    if job_id in state:
        state[job_id]["alerted"] = True


# ── pending_queue.json helpers ────────────────────────────────────────────────

def load_queue() -> list[dict]:
    """Load the pending score queue, returning a list of job dicts."""
    if QUEUE_FILE.exists():
        try:
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            return data.get("queue", [])
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_queue(queue: list[dict]) -> None:
    QUEUE_FILE.write_text(json.dumps({"queue": queue}, indent=2), encoding="utf-8")


def enqueue_jobs(queue: list[dict], jobs: list[dict]) -> None:
    """
    Add new jobs to the queue. Each entry gets a queued_at timestamp.
    Jobs already in the queue (by id) are not re-added.
    """
    existing_ids = {str(j["id"]) for j in queue}
    now = datetime.now(timezone.utc).isoformat()
    for job in jobs:
        job_id = str(job.get("id", ""))
        if job_id and job_id not in existing_ids:
            queue.append({
                "id": job_id,
                "queued_at": now,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "content": job.get("content", ""),
                "_location": job.get("_location", ""),
                "_department": job.get("_department", ""),
                "_url": job.get("_url", ""),
                "updated_at": job.get("updated_at", ""),
            })
            existing_ids.add(job_id)


def drop_expired_queue_entries(queue: list[dict]) -> tuple[list[dict], int]:
    """
    Remove entries older than QUEUE_MAX_AGE_DAYS.
    Returns (pruned_queue, dropped_count).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=QUEUE_MAX_AGE_DAYS)
    kept = []
    dropped = 0
    for entry in queue:
        queued_at_str = entry.get("queued_at", "")
        try:
            queued_at = datetime.fromisoformat(queued_at_str)
            if queued_at >= cutoff:
                kept.append(entry)
            else:
                dropped += 1
        except (ValueError, TypeError):
            kept.append(entry)  # keep if timestamp unparseable
    return kept, dropped


def remove_from_queue(queue: list[dict], job_id: str) -> None:
    """Remove a single job from the queue by id (in-place)."""
    queue[:] = [j for j in queue if str(j["id"]) != str(job_id)]