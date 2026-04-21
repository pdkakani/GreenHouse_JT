"""
notifier.py — Slack webhook alerter for high-match jobs.

send_slack_alert     : individual alert for a single high-scoring job (>= 65%)
send_new_jobs_digest : one plain-text summary per run for ALL new jobs
send_run_summary     : end-of-run stats summary
"""

import os
import json
import requests

SLACK_TIMEOUT = 10


def _get_webhook_url():
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    return url if url else None


def _score_emoji(score: int) -> str:
    if score >= 85:
        return "🔥"
    elif score >= 70:
        return "🎯"
    elif score >= 65:
        return "✅"
    else:
        return "👀"


def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def _format_digest_job(job: dict) -> str:
    """Render one job in a compact, readable two-line Slack format."""
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "")
    url = job.get("_url", "")
    score = job.get("_score")

    meta_bits = [company]
    if location:
        meta_bits.append(location)
    if score is not None:
        meta_bits.append(f"{score}%")

    meta_line = " · ".join(meta_bits)
    apply_link = f" | <{url}|Apply>" if url else ""
    return f"• *{title}*\n  {meta_line}{apply_link}"


def _format_digest_summary(stats: dict) -> str:
    """Keep the run summary compact so it reads like a footer, not a second post."""
    return (
        f"Run summary: {stats.get('companies_checked', 0)}/"
        f"{stats.get('companies_checked', 0) + stats.get('companies_failed', 0)} companies, "
        f"{stats.get('jobs_fetched', 0)} fetched, "
        f"{stats.get('jobs_new', 0)} new, "
        f"{stats.get('jobs_updated', 0)} updated, "
        f"{stats.get('jobs_skipped_location', 0)} loc filtered, "
        f"{stats.get('jobs_skipped_title', 0)} title filtered, "
        f"{stats.get('elapsed', 0)}s"
    )


def _format_alert_text(job: dict, score: int) -> str:
    """Build the compact plain-text fallback for the single-job alert."""
    title = job.get("title", "Unknown Title")
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "Remote / Unspecified")
    department = job.get("_department", "")
    updated_at = job.get("updated_at", "")

    dept_text = f" · {department}" if department else ""
    return (
        f"{_score_emoji(score)} {score}% Match — {title}\n"
        f"{company}{dept_text} · {location}\n"
        f"Updated: {updated_at}"
    )


def _format_alert_meta(job: dict, score: int) -> str:
    """Render the compact metadata line used in the Slack blocks."""
    company = job.get("company", "Unknown Company")
    location = job.get("_location", "Remote / Unspecified")
    department = job.get("_department", "")
    dept_text = f" · {department}" if department else ""
    bar = _score_bar(score)
    return f"*{company}{dept_text}* · {location} · `{bar}` {score}/100"


def send_slack_alert(job: dict, score: int) -> bool:
    """Send a Slack alert for a single high-match job (score >= 65)."""
    webhook_url = _get_webhook_url()
    if not webhook_url:
        print("  [slack] SLACK_WEBHOOK_URL not set — skipping alert.")
        return False

    title = job.get("title", "Unknown Title")
    url = job.get("_url", "")
    job_id = job.get("id", "")
    updated_at = job.get("updated_at", "")

    emoji = _score_emoji(score)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {score}% Match — {title}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": _format_alert_meta(job, score)},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔗 Apply Now", "emoji": True},
                    "url": url,
                    "style": "primary",
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Job ID: `{job_id}` · Updated: `{updated_at}`"}
            ],
        },
        {"type": "divider"},
    ]

    payload = {
        "text": _format_alert_text(job, score),
        "blocks": blocks,
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] ✅ Alert sent: {title} ({score}%)")
            return True
        else:
            print(f"  [slack] ❌ Failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] ❌ Request error: {e}")
        return False


def send_new_jobs_digest(new_jobs: list[dict], stats: dict) -> bool:
    """
    Send a single Slack message with:
      - top 20 newest jobs found this run (overflow count noted)
      - a compact run summary footer

    All jobs are already persisted in seen_jobs.json before this is called,
    so the digest is purely informational — nothing is lost on overflow.
    """
    webhook_url = _get_webhook_url()
    if not webhook_url:
        print("  [slack] SLACK_WEBHOOK_URL not set — skipping digest.")
        return False
    if not new_jobs:
        return False

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(new_jobs)
    MAX_IN_DIGEST = 20

    # Sort newest-first so overflow always drops the oldest, not the newest.
    sorted_jobs = sorted(new_jobs, key=lambda j: j.get("updated_at", ""), reverse=True)
    shown = sorted_jobs[:MAX_IN_DIGEST]
    overflow = total - len(shown)

    lines = [_format_digest_job(job) for job in shown]

    if overflow > 0:
        lines.append(f"\n_…and {overflow} more recorded in seen_jobs.json_")

    header = f"*{total} New Job{'s' if total != 1 else ''} Found* — {timestamp}"
    text = header + "\n\n" + "\n\n".join(lines) + "\n\n" + _format_digest_summary(stats)

    payload = {
        "text": text,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"{total} New Job{'s' if total != 1 else ''} Found", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n\n".join(lines)}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _format_digest_summary(stats)}]},
        ],
    }

    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=SLACK_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"  [slack] ✅ Digest sent ({len(shown)} shown, {overflow} overflow)")
            return True
        else:
            print(f"  [slack] ❌ Failed ({resp.status_code}): {resp.text[:100]}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"  [slack] ❌ Request error: {e}")
        return False
