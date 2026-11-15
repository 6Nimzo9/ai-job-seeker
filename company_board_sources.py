"""Fetch company vacancies from documented public ATS job-posting APIs.

Supported public endpoints:
- Greenhouse Job Board API
- Lever Postings API
- Ashby Public Job Posting API
"""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote, urlparse

import requests

USER_AGENT = "AIJobSeeker/2.0 (personal job discovery; contact: local user)"
REQUEST_TIMEOUT = 18
MAX_LEVER_JOBS_PER_BOARD = 200


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_text(x) for x in value if x is not None).strip()
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("label") or value.get("text") or "")
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def _strip_html(value: Any) -> str:
    raw = html.unescape(str(value or ""))
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"Respons bukan JSON dari {urlparse(url).netloc}") from exc


def _greenhouse(board: dict[str, Any]) -> list[dict[str, Any]]:
    slug = board["slug"]
    payload = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug, safe='')}/jobs", {"content": "true"})
    rows = payload.get("jobs", []) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Format Greenhouse tidak berisi jobs array.")
    output = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("absolute_url") or not row.get("title"):
            continue
        offices = row.get("offices") or []
        departments = row.get("departments") or []
        office_labels = []
        for office in offices:
            if isinstance(office, dict):
                office_name = _text(office.get("name"))
                office_location = _text(office.get("location"))
                label = " - ".join(x for x in (office_name, office_location) if x)
                if label:
                    office_labels.append(label)
        location = "; ".join(office_labels)
        tags = [
            _text(x.get("name")) for x in departments if isinstance(x, dict)
        ]
        output.append({
            "id": str(row.get("id") or row["absolute_url"]),
            "position": _text(row.get("title")),
            "company": board["company"],
            "company_url": board["board_url"],
            "url": _text(row.get("absolute_url")),
            "location": location or "Location not specified",
            "tags": tags,
            "description": _strip_html(row.get("content")),
            "date": row.get("updated_at"),
        })
    return output


def _lever(board: dict[str, Any]) -> list[dict[str, Any]]:
    slug = board["slug"]
    params = {"mode": "json", "limit": 100, "skip": 0}
    try:
        encoded_slug = quote(slug, safe="")
        payload = _get_json(f"https://api.lever.co/v0/postings/{encoded_slug}", params)
    except requests.HTTPError as first_error:
        try:
            payload = _get_json(f"https://api.eu.lever.co/v0/postings/{encoded_slug}", params)
        except requests.RequestException:
            raise first_error
    if not isinstance(payload, list):
        raise ValueError("Format Lever bukan list.")
    output = []
    for row in payload[:MAX_LEVER_JOBS_PER_BOARD]:
        if not isinstance(row, dict) or not row.get("hostedUrl") or not row.get("text"):
            continue
        cats = row.get("categories") or {}
        tags = [
            _text(cats.get(k)) for k in ("team", "department", "commitment")
            if cats.get(k)
        ]
        output.append({
            "id": str(row.get("id") or row["hostedUrl"]),
            "position": _text(row.get("text")),
            "company": board["company"],
            "company_url": board["board_url"],
            "url": _text(row.get("hostedUrl")),
            "location": _text(cats.get("location")) or "Location not specified",
            "tags": tags,
            "description": _text(row.get("descriptionPlain")) or _strip_html(row.get("description")),
            "date": row.get("createdAt"),
        })
    return output


def _ashby(board: dict[str, Any]) -> list[dict[str, Any]]:
    slug = board["slug"]
    payload = _get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug, safe='')}",
        {"includeCompensation": "true"},
    )
    rows = payload.get("jobs", []) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Format Ashby tidak berisi jobs array.")
    output = []
    for row in rows:
        if not isinstance(row, dict) or row.get("isListed") is False:
            continue
        url = _text(row.get("jobUrl") or row.get("applyUrl"))
        title = _text(row.get("title"))
        if not url or not title:
            continue
        locations = [_text(row.get("location"))]
        secondary = row.get("secondaryLocations") or []
        for item in secondary:
            if isinstance(item, dict):
                locations.append(_text(item.get("location")))
            else:
                locations.append(_text(item))
        locations = [x for x in locations if x]
        workplace_type = _text(row.get("workplaceType"))
        if workplace_type and workplace_type.lower() not in " ".join(locations).lower():
            locations.insert(0, workplace_type)
        elif row.get("isRemote") and not any("remote" in x.lower() for x in locations):
            locations.insert(0, "Remote or Hybrid (employer indicator)")
        tags = [_text(row.get(k)) for k in ("department", "team", "employmentType") if row.get(k)]
        description = _text(row.get("descriptionPlain")) or _strip_html(row.get("descriptionHtml"))
        output.append({
            "id": str(row.get("id") or url),
            "position": title,
            "company": board["company"],
            "company_url": board["board_url"],
            "url": url,
            "location": "; ".join(locations) or "Location not specified",
            "tags": tags,
            "description": description,
            "date": row.get("publishedAt"),
        })
    return output


def fetch_company_board_jobs(board: dict[str, Any]) -> list[dict[str, Any]]:
    provider = str(board.get("provider") or "").strip().lower()
    if provider == "greenhouse":
        return _greenhouse(board)
    if provider == "lever":
        return _lever(board)
    if provider == "ashby":
        return _ashby(board)
    raise ValueError(f"Provider ATS tidak didukung: {provider or 'empty'}")
