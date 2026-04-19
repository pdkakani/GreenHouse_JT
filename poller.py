"""
poller.py — Main Greenhouse job polling script.

Key changes:
- write_output() is called BEFORE scoring — jobs always land in jobs.md
- Scoring is fully decoupled and best-effort: if it completes within the
  remaining budget, scores are patched into jobs.md; if time runs out, jobs
  are already written without scores (no data loss)
- Rate-limit sleep moved to AFTER each score call so pacing is consistent
- WORKFLOW_TIMEOUT_SECONDS guards the scoring loop so the job never exceeds
  the GitHub Actions 10-min limit
"""

import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from filters import is_usa_location, is_software_role
from state import load_state, save_state, is_seen, get_updated_at, record_job, was_alerted, mark_alerted
from scorer import score_job, should_alert
from notifier import send_slack_alert, send_run_summary, send_new_jobs_digest

COMPANIES_FILE = Path("companies.txt")
OUTPUT_FILE = Path("output/jobs.md")
API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3
MAX_JOBS_PER_COMPANY = 500

# Leave 90s buffer before the 10-min GitHub Actions hard kill
WORKFLOW_TIMEOUT_SECONDS = 8 * 60  # 8 minutes for fetch+filter+write; scoring gets the rest
SCORE_RATE_LIMIT_SLEEP = 2.1       # slightly over 2s to stay safely under 30 req/min


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


def write_output(new_jobs: list[dict], updated_jobs: list[dict], run_label: str) -> None:
    """Write jobs to output file. Can be called multiple times; run_label must be stable."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    header = f"\n## 📅 Run: {run_label}\n\n"
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


def patch_scores_in_output(scored_jobs: list[dict], run_label: str) -> None:
    """
    After scoring completes, re-write the output file so scored jobs show
    their 🎯 score. This is a no-op if OUTPUT_FILE doesn't exist yet.
    We simply regenerate from the in-memory lists which already have _score set.
    """
    # Intentionally a no-op here — write_output is called again at the end
    # of main() with the same lists (which now have _score populated).
    pass


def main():
    wall_start = time.monotonic()
    start = datetime.now(timezone.utc)
    run_label = start.strftime("%Y-%m-%d %H:%M UTC")

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
        "scores_completed": 0,
        "scores_timed_out": 0,
    }

    new_jobs = []
    updated_jobs = []

    # ── Phase 1: Fetch & Filter ───────────────────────────────────────────────
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
                # updated_at changed — record update
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
                    "_score": None,  # updated jobs are not re-scored
                })
                stats["jobs_updated"] += 1
                stats["jobs_skipped_seen"] += 1
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
                "_score": None,  # will be filled in Phase 3 if time allows
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

    # ── Phase 2: Write output IMMEDIATELY (no scoring dependency) ─────────────
    if new_jobs or updated_jobs:
        write_output(new_jobs, updated_jobs, run_label)
        print(f"\n[output] ✅ Written {stats['jobs_new']} new + {stats['jobs_updated']} updated jobs to {OUTPUT_FILE}")
    else:
        print("\n[output] No new or updated jobs — skipping file write.")

    # ── Phase 3: Score new jobs — best-effort, time-boxed ────────────────────
    jobs_to_score = [
        job for job in new_jobs
        if not was_alerted(state, str(job.get("id", "")))
    ]

    if jobs_to_score:
        elapsed_so_far = time.monotonic() - wall_start
        time_budget = WORKFLOW_TIMEOUT_SECONDS - elapsed_so_far
        print(f"\n[scorer] {len(jobs_to_score)} job(s) to score | time budget: {time_budget:.0f}s")

        if time_budget <= 30:
            print("[scorer] ⚠️  Less than 30s remaining — skipping scoring entirely to protect output commit.")
        else:
            any_scored = False
            for job in jobs_to_score:
                elapsed_now = time.monotonic() - wall_start
                remaining = WORKFLOW_TIMEOUT_SECONDS - elapsed_now

                if remaining < 20:
                    print(f"\n[scorer] ⏱️  Time budget exhausted ({stats['scores_timed_out']} job(s) skipped). Stopping.")
                    stats["scores_timed_out"] += len(jobs_to_score) - stats["scores_completed"] - stats["scores_timed_out"]
                    break

                job_id = str(job.get("id", ""))
                job_label = f"{job.get('title', '?')} @ {job.get('company', '?')}"
                print(f"  → {job_label} ...", end=" ", flush=True)

                score = score_job(job)  # returns int or None

                # Rate-limit sleep AFTER the call (not before)
                time.sleep(SCORE_RATE_LIMIT_SLEEP)

                if score is None:
                    print("scoring failed — job already written without score.")
                    stats["alerts_skipped"] += 1
                    stats["scores_timed_out"] += 1
                    continue

                print(f"score={score}%")
                job["_score"] = score
                stats["scores_completed"] += 1
                any_scored = True

                mark_alerted(state, job_id)

                if should_alert(score):
                    sent = send_slack_alert(job, score)
                    if sent:
                        stats["alerts_sent"] += 1
                    else:
                        stats["alerts_skipped"] += 1
                else:
                    print(f"    [scorer] Below threshold ({score}% < 65%) — no alert.")
                    stats["alerts_skipped"] += 1

            save_state(state)

            # Re-write output with scores patched in (only if at least one scored)
            if any_scored:
                write_output(new_jobs, updated_jobs, run_label)
                print(f"\n[output] ✅ Re-written with {stats['scores_completed']} score(s) patched in.")

    if stats["alerts_sent"] > 0:
        send_run_summary(stats)

    if new_jobs:
        send_new_jobs_digest(new_jobs)

    elapsed = time.monotonic() - wall_start
    stats["elapsed"] = int(elapsed)

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Companies checked  : {stats['companies_checked']} / {len(companies)}")
    print(f"  Companies failed   : {stats['companies_failed']}")
    print(f"  Jobs fetched       : {stats['jobs_fetched']}")
    print(f"  Skipped (seen)     : {stats['jobs_skipped_seen']}")
    print(f"  Skipped (location) : {stats['jobs_skipped_location']}")
    print(f"  Skipped (title)    : {stats['jobs_skipped_title']}")
    print(f"  New jobs written   : {stats['jobs_new']} 🆕")
    print(f"  Updated jobs       : {stats['jobs_updated']} 🔄")
    print(f"  Scores completed   : {stats['scores_completed']}")
    print(f"  Scores timed out   : {stats['scores_timed_out']}")
    print(f"  Slack alerts sent  : {stats['alerts_sent']} 🔔")
    print(f"  Below threshold    : {stats['alerts_skipped']}")
    print(f"  Elapsed            : {elapsed:.1f}s")
    print(f"{'='*60}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()