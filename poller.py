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
OUTPUT_FILE = Path("output/jobs.md")
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


# def format_job_entry(job: dict, tag: str = "NEW") -> str:
#     icon = "🆕" if tag == "NEW" else "🔄"
#     title = job.get("title", "Unknown Title")
#     company = job.get("company", "Unknown Company")
#     location = job.get("_location", "")
#     url = job.get("_url", "")
#     job_id = job.get("id", "")
#     updated_at = job.get("updated_at", "")
#     department = job.get("_department", "")
#     score = job.get("_score")
#
#     loc_display = f"📍 {location}" if location else "📍 Remote / Unspecified"
#     dept_display = f" · {department}" if department else ""
#     score_display = f" · 🎯 {score}%" if score is not None else ""
#
#     return (
#         f"### {icon} {title}\n"
#         f"**{company}**{dept_display}{score_display}\n"
#         f"{loc_display} &nbsp;|&nbsp; 🔗 [Apply Here]({url})\n"
#         f"🕐 Updated: `{updated_at}` &nbsp;|&nbsp; ID: `{job_id}`\n"
#         f"\n---\n"
#     )
#
#
# def write_output(new_jobs: list[dict], updated_jobs: list[dict], run_label: str) -> None:
#     """Write jobs to output file."""
#     OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
#
#     header = f"\n## 📅 Run: {run_label}\n\n"
#     entries = ""
#     for job in new_jobs:
#         entries += format_job_entry(job, tag="NEW")
#     for job in updated_jobs:
#         entries += format_job_entry(job, tag="UPDATED")
#
#     if not entries:
#         return
#
#     existing = ""
#     if OUTPUT_FILE.exists():
#         existing = OUTPUT_FILE.read_text(encoding="utf-8")
#
#     page_header = (
#         "# 🌿 Greenhouse Job Tracker\n"
#         "_Filtered: USA/Remote · Software & IT roles only_\n\n"
#     )
#
#     if existing.startswith("# 🌿"):
#         existing = existing[existing.index("\n## "):] if "\n## " in existing else ""
#
#     OUTPUT_FILE.write_text(page_header + header + entries + existing, encoding="utf-8")
#
#
# def patch_score_in_output(job_id: str, score: int) -> bool:
#     """
#     Find the job block by its ID anchor line in jobs.md and patch the score
#     into the company/dept line.
#
#     Block structure (fixed by format_job_entry):
#         ### 🆕 Title                           ← anchor - 3
#         **Company** · Dept · 🎯 score%         ← anchor - 2  (patch here)
#         📍 Location | 🔗 Apply Here            ← anchor - 1
#         🕐 Updated: `...` | ID: `{job_id}`    ← anchor
#
#     Returns True if patched, False if job not found in file.
#     """
#     if not OUTPUT_FILE.exists():
#         return False
#
#     content = OUTPUT_FILE.read_text(encoding="utf-8")
#     anchor = f"ID: `{job_id}`"
#
#     if anchor not in content:
#         return False
#
#     lines = content.splitlines(keepends=True)
#     anchor_idx = None
#     for i, line in enumerate(lines):
#         if anchor in line:
#             anchor_idx = i
#             break
#
#     if anchor_idx is None:
#         return False
#
#     company_line_idx = anchor_idx - 2
#     if company_line_idx < 0:
#         return False
#
#     old_line = lines[company_line_idx]
#     # Remove any existing score display before re-adding
#     old_line_stripped = re.sub(r" · 🎯 \d+%", "", old_line).rstrip("\n")
#     lines[company_line_idx] = f"{old_line_stripped} · 🎯 {score}%\n"
#
#     OUTPUT_FILE.write_text("".join(lines), encoding="utf-8")
#     return True
#
#
# def commit_state() -> None:
#     """
#     Commit state + queue + output to git mid-run, before scoring starts.
#     This ensures seen_jobs and pending_queue are persisted even if the
#     workflow is cancelled during the (potentially long) scoring phase.
#     Failures are non-fatal — we log and continue.
#     """
#     print("\n[git] Committing state before scoring...")
#     try:
#         subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
#         subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
#         subprocess.run(
#             ["git", "add",
#              "data/seen_jobs.json",
#              "data/pending_queue.json",
#              "output/jobs.md"],
#             check=True
#         )
#         diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
#         if diff.returncode == 0:
#             print("[git] Nothing to commit — state unchanged.")
#             return
#         subprocess.run(
#             ["git", "commit", "-m", "chore: state + queue (pre-score)"],
#             check=True
#         )
#         subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
#         subprocess.run(["git", "push"], check=True)
#         print("[git] ✅ State committed successfully.")
#     except subprocess.CalledProcessError as e:
#         print(f"[git] ⚠️  Commit failed: {e} — continuing anyway.")


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
            job_id = str(job.get("id", ""))
            updated_at = job.get("updated_at", "")

            # ── Already seen this job ─────────────────────────────────────────
            if is_seen(state, job_id):
                prev_updated = get_updated_at(state, job_id)
                if prev_updated != updated_at:
                    # Job was re-posted or details changed — update state so we
                    # track the latest updated_at, but don't re-alert on it.
                    record_job(state, {
                        "id": job_id,
                        "updated_at": updated_at,
                        "title": job.get("title", ""),
                        "company": job.get("company", board),
                    })
                    stats["jobs_updated"] += 1
                else:
                    # Completely unchanged — nothing to do
                    stats["jobs_skipped_seen"] += 1
                continue  # either way, skip further processing

            # ── Brand new job — run through filters ──────────────────────────

            # Filter 1: must be a USA or US-remote location
            location = extract_location(job)
            if not is_usa_location(location):
                stats["jobs_skipped_location"] += 1
                continue

            # Filter 2: title/department must match software role keywords
            title = job.get("title", "")
            department = extract_department(job)
            if not is_software_role(title, department):
                stats["jobs_skipped_title"] += 1
                continue

            # Passed all filters — enrich, record, and queue for digest
            enriched = {
                **job,
                "company": job.get("company", board),
                "_location": location,
                "_department": department,
                "_url": extract_job_url(job),
            }
            new_jobs.append(enriched)
            stats["jobs_new"] += 1

            score = score_job(enriched)
            if score is None:
                stats["jobs_score_failed"] += 1
                print(f"  [scorer] {title} @ {enriched['company']} — score unavailable")
                sleep_between_scores()
                continue

            enriched["_score"] = score
            stats["jobs_scored"] += 1

            record_job(state, {
                "id": job_id,
                "updated_at": updated_at,
                "title": title,
                "company": enriched["company"],
            })
            mark_alerted(state, job_id)
            save_state(state)

            if should_alert(score):
                if send_slack_alert(enriched, score):
                    stats["jobs_alerted"] += 1
                else:
                    stats["jobs_alert_failed"] += 1

            sleep_between_scores()

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
