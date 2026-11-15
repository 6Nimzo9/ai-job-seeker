"""Additional official/free job-feed adapters for the local AI Job Seeker.

The adapters only consume public JSON APIs or RSS/Atom feeds. They preserve canonical
listing URLs and source attribution. Remote Landers provides a public read-only API.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import requests
TIMEOUT = 30
HEADERS = {"User-Agent": "AIJobSeekerLocal/1.0 (personal job search; links to original listings)"}


def _plain(value: Any) -> str:
    """Turn common HTML descriptions into safe plain text for matching/storage."""
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(x) for x in value if x is not None)
    text = str(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _items(value: Any) -> list[Any]:
    """Normalize a feed field that can be a string or a list of strings."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _job(job_id: Any, title: Any, company: Any, url: Any, location: Any,
         description: Any = "", date: Any = None, closing_date: Any = None,
         tags: Any = None, company_url: Any = None, source: str = "") -> dict:
    if isinstance(tags, str):
        tags = [tags]
    tags = tags or []
    return {
        "id": str(job_id or url or "").strip(),
        "position": _plain(title) or "Untitled",
        "company": _plain(company) or "Unknown",
        "company_url": company_url,
        "url": str(url or "").strip(),
        "location": _plain(location) or "Location not specified",
        "tags": [_plain(t) for t in tags if _plain(t)],
        "description": _plain(description),
        "date": date,
        "closing_date": closing_date,
        "source_name": source,
        "source_url": {
            "Jobicy": "https://jobicy.com",
            "Arbeitnow": "https://www.arbeitnow.com",
            "We Work Remotely": "https://weworkremotely.com",
            "Remote.co": "https://remote.co",
            "Jobspresso": "https://jobspresso.co",
            "Remote Landers": "https://remotelanders.com",
            "Python.org Job Board": "https://www.python.org/jobs/",
        }.get(source, ""),
    }


def _get_json(url: str, params: dict | None = None) -> dict | list:
    response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_jobicy_jobs() -> list[dict]:
    """Jobicy public remote-jobs API; no API key required. Once/day is conservative."""
    payload = _get_json("https://jobicy.com/api/v2/remote-jobs", {"count": 200})
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    results = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        job_id = item.get("id") or item.get("jobSlug")
        if not url or not job_id:
            continue
        geo = item.get("jobGeo") or "Anywhere"
        result = _job(
            job_id, item.get("jobTitle"), item.get("companyName"), url, geo,
            item.get("jobDescription") or item.get("jobExcerpt"),
            item.get("pubDate"),
            next((item.get(k) for k in ("expiryDate", "expiresAt", "expires_at", "validThrough") if item.get(k)), None),
            [*_items(item.get("jobIndustry")), *_items(item.get("jobType")), item.get("jobLevel")],
            source="Jobicy",
        )
        results.append(result)
    print(f"[Jobicy] Lowongan valid: {len(results)}")
    return results


def fetch_arbeitnow_jobs() -> list[dict]:
    """Arbeitnow public API; keep records explicitly marked remote."""
    sources = (
        ("https://www.arbeitnow.com/api/job-board-api", "Arbeitnow"),
        ("https://www.arbeitnow.co.uk/api/job-board-api", "Arbeitnow"),
    )
    by_url: dict[str, dict] = {}
    for endpoint, source in sources:
        try:
            payload = _get_json(endpoint)
        except requests.RequestException as exc:
            print(f"[Arbeitnow] Feed gagal ({endpoint}): {type(exc).__name__}")
            continue
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict) or not item.get("remote"):
                continue
            url = item.get("url")
            job_id = item.get("slug") or item.get("id") or url
            if not url or not job_id:
                continue
            by_url[url] = _job(
                job_id, item.get("title"), item.get("company_name"), url,
                item.get("location") or ("Remote" if item.get("remote") else "Location not specified"),
                item.get("description"), item.get("created_at"),
                next((item.get(k) for k in ("expiryDate", "expires_at", "validThrough", "deadline") if item.get(k)), None),
                [*_items(item.get("tags")), *_items(item.get("job_types"))],
                source=source,
            )
    results = list(by_url.values())
    print(f"[Arbeitnow] Lowongan remote valid: {len(results)}")
    return results


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join((t or "").strip() for t in node.itertext() if (t or "").strip()).strip()


def _date_from_feed(item: ET.Element, names: tuple[str, ...]) -> str | None:
    for node in item.iter():
        if _local_name(node.tag) in names:
            value = _node_text(node)
            if value:
                try:
                    # Validate common RSS date shape but preserve string for common parser.
                    parsedate_to_datetime(value)
                    return value
                except (TypeError, ValueError, OverflowError):
                    return value
    return None


def _fetch_rss(feed_url: str, source: str, host_suffixes: tuple[str, ...]) -> list[dict]:
    response = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        print(f"[{source}] XML RSS tidak valid: {exc}")
        return []
    rows: list[dict] = []
    for item in root.iter():
        if _local_name(item.tag) not in {"item", "entry"}:
            continue
        title = _node_text(next((c for c in list(item) if _local_name(c.tag) == "title"), None))
        link_node = next((c for c in list(item) if _local_name(c.tag) == "link"), None)
        url = ""
        if link_node is not None:
            url = (link_node.attrib.get("href") or _node_text(link_node)).strip()
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.hostname or not any(parsed.hostname == h or parsed.hostname.endswith("." + h) for h in host_suffixes):
            continue
        guid = _node_text(next((c for c in list(item) if _local_name(c.tag) in {"guid", "id"}), None)) or url
        description = ""
        employer_node = next((c for c in list(item) if _local_name(c.tag) in {"company", "companyname", "employer", "employername", "organisation", "organization"}), None)
        location_node = next((c for c in list(item) if _local_name(c.tag) in {"location", "joblocation", "job_geo", "jobgeo"}), None)
        feed_company = _node_text(employer_node)
        feed_location = _node_text(location_node)
        for child in list(item):
            if _local_name(child.tag) in {"description", "summary", "content", "encoded"}:
                description = _node_text(child)
                if description:
                    break
        pub_date = _date_from_feed(item, ("pubdate", "published", "updated", "date"))
        closing_date = _date_from_feed(item, ("expirydate", "expiresat", "expires_at", "validthrough", "deadline", "closingdate"))
        # Most RSS feeds use "Company: Role"; retain full title when structure is ambiguous.
        company, role = feed_company or "Unknown", title or "Untitled"
        if ":" in role:
            left, right = role.split(":", 1)
            if left.strip() and right.strip() and len(left.strip()) <= 100:
                if not feed_company:
                    company = left.strip()
                role = right.strip()
        categories = [_node_text(c) for c in list(item) if _local_name(c.tag) == "category"]
        location_fallback = "Location not specified" if source == "Python.org Job Board" else "Remote / check employer details"
        rows.append(_job(guid, role, company, url, feed_location or location_fallback, description,
                         pub_date, closing_date, categories, source=source))
    # Avoid repeated nodes if a namespace structure unexpectedly nests item/entry tags.
    unique = {j["url"]: j for j in rows}
    print(f"[{source}] RSS valid: {len(unique)}")
    return list(unique.values())


def fetch_we_work_remotely_jobs() -> list[dict]:
    return _fetch_rss("https://weworkremotely.com/remote-jobs.rss", "We Work Remotely", ("weworkremotely.com",))


def fetch_remote_co_jobs() -> list[dict]:
    return _fetch_rss("https://remote.co/feed/?post_type=job_listing", "Remote.co", ("remote.co",))


def fetch_jobspresso_jobs() -> list[dict]:
    return _fetch_rss("https://jobspresso.co/feed/?post_type=job_listing", "Jobspresso", ("jobspresso.co",))


def fetch_python_org_jobs() -> list[dict]:
    """Official Python.org job board RSS, filtered to remote/global opportunities."""
    rows = _fetch_rss("https://www.python.org/jobs/feed/rss/", "Python.org Job Board", ("python.org",))
    remote_terms = ("remote", "worldwide", "anywhere", "distributed", "work from home", "work-from-home")
    results = []
    for row in rows:
        combined = " ".join(str(row.get(k) or "") for k in ("location", "position", "description")).lower()
        if any(term in combined for term in remote_terms):
            results.append(row)
    print(f"[Python.org Job Board] Lowongan remote/global valid: {len(results)}")
    return results


def fetch_remote_landers_jobs() -> list[dict]:
    """Remote Landers public read-only API; original listing link retained."""
    payload = _get_json("https://remotelanders.com/api/jobs", {"limit": 100, "page": 1})
    rows = payload.get("jobs", []) if isinstance(payload, dict) else []
    results: list[dict] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        slug = item.get("slug") or url
        if not url or not slug:
            continue
        location = item.get("location") or "Remote / check employer details"
        company = item.get("company") or "Unknown"
        desc = item.get("description") or item.get("summary") or item.get("descriptionText") or ""
        tags = [item.get("category"), *_items(item.get("subtags")), item.get("type"), item.get("level")]
        results.append(_job(
            slug, item.get("title"), company, url, location, desc,
            item.get("postedDate") or item.get("posted_at"),
            next((item.get(k) for k in ("expiryDate", "expiresAt", "expires_at", "validThrough", "deadline") if item.get(k)), None),
            tags, company_url=item.get("companyWebsite"), source="Remote Landers",
        ))
    print(f"[Remote Landers] Lowongan valid: {len(results)}")
    return results
