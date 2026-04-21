"""
ats_sources.py — ATS source configuration and normalized job fetch helpers.

Supported ATS systems:
  - greenhouse
  - lever
  - ashby

Enable/disable systems by editing ENABLED_ATS or setting the environment
variable of the same name to a comma-separated list, e.g.:
  ENABLED_ATS=greenhouse,ashby
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

REQUEST_TIMEOUT = 15
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3

ATS_ORDER = ("greenhouse", "lever", "ashby")
ATS_LABELS = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
}

COMPANIES_DIR = Path("companies")
LEGACY_GREENHOUSE_FILE = Path("companies.txt")
ATS_FILES = {
    "greenhouse": COMPANIES_DIR / "greenhouse.txt",
    "lever": COMPANIES_DIR / "lever.txt",
    "ashby": COMPANIES_DIR / "ashby.txt",
}

ATS_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


@dataclass(frozen=True)
class ATSSource:
    ats: str
    slug: str


def ats_label(ats: str) -> str:
    return ATS_LABELS.get(ats, ats.title())


def ats_enabled() -> list[str]:
    raw = os.environ.get("ENABLED_ATS", "").strip()
    if not raw:
        return list(ATS_ORDER)

    requested = {
        part.strip().lower()
        for part in raw.split(",")
        if part.strip()
    }
    return [ats for ats in ATS_ORDER if ats in requested]


def _read_slugs(path: Path) -> list[str]:
    if not path.exists():
        return []

    slugs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        slug = line.strip()
        if not slug or slug.startswith("#"):
            continue
        slugs.append(slug)
    return slugs


def load_sources() -> list[ATSSource]:
    sources: list[ATSSource] = []
    enabled = ats_enabled()

    for ats in enabled:
        path = ATS_FILES[ats]
        if ats == "greenhouse" and not path.exists() and LEGACY_GREENHOUSE_FILE.exists():
            path = LEGACY_GREENHOUSE_FILE

        for slug in _read_slugs(path):
            sources.append(ATSSource(ats=ats, slug=slug))

    return sources


def source_prefix(source: ATSSource) -> str:
    return f"[{ats_label(source.ats)}]"


def fetch_jobs(source: ATSSource) -> Optional[list[dict]]:
    url = ATS_ENDPOINTS[source.ats].format(slug=source.slug)

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 200:
                return _parse_jobs_response(source.ats, resp)
            if resp.status_code == 404:
                print(f"  {source_prefix(source)} {source.slug}: 404 — board not found.")
                return None

            print(
                f"  {source_prefix(source)} {source.slug}: HTTP {resp.status_code} "
                f"(attempt {attempt})"
            )
        except requests.exceptions.Timeout:
            print(f"  {source_prefix(source)} {source.slug}: timeout (attempt {attempt})")
        except requests.exceptions.RequestException as exc:
            print(f"  {source_prefix(source)} {source.slug}: {exc} (attempt {attempt})")

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_DELAY)

    return None


def _parse_jobs_response(ats: str, resp: requests.Response) -> Optional[list[dict]]:
    try:
        data = resp.json()
    except ValueError:
        print(f"  [{ats_label(ats)}] Unexpected non-JSON response — skipping.")
        return None

    if ats == "greenhouse":
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        return jobs if isinstance(jobs, list) else []

    if ats == "lever":
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            jobs = data.get("jobs")
            if isinstance(jobs, list):
                return jobs
        print(f"  [{ats_label(ats)}] Unexpected Lever payload shape — skipping.")
        return None

    if ats == "ashby":
        if isinstance(data, dict):
            jobs = data.get("jobs", [])
            return jobs if isinstance(jobs, list) else []
        print(f"  [{ats_label(ats)}] Unexpected Ashby payload shape — skipping.")
        return None

    return None


def normalize_job(source: ATSSource, raw_job: dict) -> Optional[dict]:
    if source.ats == "greenhouse":
        return _normalize_greenhouse_job(source, raw_job)
    if source.ats == "lever":
        return _normalize_lever_job(source, raw_job)
    if source.ats == "ashby":
        return _normalize_ashby_job(source, raw_job)
    return None


def _base_job(source: ATSSource, job_id: str, title: str, location: str, department: str, url: str, content: str, updated_at: str) -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": source.slug,
        "_ats": source.ats,
        "_ats_label": ats_label(source.ats),
        "_source_slug": source.slug,
        "_location": location,
        "_department": department,
        "_url": url,
        "content": content,
        "updated_at": updated_at,
    }


def _normalize_greenhouse_job(source: ATSSource, raw_job: dict) -> Optional[dict]:
    job_id = str(raw_job.get("id", "")).strip()
    title = str(raw_job.get("title", "")).strip()
    if not job_id or not title:
        return None

    location = _extract_greenhouse_location(raw_job)
    department = _extract_greenhouse_department(raw_job)
    url = str(raw_job.get("absolute_url", "")).strip()
    content = str(raw_job.get("content", "")).strip()
    updated_at = str(raw_job.get("updated_at", "")).strip()
    return _base_job(source, job_id, title, location, department, url, content, updated_at)


def _extract_greenhouse_location(raw_job: dict) -> str:
    loc = raw_job.get("location", {})
    if isinstance(loc, dict):
        value = loc.get("name", "")
        if value:
            return str(value).strip()
    if isinstance(loc, str) and loc.strip():
        return loc.strip()

    offices = raw_job.get("offices", [])
    if offices and isinstance(offices, list):
        first = offices[0]
        if isinstance(first, dict):
            value = first.get("name", "")
            if value:
                return str(value).strip()
    return ""


def _extract_greenhouse_department(raw_job: dict) -> str:
    depts = raw_job.get("departments", [])
    if depts and isinstance(depts, list):
        first = depts[0]
        if isinstance(first, dict):
            value = first.get("name", "")
            if value:
                return str(value).strip()
    return ""


def _normalize_lever_job(source: ATSSource, raw_job: dict) -> Optional[dict]:
    job_id = str(raw_job.get("id", "")).strip()
    title = str(raw_job.get("text") or raw_job.get("title") or raw_job.get("name") or "").strip()
    url = str(raw_job.get("hostedUrl") or raw_job.get("applyUrl") or raw_job.get("url") or "").strip()
    if not job_id or not title or not url:
        return None

    categories = raw_job.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}

    location = _first_non_empty(
        _coerce_str(categories.get("location")),
        _coerce_str(raw_job.get("location")),
        _coerce_str(raw_job.get("workplaceType")),
    )
    department = _first_non_empty(
        _coerce_str(categories.get("team")),
        _coerce_str(categories.get("department")),
        _coerce_str(raw_job.get("team")),
    )
    content = _first_non_empty(
        _coerce_str(raw_job.get("descriptionPlain")),
        _coerce_str(raw_job.get("description")),
        _coerce_str(raw_job.get("text")),
    )
    updated_at = _first_non_empty(
        _coerce_str(raw_job.get("updatedAt")),
        _coerce_str(raw_job.get("createdAt")),
        _coerce_str(raw_job.get("publishedAt")),
    )
    return _base_job(source, job_id, title, location, department, url, content, updated_at)


def _normalize_ashby_job(source: ATSSource, raw_job: dict) -> Optional[dict]:
    if raw_job.get("isListed") is False:
        return None

    job_id = str(raw_job.get("id", "")).strip()
    title = str(raw_job.get("title", "")).strip()
    url = str(raw_job.get("jobUrl") or raw_job.get("applyUrl") or "").strip()
    if not job_id or not title or not url:
        return None

    location = _format_ashby_location(raw_job)
    department = _first_non_empty(
        _coerce_str(raw_job.get("department")),
        _coerce_str(raw_job.get("team")),
    )
    content = _first_non_empty(
        _coerce_str(raw_job.get("descriptionPlain")),
        _coerce_str(raw_job.get("descriptionHtml")),
        _coerce_str(raw_job.get("description")),
    )
    updated_at = _first_non_empty(
        _coerce_str(raw_job.get("publishedAt")),
        _coerce_str(raw_job.get("updatedAt")),
    )
    return _base_job(source, job_id, title, location, department, url, content, updated_at)


def _format_ashby_location(raw_job: dict) -> str:
    locations: list[str] = []

    location = raw_job.get("location")
    if isinstance(location, str) and location.strip():
        locations.append(location.strip())

    for secondary in raw_job.get("secondaryLocations", []) or []:
        if isinstance(secondary, dict):
            value = secondary.get("location", "")
            if value and str(value).strip():
                locations.append(str(value).strip())

    address = raw_job.get("address", {})
    if isinstance(address, dict):
        postal = address.get("postalAddress", {})
        if isinstance(postal, dict):
            locality = _coerce_str(postal.get("addressLocality"))
            region = _coerce_str(postal.get("addressRegion"))
            country = _coerce_str(postal.get("addressCountry"))
            composed = ", ".join(part for part in [locality, region, country] if part)
            if composed:
                locations.append(composed)

    deduped: list[str] = []
    seen = set()
    for loc in locations:
        if loc not in seen:
            seen.add(loc)
            deduped.append(loc)
    return "; ".join(deduped)


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value.strip()
    return ""


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
