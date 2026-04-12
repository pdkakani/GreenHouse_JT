"""
poller.py — Main Greenhouse job polling script.
"""

import sys
import os
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filters import is_usa_location, is_software_role
from state import load_state, save_state, is_seen, get_updated_at, record_job, was_alerted, mark_alerted
from scorer import score_job, should_alert
from notifier import send_slack_alert, send_run_summary

COMPANIES_FILE = Path("companies.txt")
OUTPUT_FILE = Path("output/jobs.md")
API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3
MAX_JOBS_PER_COMPANY = 500


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


def format_job_entry(job: dict, tag: str = "NEW") -> str:
    icon = "🆕" if tag == "NEW" else "🔄"
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "")
    url = job.get("_url", "")
    job_id = job.get("id", "")
    updated_at = job.get("updated_at", "")
    department = job.get("_department", "")
    score = job.get("_score")

    loc_display = f"📍 {location}" if location else "📍 Remote / Unspecified"
    dept_display = f" · {department}" if department else ""
    score_display = f" · 🎯 {score}%" if score is not None else ""

    return (
        f"### {icon} {title}\n"
        f"**{company}**{dept_display}{score_display}\n"
        f"{loc_display} &nbsp;|&nbsp; 🔗 [Apply Here]({url})\n"
        f"🕐 Updated: `{updated_at}` &nbsp;|&nbsp; ID: `{job_id}`\n"
        f"\n---\n"
    )


def write_output(new_jobs: list[dict], updated_jobs: list[dict]) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"\n## 📅 Run: {now_str}\n\n"
    entries = ""
    for job in new_jobs:
        entries += format_job_entry(job, tag="NEW")
    for job in updated_jobs:
        entries += format_job_entry(job, tag="UPDATED")

    if not entries:
        return

    existing = ""
    if OUTPUT_FILE.exists():
        existing = OUTPUT_FILE.read_text(encoding="utf-8")

    page_header = (
        "# 🌿 Greenhouse Job Tracker\n"
        "_Filtered: USA/Remote · Software & IT roles only_\n\n"
    )

    if existing.startswith("# 🌿"):
        existing = existing[existing.index("\n## "):] if "\n## " in existing else ""

    OUTPUT_FILE.write_text(page_header + header + entries + existing, encoding="utf-8")



def _validate_env() -> None:
    """Fail fast if required environment variables are missing."""
    if not os.environ.get("CL_API_KEY", "").strip():
        print("[ERROR] Missing required env var: CL_API_KEY (Anthropic API key for resume scoring)")
        sys.exit(1)
    if not os.environ.get("SLACK_WEBHOOK_URL", "").strip():
        print("[warn] SLACK_WEBHOOK_URL not set — Slack alerts will be skipped.")

def main():
    start = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"Greenhouse Job Poller — {start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")

    _validate_env()

    companies = load_companies()
    print(f"[info] Loaded {len(companies)} companies from {COMPANIES_FILE}")

    state = load_state()
    print(f"[info] State loaded: {len(state)} previously seen job(s)")

    stats = {
        "companies_checked": 0,
        "companies_failed": 0,
        "jobs_fetched": 0,
        "jobs_skipped_seen": 0,
        "jobs_skipped_location": 0,
        "jobs_skipped_title": 0,
        "jobs_new": 0,
        "jobs_updated": 0,
        "alerts_sent": 0,
        "alerts_skipped": 0,
    }

    new_jobs = []
    updated_jobs = []

    for board in companies:
        print(f"\n[fetch] {board} ...", end=" ", flush=True)
        raw_jobs = fetch_jobs(board)

        if raw_jobs is None:
            stats["companies_failed"] += 1
            continue

        stats["companies_checked"] += 1
        stats["jobs_fetched"] += len(raw_jobs)
        print(f"{len(raw_jobs)} jobs")

        for job in raw_jobs[:MAX_JOBS_PER_COMPANY]:
            job_id = str(job.get("id", ""))
            updated_at = job.get("updated_at", "")

            if is_seen(state, job_id):
                prev_updated = get_updated_at(state, job_id)
                if prev_updated == updated_at:
                    stats["jobs_skipped_seen"] += 1
                    continue
                # updated_at changed — update state, add to output, but never re-score
                record_job(state, {
                    "id": job_id,
                    "updated_at": updated_at,
                    "title": job.get("title", ""),
                    "company": job.get("company", board),
                })
                updated_jobs.append({
                    **job,
                    "company": job.get("company", board),
                    "_location": extract_location(job),
                    "_department": extract_department(job),
                    "_url": extract_job_url(job),
                    "_tag": "UPDATED",
                })
                stats["jobs_updated"] += 1
                continue

            # Brand new job — run through filters
            location = extract_location(job)
            if not is_usa_location(location):
                stats["jobs_skipped_location"] += 1
                continue

            title = job.get("title", "")
            department = extract_department(job)
            if not is_software_role(title, department):
                stats["jobs_skipped_title"] += 1
                continue

            enriched = {
                **job,
                "company": job.get("company", board),
                "_location": location,
                "_department": department,
                "_url": extract_job_url(job),
                "_tag": "NEW",
            }

            record_job(state, {
                "id": job_id,
                "updated_at": updated_at,
                "title": title,
                "company": enriched["company"],
            })

            new_jobs.append(enriched)
            stats["jobs_new"] += 1

    save_state(state)

    # --- Score & alert for NEW jobs only ---
    # Skip any job already alerted (safety net against duplicates)
    jobs_to_score = [
        job for job in new_jobs
        if not was_alerted(state, str(job.get("id", "")))
    ]

    if jobs_to_score:
        print(f"\n[scorer] Scoring {len(jobs_to_score)} new job(s) against resume...")
        for job in jobs_to_score:
            job_id = str(job.get("id", ""))
            job_label = f"{job.get('title', '?')} @ {job.get('company', '?')}"
            print(f"  → {job_label} ...", end=" ", flush=True)

            score = score_job(job)  # returns int or None
            if score is None:
                print("scoring failed — marking to avoid infinite retry.")
                mark_alerted(state, job_id)   # Bug fix: prevent re-scoring every run
                stats["alerts_skipped"] += 1
                continue

            print(f"score={score}%")
            job["_score"] = score  # attach score to job for output/md display

            # Mark as scored — never score this job again regardless of outcome
            mark_alerted(state, job_id)

            if should_alert(score):
                sent = send_slack_alert(job, score)
                if sent:
                    stats["alerts_sent"] += 1
                else:
                    stats["alerts_skipped"] += 1
            else:
                print(f"  [scorer] Below threshold ({score}% < 65%) — no alert.")
                stats["alerts_skipped"] += 1

        # Save state again to persist alerted flags
        save_state(state)

        # Send summary when any new jobs were found, not just when alerts fired
        if stats["jobs_new"] > 0 or stats["alerts_sent"] > 0:
            send_run_summary(stats)

    if new_jobs or updated_jobs:
        write_output(new_jobs, updated_jobs)
        print(f"\n[output] Written to {OUTPUT_FILE}")
    else:
        print("\n[output] No new or updated jobs — skipping file write.")

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    stats["elapsed"] = int(elapsed)
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Companies checked : {stats['companies_checked']} / {len(companies)}")
    print(f"  Companies failed  : {stats['companies_failed']}")
    print(f"  Jobs fetched      : {stats['jobs_fetched']}")
    print(f"  Skipped (seen)    : {stats['jobs_skipped_seen']}")
    print(f"  Skipped (location): {stats['jobs_skipped_location']}")
    print(f"  Skipped (title)   : {stats['jobs_skipped_title']}")
    print(f"  New jobs found    : {stats['jobs_new']} 🆕")
    print(f"  Updated jobs      : {stats['jobs_updated']} 🔄")
    print(f"  Slack alerts sent : {stats['alerts_sent']} 🔔")
    print(f"  Below threshold   : {stats['alerts_skipped']}")
    print(f"  Elapsed           : {elapsed:.1f}s")
    print(f"{'='*60}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()