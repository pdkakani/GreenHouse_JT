"""
poller.py — Main ATS job polling script.

Flow per run:
  1. Fetch jobs from every enabled ATS source in companies/*.txt
  2. Filter by USA/remote location and software role title
  3. Score brand-new jobs against resume.txt with Gemini 2.5 Flash-Lite
  4. Send Slack digest + high-match alerts
"""

import sys
import time
from datetime import datetime, timezone

from filters import is_usa_location, is_software_role
from ats_sources import load_sources, fetch_jobs, normalize_job, source_prefix, ats_label
from state import load_state, save_state, is_seen, get_updated_at, record_job, mark_alerted
from scorer import score_job, should_alert, sleep_between_scores
from notifier import send_new_jobs_digest, send_slack_alert
from output_writer import write_jobs_markdown

MAX_SCORED_PER_ATS = 3

def _handle_seen_job(state: dict, job: dict, ats: str, stats: dict, updated_jobs: list[dict] | None = None) -> bool:
    """Update state for a previously seen job and return True if it should skip."""
    updated_at = job.get("updated_at", "")

    if not is_seen(state, job):
        return False

    prev_updated = get_updated_at(state, job)
    if prev_updated != updated_at:
        # The posting changed, so we keep the latest timestamp but never score it again.
        record_job(state, job)
        if updated_jobs is not None:
            updated_jobs.append({**job, "_status": "updated"})
        _bump_ats(stats, ats, "jobs_updated")
    else:
        _bump_ats(stats, ats, "jobs_skipped_seen")

    return True


def _score_and_record_job(
    job: dict,
    ats: str,
    state: dict,
    stats: dict,
    new_jobs: list[dict],
) -> None:
    """Score a passing job, persist state, and optionally alert on strong matches."""
    title = job.get("title", "")
    updated_at = job.get("updated_at", "")
    company = job.get("company", "")

    enriched = {**job, "company": company}
    new_jobs.append(enriched)
    _bump_ats(stats, ats, "jobs_new")

    # Score first so we can decide whether to alert before we mark the job alerted.
    score = score_job(enriched)
    if score is None:
        _bump_ats(stats, ats, "jobs_score_failed")
        print(f"  [scorer] {title} @ {company} — score unavailable")
        sleep_between_scores()
        return

    enriched["_score"] = score
    _bump_ats(stats, ats, "jobs_scored")

    # Persist before Slack so a crash cannot cause the same posting to be scored twice.
    record_job(state, enriched)
    mark_alerted(state, enriched)
    save_state(state)

    if should_alert(score):
        if send_slack_alert(enriched, score):
            _bump_ats(stats, ats, "jobs_alerted")
        else:
            _bump_ats(stats, ats, "jobs_alert_failed")

    # Keep a small gap between Gemini calls so a burst of jobs stays under RPM.
    sleep_between_scores()


def _process_new_job(job: dict, ats: str, state: dict, stats: dict, new_jobs: list[dict]) -> None:
    """Apply location/title filters before scoring a brand-new job."""
    location = job.get("_location", "")
    if not is_usa_location(location):
        _bump_ats(stats, ats, "jobs_skipped_location")
        return

    title = job.get("title", "")
    department = job.get("_department", "")
    if not is_software_role(title, department):
        _bump_ats(stats, ats, "jobs_skipped_title")
        return

    _score_and_record_job(job, ats, state, stats, new_jobs)


def _blank_ats_stats() -> dict:
    return {
        "companies_checked": 0,
        "companies_failed": 0,
        "jobs_fetched": 0,
        "jobs_skipped_seen": 0,
        "jobs_updated": 0,
        "jobs_skipped_location": 0,
        "jobs_skipped_title": 0,
        "jobs_skipped_score_cap": 0,
        "jobs_new": 0,
        "jobs_scored": 0,
        "jobs_score_failed": 0,
        "jobs_alerted": 0,
        "jobs_alert_failed": 0,
    }


def _make_stats(sources) -> dict:
    return {
        **_blank_ats_stats(),
        "by_ats": {source.ats: _blank_ats_stats() for source in sources},
        "elapsed": 0,
    }


def _bump(stats: dict, source, key: str, amount: int = 1) -> None:
    stats[key] += amount
    stats["by_ats"][source.ats][key] += amount


def _bump_ats(stats: dict, ats: str, key: str, amount: int = 1) -> None:
    stats[key] += amount
    stats["by_ats"][ats][key] += amount


def _parse_job_timestamp(job: dict) -> datetime:
    raw = job.get("updated_at", "") or job.get("publishedAt", "") or job.get("updatedAt", "")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _print_summary(stats: dict, total_sources: int, run_label: str, elapsed: float) -> None:
    print(f"\n{'='*60}")
    print(f"SUMMARY — {run_label}")
    print(f"  Sources checked      : {stats['companies_checked']} / {total_sources}")
    print(f"  Sources failed       : {stats['companies_failed']}")
    print(f"  Jobs fetched         : {stats['jobs_fetched']}")
    print(f"  ├─ Seen (no change)  : {stats['jobs_skipped_seen']}")
    print(f"  ├─ Updated           : {stats['jobs_updated']} 🔄")
    print(f"  ├─ Skipped (loc)     : {stats['jobs_skipped_location']}")
    print(f"  ├─ Skipped (title)   : {stats['jobs_skipped_title']}")
    print(f"  ├─ Skipped (cap)     : {stats['jobs_skipped_score_cap']}")
    print(f"  └─ New               : {stats['jobs_new']} 🆕")
    print(f"  Scored               : {stats['jobs_scored']}")
    print(f"  Alerts sent          : {stats['jobs_alerted']}")
    print(f"  Score failures       : {stats['jobs_score_failed']}")
    print(f"  Alert failures       : {stats['jobs_alert_failed']}")
    print(f"  Elapsed              : {elapsed:.1f}s")
    print("  By ATS:")
    for ats, bucket in stats["by_ats"].items():
        print(
            f"    - {ats_label(ats)}: "
            f"{bucket['jobs_fetched']} fetched, {bucket['jobs_new']} new, "
            f"{bucket['jobs_skipped_score_cap']} cap-skipped, "
            f"{bucket['jobs_alerted']} alerts, {bucket['jobs_score_failed']} score failures"
        )
    print(f"{'='*60}\n")


def main():
    wall_start = time.monotonic()
    start = datetime.now(timezone.utc)
    run_label = start.strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n{'='*60}")
    print(f"ATS Job Poller — {start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")

    sources = load_sources()
    print(f"[info] Loaded {len(sources)} enabled ATS source(s)")

    # Load previously seen jobs so we can skip or detect updates
    state = load_state()
    print(f"[info] State loaded: {len(state)} previously seen job(s)")

    # Every fetched job must land in exactly one bucket so the summary totals
    # always add up to jobs_fetched.
    stats = _make_stats(sources)

    new_jobs = []  # jobs that passed all filters this run — sent in digest
    updated_jobs = []  # previously seen jobs that changed and are logged only
    new_jobs_by_ats = {ats: [] for ats in stats["by_ats"].keys()}

    # ── Phase 1: Fetch & Filter ───────────────────────────────────────────────
    for source in sources:
        print(f"\n{source_prefix(source)} [fetch] {source.slug} ...", end=" ", flush=True)
        raw_jobs = fetch_jobs(source)
        if raw_jobs is None:
            # fetch_jobs already printed the reason (404, timeout, etc.)
            _bump(stats, source, "companies_failed")
            continue

        _bump(stats, source, "companies_checked")
        _bump(stats, source, "jobs_fetched", len(raw_jobs))
        print(f"{len(raw_jobs)} jobs")

        for job in raw_jobs:
            normalized = normalize_job(source, job)
            if normalized is None:
                continue

            if _handle_seen_job(state, normalized, source.ats, stats, updated_jobs):
                continue  # either way, skip further processing

            new_jobs_by_ats[source.ats].append(normalized)

    for ats, jobs in new_jobs_by_ats.items():
        sorted_jobs = sorted(jobs, key=_parse_job_timestamp, reverse=True)
        scored_jobs = sorted_jobs[:MAX_SCORED_PER_ATS]
        capped_jobs = sorted_jobs[MAX_SCORED_PER_ATS:]

        for job in capped_jobs:
            record_job(state, job)
            _bump_ats(stats, ats, "jobs_skipped_score_cap")

        if capped_jobs:
            save_state(state)

        for job in scored_jobs:
            _process_new_job(job, ats, state, stats, new_jobs)

    save_state(state)

    # ── Phase 2: Send Slack digest for new jobs ───────────────────────────────
    # Digest is capped at 20 inside send_new_jobs_digest; overflow is noted
    # in the message. All jobs are already persisted in seen_jobs.json above.
    output_jobs = new_jobs + updated_jobs
    if output_jobs:
        write_jobs_markdown(output_jobs, stats, run_label)
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
    _print_summary(stats, len(sources), run_label, elapsed)

    sys.exit(0)


if __name__ == "__main__":
    main()
