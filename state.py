"""
state.py — Job state management.

state.json  : tracks seen jobs (ats:slug:id → {updated_at, title, company, ats, source_slug, alerted})
pending_queue.json : jobs waiting to be scored (separate file to keep state lean)
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_FILE = Path("data/seen_jobs.json")
QUEUE_FILE = Path("data/pending_queue.json")

QUEUE_MAX_AGE_DAYS = 2  # drop unscored jobs older than this


# ── state.json helpers ────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return _normalize_state(raw)
            return {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def resolve_state_key(job_or_key, ats: str = "", source_slug: str = "") -> str:
    if isinstance(job_or_key, dict):
        job_id = str(job_or_key.get("id", ""))
        ats = str(job_or_key.get("_ats") or ats or "").strip()
        source_slug = str(job_or_key.get("_source_slug") or source_slug or job_or_key.get("company", "")).strip()
    else:
        job_id = str(job_or_key)
        ats = str(ats or "").strip()
        source_slug = str(source_slug or "").strip()

    if ats and source_slug:
        return f"{ats}:{source_slug}:{job_id}"
    return job_id


def is_seen(state: dict, job_or_key, ats: str = "", source_slug: str = "") -> bool:
    return resolve_state_key(job_or_key, ats=ats, source_slug=source_slug) in state


def get_updated_at(state: dict, job_or_key, ats: str = "", source_slug: str = "") -> str:
    return state.get(resolve_state_key(job_or_key, ats=ats, source_slug=source_slug), {}).get("updated_at", "")


def record_job(state: dict, job: dict) -> None:
    job_id = str(job["id"])
    key = resolve_state_key(job)
    state[key] = {
        "updated_at": job.get("updated_at", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "ats": job.get("_ats", "greenhouse"),
        "source_slug": job.get("_source_slug", job.get("company", "")),
        "alerted": state.get(key, state.get(job_id, {})).get("alerted", False),
    }


def mark_alerted(state: dict, job_or_key, ats: str = "", source_slug: str = "") -> None:
    key = resolve_state_key(job_or_key, ats=ats, source_slug=source_slug)
    if key in state:
        state[key]["alerted"] = True


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


def purge_alerted_from_queue(queue: list[dict], state: dict) -> int:
    """
    Remove any queue entries already marked alerted in state.
    Handles the case where a job was scored+alerted in a previous run
    but remove_from_queue never persisted (e.g. crash before save_queue).
    Returns count of entries removed.
    """
    before = len(queue)
    queue[:] = [
        j for j in queue
        if not state.get(
            resolve_state_key(j.get("id", ""), ats=j.get("_ats", ""), source_slug=j.get("_source_slug", "")),
            {},
        ).get("alerted", False)
    ]
    return before - len(queue)


def _normalize_state(raw: dict) -> dict:
    """
    Migrate legacy greenhouse-only keys to ATS-aware keys.

    Older records were keyed only by job id. We preserve them by promoting
    them to greenhouse:{company}:{job_id} where possible.
    """
    normalized = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue

        record = {
            "updated_at": value.get("updated_at", ""),
            "title": value.get("title", ""),
            "company": value.get("company", ""),
            "ats": value.get("ats", "greenhouse"),
            "source_slug": value.get("source_slug", value.get("company", "")),
            "alerted": value.get("alerted", False),
        }

        if ":" in str(key):
            normalized[str(key)] = record
            continue

        source_slug = record["source_slug"] or "legacy"
        new_key = resolve_state_key(str(key), ats=record["ats"], source_slug=source_slug)
        normalized[new_key] = record

    return normalized
