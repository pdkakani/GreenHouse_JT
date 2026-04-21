"""
output_writer.py — Markdown run log for jobs discovered during a poll.

The file is updated newest-first so the latest run always appears at the top.
Jobs are grouped by ATS system inside each run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ats_sources import ATS_ORDER, ats_label

OUTPUT_FILE = Path("output/jobs.md")


def _format_job(job: dict) -> str:
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "")
    department = job.get("_department", "")
    url = job.get("_url", "")
    updated_at = job.get("updated_at", "")
    job_id = job.get("id", "")
    score = job.get("_score")
    ats = ats_label(job.get("_ats", "greenhouse"))
    status = job.get("_status", "new")
    status_icon = "🆕" if status == "new" else "🔄"

    dept_text = f" · {department}" if department else ""
    score_text = f" · 🎯 {score}%" if score is not None else ""
    location_text = f"📍 {location}" if location else "📍 Unspecified"
    apply_text = f" | 🔗 [Apply Here]({url})" if url else ""

    return (
        f"#### {status_icon} {title}\n"
        f"**{ats}** · {company}{dept_text}{score_text}\n"
        f"{location_text}{apply_text}\n"
        f"🕐 Updated: {updated_at} | ID: {job_id}"
    )


def _format_summary(stats: dict) -> str:
    by_ats = stats.get("by_ats", {})
    lines = ["## ATS Summary"]
    for ats in ATS_ORDER:
        bucket = by_ats.get(ats)
        if not bucket:
            continue
        lines.append(
            f"- **{ats_label(ats)}**: {bucket.get('jobs_new', 0)} new, "
            f"{bucket.get('jobs_fetched', 0)} fetched, "
            f"{bucket.get('jobs_updated', 0)} updated, "
            f"{bucket.get('jobs_skipped_score_cap', 0)} cap-skipped, "
            f"{bucket.get('jobs_alerted', 0)} alerts"
        )

    lines.append("")
    lines.append(
        f"- **Run total**: {stats.get('jobs_new', 0)} new, {stats.get('jobs_fetched', 0)} fetched, "
        f"{stats.get('jobs_updated', 0)} updated, {stats.get('jobs_skipped_score_cap', 0)} cap-skipped"
    )
    return "\n".join(lines)


def write_jobs_markdown(new_jobs: list[dict], stats: dict, run_label: str) -> bool:
    if not new_jobs:
        return False

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for job in sorted(new_jobs, key=lambda j: j.get("updated_at", ""), reverse=True):
        grouped[job.get("_ats", "greenhouse")].append(job)

    sections = [f"## 📅 Run: {run_label}"]
    for ats in ATS_ORDER:
        jobs = grouped.get(ats, [])
        if not jobs:
            continue
        sections.append(f"### {ats_label(ats)}")
        sections.extend(_format_job(job) for job in jobs)

    sections.append(_format_summary(stats))
    sections.append("\n---\n")
    run_block = "\n\n".join(sections).strip() + "\n\n"

    existing = OUTPUT_FILE.read_text(encoding="utf-8") if OUTPUT_FILE.exists() else ""
    OUTPUT_FILE.write_text(run_block + existing, encoding="utf-8")
    print(f"  [output] ✅ Wrote ATS-categorized run log to {OUTPUT_FILE}")
    return True
