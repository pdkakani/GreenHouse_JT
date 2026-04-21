"""
poller.py — Main Greenhouse job polling script.

Flow per run:
  1. Fetch jobs from every Greenhouse board in companies.txt
  2. Filter by USA/remote location and software role title
  3. Score brand-new jobs against resume.txt with GPT-5 mini
  4. Send Slack digest + high-match alerts
"""

import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filters import is_usa_location, is_software_role
from state import load_state, save_state, is_seen, get_updated_at, record_job, mark_alerted
from scorer import score_job, should_alert, sleep_between_scores
from notifier import send_new_jobs_digest, send_slack_alert

COMPANIES_FILE = Path("companies.txt")
API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3


def load_companies() -> list[str]:
    if not COMPANIES_FILE.exists():
        print(f"[ERROR] {COMPANIES_FILE} not found.")
        sys.exit(1)
    companies = []
    for line in COMPANIES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            companies.append(line)
    return companies


def fetch_jobs(board: str) -> Optional[list[dict]]:
    url = API_BASE.format(board=board)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("jobs", [])
            elif resp.status_code == 404:
                print(f"  [skip] {board}: 404 — board not found.")
                return None
            else:
                print(f"  [warn] {board}: HTTP {resp.status_code} (attempt {attempt})")
        except requests.exceptions.Timeout:
            print(f"  [warn] {board}: timeout (attempt {attempt})")
        except requests.exceptions.RequestException as e:
            print(f"  [warn] {board}: {e} (attempt {attempt})")
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY)
    return None


def extract_location(job: dict) -> str:
    loc = job.get("location", {})
    if isinstance(loc, dict):
        return loc.get("name", "")
    if isinstance(loc, str):
        return loc
    offices = job.get("offices", [])
    if offices:
        return offices[0].get("name", "")
    return ""


def extract_department(job: dict) -> str:
    depts = job.get("departments", [])
    if depts:
        return depts[0].get("name", "")
    return ""


def extract_job_url(job: dict) -> str:
    return job.get("absolute_url", "")


def _handle_seen_job(state: dict, job: dict, board: str, stats: dict) -> bool:
    """Update state for a previously seen job and return True if it should skip."""
    job_id = str(job.get("id", ""))
    updated_at = job.get("updated_at", "")

    if not is_seen(state, job_id):
        return False

    prev_updated = get_updated_at(state, job_id)
    if prev_updated != updated_at:
        # The posting changed, so we keep the latest timestamp but never score it again.
        record_job(state, {
            "id": job_id,
            "updated_at": updated_at,
            "title": job.get("title", ""),
            "company": job.get("company", board),
        })
        stats["jobs_updated"] += 1
    else:
        stats["jobs_skipped_seen"] += 1

    return True


def _score_and_record_job(
    job: dict,
    board: str,
    state: dict,
    stats: dict,
    new_jobs: list[dict],
    location: str,
    department: str,
) -> None:
    """Score a passing job, persist state, and optionally alert on strong matches."""
    title = job.get("title", "")
    job_id = str(job.get("id", ""))
    updated_at = job.get("updated_at", "")
    company = job.get("company", board)

    enriched = {
        **job,
        "company": company,
        "_location": location,
        "_department": department,
        "_url": extract_job_url(job),
    }
    new_jobs.append(enriched)
    stats["jobs_new"] += 1

    # Score first so we can decide whether to alert before we mark the job alerted.
    score = score_job(enriched)
    if score is None:
        stats["jobs_score_failed"] += 1
        print(f"  [scorer] {title} @ {company} — score unavailable")
        sleep_between_scores()
        return

    enriched["_score"] = score
    stats["jobs_scored"] += 1

    # Persist before Slack so a crash cannot cause the same posting to be scored twice.
    record_job(state, {
        "id": job_id,
        "updated_at": updated_at,
        "title": title,
        "company": company,
    })
    mark_alerted(state, job_id)
    save_state(state)

    if should_alert(score):
        if send_slack_alert(enriched, score):
            stats["jobs_alerted"] += 1
        else:
            stats["jobs_alert_failed"] += 1

    # Keep a small gap between Gemini calls so a burst of jobs stays under RPM.
    sleep_between_scores()


def _process_new_job(job: dict, board: str, state: dict, stats: dict, new_jobs: list[dict]) -> None:
    """Apply location/title filters before scoring a brand-new job."""
    location = extract_location(job)
    if not is_usa_location(location):
        stats["jobs_skipped_location"] += 1
        return

    title = job.get("title", "")
    department = extract_department(job)
    if not is_software_role(title, department):
        stats["jobs_skipped_title"] += 1
        return

    _score_and_record_job(job, board, state, stats, new_jobs, location, department)


def main():
    wall_start = time.monotonic()
    start = datetime.now(timezone.utc)
    run_label = start.strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{'='*60}")
    print(f"Greenhouse Job Poller — {start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")

    companies = load_companies()
    print(f"[info] Loaded {len(companies)} companies from {COMPANIES_FILE}")

    # Load previously seen jobs so we can skip or detect updates
    state = load_state()
    print(f"[info] State loaded: {len(state)} previously seen job(s)")

    # Every fetched job must land in exactly one bucket so the
    # summary totals always add up to jobs_fetched.
    stats = {
        "companies_checked": 0,
        "companies_failed": 0,
        "jobs_fetched": 0,
        "jobs_skipped_seen": 0,   # seen before, updated_at unchanged
        "jobs_updated": 0,         # seen before, but updated_at changed
        "jobs_skipped_location": 0, # new but non-USA location
        "jobs_skipped_title": 0,   # new, USA, but not a software role
        "jobs_new": 0,             # passed all filters — recorded + alerted
        "jobs_scored": 0,
        "jobs_score_failed": 0,
        "jobs_alerted": 0,
        "jobs_alert_failed": 0,
    }

    new_jobs = []  # jobs that passed all filters this run — sent in digest

    # ── Phase 1: Fetch & Filter ───────────────────────────────────────────────
    for board in companies:
        print(f"\n[fetch] {board} ...", end=" ", flush=True)
        raw_jobs = fetch_jobs(board)
        if raw_jobs is None:
            # fetch_jobs already printed the reason (404, timeout, etc.)
            stats["companies_failed"] += 1
            continue

        stats["companies_checked"] += 1
        stats["jobs_fetched"] += len(raw_jobs)
        print(f"{len(raw_jobs)} jobs")

        for job in raw_jobs:
            if _handle_seen_job(state, job, board, stats):
                continue  # either way, skip further processing

            _process_new_job(job, board, state, stats, new_jobs)

    save_state(state)

    # ── Phase 2: Send Slack digest for new jobs ───────────────────────────────
    # Digest is capped at 20 inside send_new_jobs_digest; overflow is noted
    # in the message. All jobs are already persisted in seen_jobs.json above.
    if new_jobs:
        send_new_jobs_digest(new_jobs, stats)
    else:
        print("\n[slack] No new jobs this run — skipping digest.")

    # ── Summary ───────────────────────────────────────────────────────────────
    # jobs_fetched = seen_unchanged + updated + skipped_location + skipped_title + new
    # This must always hold; any mismatch means a bucket is missing a branch.
    # Compute elapsed before digest so the summary line includes it
    elapsed = time.monotonic() - wall_start
    stats["elapsed"] = int(elapsed)
    print(f"\n{'='*60}")
    print(f"SUMMARY — {run_label}")
    print(f"  Companies checked    : {stats['companies_checked']} / {len(companies)}")
    print(f"  Companies failed     : {stats['companies_failed']}")
    print(f"  Jobs fetched         : {stats['jobs_fetched']}")
    print(f"  ├─ Seen (no change)  : {stats['jobs_skipped_seen']}")
    print(f"  ├─ Updated           : {stats['jobs_updated']} 🔄")
    print(f"  ├─ Skipped (loc)     : {stats['jobs_skipped_location']}")
    print(f"  ├─ Skipped (title)   : {stats['jobs_skipped_title']}")
    print(f"  └─ New               : {stats['jobs_new']} 🆕")
    print(f"  Scored               : {stats['jobs_scored']}")
    print(f"  Alerts sent          : {stats['jobs_alerted']}")
    print(f"  Score failures        : {stats['jobs_score_failed']}")
    print(f"  Alert failures       : {stats['jobs_alert_failed']}")
    print(f"  Elapsed              : {elapsed:.1f}s")
    print(f"{'='*60}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
