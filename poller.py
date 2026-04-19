"""
poller.py — Main Greenhouse job polling script.

Scoring queue design:
- New jobs passing filters are written to jobs.md immediately (no score yet)
  and added to data/pending_queue.json
- State + queue are committed to git MID-RUN before scoring starts,
  so a workflow timeout during scoring never loses seen-job state
- Each run scores as many queued jobs as the time budget allows (~2.1s/job)
- Scored jobs have their score patched into jobs.md in-place (by job ID anchor)
- Queue entries older than 2 days are dropped automatically
- Individual Slack alerts fire for scores >= 65
- One digest Slack message is sent per run for ALL new jobs (scored or not)
"""

import re
import subprocess
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from filters import is_usa_location, is_software_role
from state import (
    load_state, save_state, is_seen, get_updated_at, record_job,
    was_alerted, mark_alerted,
    load_queue, save_queue, enqueue_jobs,
    drop_expired_queue_entries, remove_from_queue,
)
from scorer import score_job, should_alert
from notifier import send_slack_alert, send_run_summary, send_new_jobs_digest

COMPANIES_FILE = Path("companies.txt")
OUTPUT_FILE = Path("output/jobs.md")
API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3
MAX_JOBS_PER_COMPANY = 500

WORKFLOW_TIMEOUT_SECONDS = 8 * 60  # 8 minutes; scoring gets whatever fetch leaves
SCORE_RATE_LIMIT_SLEEP = 2.1       # slightly over 2s → safely under 30 req/min


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
    """Write jobs to output file."""
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


def patch_score_in_output(job_id: str, score: int) -> bool:
    """
    Find the job block by its ID anchor line in jobs.md and patch the score
    into the company/dept line.

    Block structure (fixed by format_job_entry):
        ### 🆕 Title                           ← anchor - 3
        **Company** · Dept · 🎯 score%         ← anchor - 2  (patch here)
        📍 Location | 🔗 Apply Here            ← anchor - 1
        🕐 Updated: `...` | ID: `{job_id}`    ← anchor

    Returns True if patched, False if job not found in file.
    """
    if not OUTPUT_FILE.exists():
        return False

    content = OUTPUT_FILE.read_text(encoding="utf-8")
    anchor = f"ID: `{job_id}`"

    if anchor not in content:
        return False

    lines = content.splitlines(keepends=True)
    anchor_idx = None
    for i, line in enumerate(lines):
        if anchor in line:
            anchor_idx = i
            break

    if anchor_idx is None:
        return False

    company_line_idx = anchor_idx - 2
    if company_line_idx < 0:
        return False

    old_line = lines[company_line_idx]
    # Remove any existing score display before re-adding
    old_line_stripped = re.sub(r" · 🎯 \d+%", "", old_line).rstrip("\n")
    lines[company_line_idx] = f"{old_line_stripped} · 🎯 {score}%\n"

    OUTPUT_FILE.write_text("".join(lines), encoding="utf-8")
    return True


def commit_state() -> None:
    """
    Commit state + queue + output to git mid-run, before scoring starts.
    This ensures seen_jobs and pending_queue are persisted even if the
    workflow is cancelled during the (potentially long) scoring phase.
    Failures are non-fatal — we log and continue.
    """
    print("\n[git] Committing state before scoring...")
    try:
        # Set git identity — required in GitHub Actions environment
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            check=True
        )
        subprocess.run(
            ["git", "add",
             "data/seen_jobs.json",
             "data/pending_queue.json",
             "output/jobs.md"],
            check=True
        )
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            print("[git] Nothing to commit — state unchanged.")
            return
        subprocess.run(
            ["git", "commit", "-m", "chore: state + queue (pre-score)"],
            check=True
        )
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[git] ✅ State committed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[git] ⚠️  Commit failed: {e} — continuing anyway.")


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

    queue = load_queue()
    print(f"[info] Score queue loaded: {len(queue)} pending job(s)")

    stats = {
        "companies_checked": 0,
        "companies_failed": 0,
        "jobs_fetched": 0,
        "jobs_skipped_seen": 0,
        "jobs_skipped_location": 0,
        "jobs_skipped_title": 0,
        "jobs_new": 0,
        "jobs_updated": 0,
        "queue_dropped": 0,
        "scores_completed": 0,
        "scores_timed_out": 0,
        "scores_patched": 0,
        "alerts_sent": 0,
        "alerts_skipped": 0,
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
                    "_score": None,
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
                "_score": None,
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

    # ── Phase 2: Write new/updated jobs immediately ───────────────────────────
    if new_jobs or updated_jobs:
        write_output(new_jobs, updated_jobs, run_label)
        print(f"\n[output] ✅ Written {stats['jobs_new']} new + {stats['jobs_updated']} updated jobs to {OUTPUT_FILE}")
    else:
        print("\n[output] No new or updated jobs — skipping file write.")

    # ── Phase 3: Send new jobs digest (all new jobs, regardless of score) ─────
    if new_jobs:
        send_new_jobs_digest(new_jobs)

    # ── Phase 4: Enqueue new jobs + prune expired entries ────────────────────
    if new_jobs:
        before = len(queue)
        enqueue_jobs(queue, new_jobs)
        added = len(queue) - before
        print(f"[queue] Added {added} job(s) to score queue")

    queue, dropped = drop_expired_queue_entries(queue)
    stats["queue_dropped"] = dropped
    if dropped:
        print(f"[queue] Dropped {dropped} expired entry/entries (> 2 days old)")

    save_queue(queue)
    save_state(state)

    # ── Commit state + queue to git BEFORE scoring ────────────────────────────
    # This guarantees seen_jobs and pending_queue survive even if the workflow
    # is cancelled mid-scoring due to the 10-min GitHub Actions timeout.
    commit_state()

    # ── Phase 5: Score queue — best-effort, time-boxed ───────────────────────
    MAX_FAILURES = 3  # drop a job from queue after this many failed scoring attempts

    # Pre-filter: prioritise titles that are strong matches for the resume
    # (Java/Python backend, distributed systems, fintech, cloud/infra, staff+).
    # Lower-signal titles remain in queue and get scored on a future run.
    HIGH_VALUE_SIGNALS = [
        "backend", "back-end", "platform", "infrastructure", "distributed",
        "java", "python", "spring", "kafka", "microservice",
        "cloud", "aws", "devops", "sre", "site reliability",
        "fintech", "payments", "banking",
        "staff", "principal", "senior software", "senior engineer",
        "data engineer", "data platform", "fullstack", "full stack",
        "software engineer", "software developer", "solutions architect",
        "technical architect", "engineering manager", "tech lead",
        "llm", "ai engineer", "ml engineer", "generative", "genai",
    ]
    import re as _re
    _hv_re = _re.compile("|".join(_re.escape(s) for s in HIGH_VALUE_SIGNALS), _re.IGNORECASE)

    all_pending = [j for j in queue if not was_alerted(state, str(j["id"]))]
    jobs_to_score = [j for j in all_pending if _hv_re.search(j.get("title", ""))]
    deprioritised = len(all_pending) - len(jobs_to_score)
    if deprioritised:
        print(f"[scorer] Deprioritised {deprioritised} lower-signal job(s) — will try next run")

    if jobs_to_score:
        elapsed_so_far = time.monotonic() - wall_start
        time_budget = WORKFLOW_TIMEOUT_SECONDS - elapsed_so_far
        print(f"\n[scorer] {len(jobs_to_score)} job(s) in queue | time budget: {time_budget:.0f}s")

        if time_budget <= 30:
            print("[scorer] ⚠️  Less than 30s remaining — skipping scoring to protect output commit.")
        else:
            for job in jobs_to_score:
                elapsed_now = time.monotonic() - wall_start
                remaining = WORKFLOW_TIMEOUT_SECONDS - elapsed_now

                if remaining < 20:
                    skipped = len(jobs_to_score) - stats["scores_completed"]
                    print(f"\n[scorer] ⏱️  Time budget exhausted ({skipped} job(s) remain in queue for next run).")
                    stats["scores_timed_out"] += skipped
                    break

                job_id = str(job.get("id", ""))
                job_label = f"{job.get('title', '?')} @ {job.get('company', '?')}"
                print(f"  → {job_label} ...", end=" ", flush=True)

                score = score_job(job)

                # Rate-limit sleep AFTER the call
                time.sleep(SCORE_RATE_LIMIT_SLEEP)

                if score is None:
                    print("scoring failed — will retry next run.")
                    stats["scores_timed_out"] += 1
                    continue

                print(f"score={score}%")
                stats["scores_completed"] += 1

                # Patch score into jobs.md in-place
                patched = patch_score_in_output(job_id, score)
                if patched:
                    stats["scores_patched"] += 1
                else:
                    print(f"    [warn] Could not patch score for ID {job_id} — may have rolled off jobs.md")

                # Remove from queue and mark as alerted to prevent re-scoring
                remove_from_queue(queue, job_id)
                mark_alerted(state, job_id)

                if should_alert(score):
                    sent = send_slack_alert(job, score)
                    if sent:
                        stats["alerts_sent"] += 1
                    else:
                        stats["alerts_skipped"] += 1
                else:
                    print(f"    [scorer] Below threshold ({score}% < 65%) — no individual alert.")
                    stats["alerts_skipped"] += 1

            save_queue(queue)
            save_state(state)
            print(f"\n[scorer] ✅ {stats['scores_completed']} scored, {stats['scores_patched']} patched into jobs.md")
    else:
        print("\n[scorer] No jobs pending score this run.")

    if stats["alerts_sent"] > 0:
        send_run_summary(stats)

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
    print(f"  Queue dropped      : {stats['queue_dropped']} (expired)")
    print(f"  Scores completed   : {stats['scores_completed']}")
    print(f"  Scores patched     : {stats['scores_patched']}")
    print(f"  Scores timed out   : {stats['scores_timed_out']} (queued for next run)")
    print(f"  Slack alerts sent  : {stats['alerts_sent']} 🔔")
    print(f"  Below threshold    : {stats['alerts_skipped']}")
    print(f"  Elapsed            : {elapsed:.1f}s")
    print(f"{'='*60}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()