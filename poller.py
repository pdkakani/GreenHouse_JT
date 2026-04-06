"""
poller.py — Main Greenhouse job polling script.

Fetches jobs from Greenhouse public board APIs for all companies
listed in companies.txt, filters by USA location and software/IT
title, deduplicates against seen state, and writes results to
output/jobs.md.

Usage:
    python poller.py
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filters import is_usa_location, is_software_role
from state import load_state, save_state, is_seen, get_updated_at, record_job
from scorer import score_job, should_alert
from notifier import send_slack_alert, send_run_summary

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COMPANIES_FILE = Path("companies.txt")
OUTPUT_FILE = Path("output/jobs.md")
API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
REQUEST_TIMEOUT = 15        # seconds per API call
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3             # seconds between retries
MAX_JOBS_PER_COMPANY = 500  # safety cap — Greenhouse rarely exceeds this


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_companies() -> list[str]:
    """Read companies.txt, skip blank lines and comments."""
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
    """Fetch all jobs for a Greenhouse board token. Returns None on failure."""
    url = API_BASE.format(board=board)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("jobs", [])
            elif resp.status_code == 404:
                # Board doesn't exist or company uses different ATS
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
    """Extract location string from a Greenhouse job object."""
    # Greenhouse sometimes nests location under offices or has a top-level location
    loc = job.get("location", {})
    if isinstance(loc, dict):
        return loc.get("name", "")
    if isinstance(loc, str):
        return loc
    # Fallback: check offices
    offices = job.get("offices", [])
    if offices:
        return offices[0].get("name", "")
    return ""


def extract_department(job: dict) -> str:
    """Extract primary department name from a Greenhouse job object."""
    depts = job.get("departments", [])
    if depts:
        return depts[0].get("name", "")
    return ""


def extract_job_url(job: dict) -> str:
    """Build the canonical job apply URL."""
    # Greenhouse absolute_url is the reliable field
    return job.get("absolute_url", "")


def format_job_entry(job: dict, tag: str = "NEW") -> str:
    """Format a single job as a Markdown entry."""
    icon = "🆕" if tag == "NEW" else "🔄"
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "")
    url = job.get("_url", "")
    job_id = job.get("id", "")
    updated_at = job.get("updated_at", "")
    department = job.get("_department", "")

    loc_display = f"📍 {location}" if location else "📍 Remote / Unspecified"
    dept_display = f" · {department}" if department else ""

    return (
        f"### {icon} {title}\n"
        f"**{company}**{dept_display}\n"
        f"{loc_display} &nbsp;|&nbsp; 🔗 [Apply Here]({url})\n"
        f"🕐 Updated: `{updated_at}` &nbsp;|&nbsp; ID: `{job_id}`\n"
        f"\n---\n"
    )


def write_output(new_jobs: list[dict], updated_jobs: list[dict]) -> None:
    """Append new/updated jobs to output/jobs.md, newest first."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_entries = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if new_jobs or updated_jobs:
        header = f"\n## 📅 Run: {now_str}\n\n"
        entries = ""
        for job in new_jobs:
            entries += format_job_entry(job, tag="NEW")
        for job in updated_jobs:
            entries += format_job_entry(job, tag="UPDATED")
        all_entries.append(header + entries)

    if not all_entries:
        return  # nothing to write

    # Prepend to file (newest first)
    existing = ""
    if OUTPUT_FILE.exists():
        existing = OUTPUT_FILE.read_text(encoding="utf-8")

    page_header = (
        "# 🌿 Greenhouse Job Tracker\n"
        "_Filtered: USA/Remote · Software & IT roles only_\n\n"
    )

    # Strip old page header if present
    if existing.startswith("# 🌿"):
        existing = existing[existing.index("\n## "):] if "\n## " in existing else ""

    OUTPUT_FILE.write_text(
        page_header + "".join(all_entries) + existing,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"Greenhouse Job Poller — {start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")

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

            # --- Deduplication check ---
            if is_seen(state, job_id):
                prev_updated = get_updated_at(state, job_id)
                if prev_updated == updated_at:
                    stats["jobs_skipped_seen"] += 1
                    continue  # identical, skip entirely
                # updated_at changed → re-process as "updated"
                tag = "UPDATED"
            else:
                tag = "NEW"

            # --- Location filter ---
            location = extract_location(job)
            if not is_usa_location(location):
                stats["jobs_skipped_location"] += 1
                continue

            # --- Title / department filter ---
            title = job.get("title", "")
            department = extract_department(job)
            if not is_software_role(title, department):
                stats["jobs_skipped_title"] += 1
                continue

            # --- Passed all filters ---
            enriched = {
                **job,
                "company": board,
                "_location": location,
                "_department": department,
                "_url": extract_job_url(job),
                "_tag": tag,
            }

            # Try to get nicer company name from metadata
            # (Greenhouse embeds it in some responses)
            enriched["company"] = job.get("company", board)

            record_job(state, {
                "id": job_id,
                "updated_at": updated_at,
                "title": title,
                "company": enriched["company"],
            })

            if tag == "NEW":
                new_jobs.append(enriched)
                stats["jobs_new"] += 1
            else:
                updated_jobs.append(enriched)
                stats["jobs_updated"] += 1

    # Persist updated state
    save_state(state)

    # --- Score & alert for new/updated jobs ---
    all_fresh_jobs = new_jobs + updated_jobs
    if all_fresh_jobs:
        print(f"\n[scorer] Scoring {len(all_fresh_jobs)} new/updated job(s) against resume...")
        for job in all_fresh_jobs:
            job_label = f"{job.get('title', '?')} @ {job.get('company', '?')}"
            print(f"  → {job_label} ...", end=" ", flush=True)
            score_result = score_job(job)
            if score_result is None:
                print("scoring failed, skipping.")
                stats["alerts_skipped"] += 1
                continue
            score = score_result["score"]
            print(f"score={score}%")
            if should_alert(score):
                sent = send_slack_alert(job, score_result)
                if sent:
                    stats["alerts_sent"] += 1
                else:
                    stats["alerts_skipped"] += 1
            else:
                print(f"  [scorer] Below threshold ({score}% < 65%) — no alert.")
                stats["alerts_skipped"] += 1

        if stats["alerts_sent"] > 0:
            send_run_summary(stats)

    # Write output only if there's something new
    if new_jobs or updated_jobs:
        write_output(new_jobs, updated_jobs)
        print(f"\n[output] Written to {OUTPUT_FILE}")
    else:
        print("\n[output] No new or updated jobs — skipping file write.")

    # Summary
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
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

    # Exit code: 0 always (don't fail workflow on 0 new jobs)
    sys.exit(0)


if __name__ == "__main__":
    main()
