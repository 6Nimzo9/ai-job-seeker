
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
import uuid

import requests

from db import get_connection
from profile_settings import get_profile_settings
from sources.remotive import fetch_remotive_jobs
from sources.himalayas import fetch_himalayas_jobs

from company_board_sources import fetch_company_board_jobs

from sources.extra_job_sources import (
    fetch_jobicy_jobs,
    fetch_arbeitnow_jobs,
    fetch_we_work_remotely_jobs,
    fetch_remote_co_jobs,
    fetch_jobspresso_jobs,
    fetch_python_org_jobs,
    fetch_remote_landers_jobs,
)


REMOTEOK_API = "https://remoteok.com/api"
REMOTIVE_API = "https://remotive.com/api/remote-jobs"


SOURCES = {
    "RemoteOK": {
        "url": REMOTEOK_API,
        "type": "public_api",
    },
    "Remotive": {
        "url": REMOTIVE_API,
        "type": "public_api",
    },
    "Himalayas": {
        "url": "https://himalayas.app",
        "type": "public_api",
    },
    "Jobicy": {
        "url": "https://jobicy.com/api/jobs",
        "type": "public_api",
    },
    "Arbeitnow": {
        "url": "https://www.arbeitnow.com/api/job-board-api",
        "type": "public_api",
    },
    "We Work Remotely": {
        "url": "https://weworkremotely.com/remote-jobs.rss",
        "type": "rss",
    },
    "Remote.co": {
        "url": "https://remote.co/feed/?post_type=job_listing",
        "type": "rss",
    },
    "Jobspresso": {
        "url": "https://jobspresso.co/feed/?post_type=job_listing",
        "type": "rss",
    },
    "Remote Landers": {
        "url": "https://remotelanders.com/api/jobs",
        "type": "public_api",
    },
    "Python.org Job Board": {
        "url": "https://www.python.org/jobs/feed/rss/",
        "type": "rss",
    },
}


PROJECT_DIR = Path(__file__).resolve().parent
SOURCE_REGISTRY_PATH = PROJECT_DIR / "source_registry.json"
COMPANY_BOARDS = []


def load_company_board_registry():
    """Load a locally generated registry of public employer ATS job boards."""
    if not SOURCE_REGISTRY_PATH.exists():
        print("INFO: source_registry.json belum ada; hanya 10 sumber awal yang digunakan.")
        return []
    try:
        payload = json.loads(SOURCE_REGISTRY_PATH.read_text(encoding="utf-8"))
        boards = payload.get("boards", []) if isinstance(payload, dict) else []
        if not isinstance(boards, list):
            raise ValueError("Field boards harus berupa list.")
        valid = []
        seen = set()
        for board in boards:
            if not isinstance(board, dict):
                continue
            provider = str(board.get("provider") or "").lower().strip()
            slug = str(board.get("slug") or "").strip()
            company = str(board.get("company") or "").strip()
            name = str(board.get("name") or "").strip()
            if provider not in {"greenhouse", "lever", "ashby"} or not slug or not company or not name:
                continue
            key = (provider, slug.casefold())
            if key in seen:
                continue
            seen.add(key)
            board = dict(board)
            board["provider"] = provider
            board["slug"] = slug
            board["company"] = company
            board["name"] = name
            board_url = str(board.get("board_url") or board.get("source_url") or "").strip()
            if not board_url.startswith("https://"):
                continue
            board["board_url"] = board_url
            board["source_url"] = board_url
            board["source_type"] = f"ats_{provider}"
            valid.append(board)
        if len(valid) < 100:
            raise ValueError(f"Registry hanya berisi {len(valid)} board valid; minimum 100 diperlukan.")
        return valid[:100]
    except Exception as exc:
        print(f"PERINGATAN: registry sumber tidak dapat dibaca: {type(exc).__name__}: {exc}")
        return []


COMPANY_BOARDS = load_company_board_registry()
for _board in COMPANY_BOARDS:
    SOURCES[_board["name"]] = {
        "url": _board["board_url"],
        # Keep the existing source_type value compatible with the DB schema.
        "type": "public_api",
        "provider": _board["provider"],
        "slug": _board["slug"],
        "company": _board["company"],
    }


SOURCE_RUN_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS source_run_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id CHAR(36) NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    provider VARCHAR(40) NULL,
    source_type VARCHAR(40) NULL,
    run_status VARCHAR(24) NOT NULL,
    fetched_count INT UNSIGNED NOT NULL DEFAULT 0,
    unique_fetched_count INT UNSIGNED NOT NULL DEFAULT 0,
    matched_count INT UNSIGNED NOT NULL DEFAULT 0,
    rejected_role_count INT UNSIGNED NOT NULL DEFAULT 0,
    inserted_count INT UNSIGNED NOT NULL DEFAULT 0,
    database_duplicate_count INT UNSIGNED NOT NULL DEFAULT 0,
    feed_duplicate_count INT UNSIGNED NOT NULL DEFAULT 0,
    invalid_url_count INT UNSIGNED NOT NULL DEFAULT 0,
    duration_ms INT UNSIGNED NOT NULL DEFAULT 0,
    error_message VARCHAR(1200) NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_source_run_latest (source_name, id),
    KEY idx_source_run_id (run_id),
    UNIQUE KEY uq_source_run_source (run_id, source_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _new_source_metrics():
    metrics = {}
    for name, config in SOURCES.items():
        metrics[name] = {
            "source_name": name,
            "provider": config.get("provider") or config.get("type") or "unknown",
            "source_type": config.get("type") or "unknown",
            "run_status": "not_run",
            "fetched_count": 0,
            "unique_fetched_count": 0,
            "matched_count": 0,
            "rejected_role_count": 0,
            "inserted_count": 0,
            "database_duplicate_count": 0,
            "feed_duplicate_count": 0,
            "invalid_url_count": 0,
            "duration_ms": 0,
            "error_message": None,
            "started_at": None,
            "_fetch_started_perf": None,
        }
    return metrics


def _start_source_timer(metric):
    metric["started_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    metric["_fetch_started_perf"] = time.perf_counter()


def _stop_source_timer(metric):
    started = metric.get("_fetch_started_perf")
    if started is not None:
        metric["duration_ms"] = max(0, int((time.perf_counter() - started) * 1000))
        metric["_fetch_started_perf"] = None


def _persist_source_run_start(metrics, run_id, run_started_at):
    """Write a running marker before network calls so interrupted executions remain visible."""
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(SOURCE_RUN_LOGS_DDL)
        rows = [
            (run_id, name, metric.get("provider"), metric.get("source_type"), run_started_at)
            for name, metric in metrics.items()
        ]
        cursor.executemany(
            """INSERT INTO source_run_logs
               (run_id, source_name, provider, source_type, run_status, started_at, finished_at)
               VALUES (%s, %s, %s, %s, 'running', %s, NULL)
               ON DUPLICATE KEY UPDATE run_status = 'running', started_at = VALUES(started_at), finished_at = NULL""",
            rows,
        )
        conn.commit()
        print(f"Source run dimulai dan ditandai di database: {len(rows)} sumber.")
        return True
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"PERINGATAN: penanda awal source run tidak dapat disimpan: {type(exc).__name__}: {exc}")
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _persist_source_run_logs(metrics, run_id, run_finished_at):
    """Persist final per-source results; logging failure must not fail the discovery itself."""
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(SOURCE_RUN_LOGS_DDL)
        rows = []
        for name, metric in metrics.items():
            rows.append((
                run_id, name, metric.get("provider"), metric.get("source_type"),
                metric.get("run_status", "not_run"),
                int(metric.get("fetched_count", 0)),
                int(metric.get("unique_fetched_count", 0)),
                int(metric.get("matched_count", 0)),
                int(metric.get("rejected_role_count", 0)),
                int(metric.get("inserted_count", 0)),
                int(metric.get("database_duplicate_count", 0)),
                int(metric.get("feed_duplicate_count", 0)),
                int(metric.get("invalid_url_count", 0)),
                int(metric.get("duration_ms", 0)),
                (str(metric.get("error_message") or "")[:1200] or None),
                metric.get("started_at") or run_finished_at, run_finished_at,
            ))
        cursor.executemany(
            """INSERT INTO source_run_logs
               (run_id, source_name, provider, source_type, run_status,
                fetched_count, unique_fetched_count, matched_count, rejected_role_count,
                inserted_count, database_duplicate_count, feed_duplicate_count, invalid_url_count,
                duration_ms, error_message, started_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                provider = VALUES(provider), source_type = VALUES(source_type),
                run_status = VALUES(run_status), fetched_count = VALUES(fetched_count),
                unique_fetched_count = VALUES(unique_fetched_count), matched_count = VALUES(matched_count),
                rejected_role_count = VALUES(rejected_role_count), inserted_count = VALUES(inserted_count),
                database_duplicate_count = VALUES(database_duplicate_count),
                feed_duplicate_count = VALUES(feed_duplicate_count), invalid_url_count = VALUES(invalid_url_count),
                duration_ms = VALUES(duration_ms), error_message = VALUES(error_message),
                started_at = VALUES(started_at), finished_at = VALUES(finished_at)""",
            rows,
        )
        conn.commit()
        print(f"Source run metrics selesai tersimpan: {len(rows)} sumber (run_id={run_id}).")
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"PERINGATAN: metrik sumber tidak dapat disimpan: {type(exc).__name__}: {exc}")
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


ROLE_KEYWORDS = (
    "ai automation engineer",
    "ai automation",
    "automation engineer",
    "workflow automation",
    "workflow engineer",
    "workflow architect",
    "automation developer",
    "automation specialist",
    "python automation",
    "business process automation",
    "process automation engineer",
    "intelligent automation",
    "llm automation",
    "ai workflow",
    "n8n",
    "power automate",
    "rpa developer",
)

# AI Engineer generik bukan target utama.
EXCLUDED_TITLE_PATTERNS = (
    r"\bai engineer\b",
    r"\bmachine learning engineer\b",
    r"\bdata scientist\b",
    r"\bdata engineer\b",
    r"\bresearch scientist\b",
)

LOCATION_KEYWORDS = (
    "australia",
    "sydney",
    "melbourne",
    "brisbane",
    "perth",
    "singapore",
    "indonesia",
    "asia",
    "worldwide",
    "anywhere",
    "global",
    "distributed",
    "remote",
)

# Negara yang perlu ditinjau karena tidak menjadi prioritas.
RESTRICTED_LOCATION_KEYWORDS = (
    "united states",
    "usa",
    "u.s.",
    "canada",
    "united kingdom",
    "uk only",
    "germany",
    "europe only",
)


def normalize_text(value):
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)

    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def get_source_id(cursor, source_name):
    source = SOURCES[source_name]

    cursor.execute(
        """
        INSERT INTO job_sources (name, source_url, source_type)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
        """,
        (
            source_name,
            source["url"],
            source["type"],
        ),
    )

    return cursor.lastrowid


def fetch_remoteok_jobs():
    response = requests.get(
        REMOTEOK_API,
        headers={
            "User-Agent": "AIJobSeekerLocal/1.0"
        },
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError("Respons RemoteOK bukan list.")

    result = {}

    for job in data:
        if not isinstance(job, dict) or not job.get("id"):
            continue

        job_id = str(job["id"])

        result[job_id] = {
            "id": job_id,
            "position": job.get("position", ""),
            "company": job.get("company", "Unknown"),
            "company_url": job.get("company_url"),
            "url": job.get("url", ""),
            "location": job.get("location", ""),
            "tags": job.get("tags") or [],
            "description": job.get("description", ""),
            "date": job.get("date"),
            "closing_date": next((job.get(k) for k in ("closing_date", "closingDate", "close_date", "closeDate", "application_deadline", "applicationDeadline", "deadline", "expires_at", "expiresAt", "expiryDate", "validThrough") if job.get(k)), None),
            "source_name": "RemoteOK",
        }

    return list(result.values())


DYNAMIC_ROLE_KEYWORDS = []

def get_role_match(job):
    title = normalize_text(job.get("position"))
    tags = normalize_text(job.get("tags"))
    description = normalize_text(job.get("description"))

    title_and_tags = f"{title} {tags}"

    # Prioritas utama: posisi yang secara langsung relevan.
    exact_role_terms = (
        "ai automation engineer",
        "ai automation specialist",
        "ai automation developer",
        "automation engineer",
        "automation developer",
        "automation specialist",
        "workflow automation",
        "workflow engineer",
        "workflow architect",
        "python automation",
        "business process automation",
        "process automation engineer",
        "intelligent automation",
        "llm automation",
        "ai workflow",
        "n8n",
        "power automate",
        "rpa developer",
        "automation analyst",
        "automation architect",
        "automation consultant",
        "automation manager",
        "ai enablement",
        "gtm automation",
    )

    dynamic_terms = tuple(
        normalize_text(term) for term in globals().get("DYNAMIC_ROLE_KEYWORDS", [])
        if normalize_text(term)
    )
    # When a saved profile is available, its target roles are authoritative.
    # Static defaults are used only if no profile configuration could be loaded.
    all_role_terms = dynamic_terms if dynamic_terms else exact_role_terms
    matched_keywords = [
        term for term in all_role_terms
        if term in title_and_tags
    ]

    # Kecocokan langsung pada judul atau tag.
    if matched_keywords:
        return True, matched_keywords

    # AI Engineer atau Data Engineer generik bukan target otomatis.
    excluded_patterns = (
        r"\bai engineer\b",
        r"\bmachine learning engineer\b",
        r"\bdata scientist\b",
        r"\bdata engineer\b",
        r"\bresearch scientist\b",
    )

    if any(re.search(pattern, title) for pattern in excluded_patterns):
        return False, []

    # Kecocokan tambahan hanya jika ada konteks automation
    # atau integration yang jelas pada judul/tag.
    relevant_role_context = any(
        term in title_and_tags
        for term in (
            "automation",
            "workflow",
            "integration",
            "business process",
        )
    )

    technical_terms = (
        "artificial intelligence",
        "large language model",
        "llm",
        "generative ai",
        "openai",
        "claude",
        "api integration",
        "workflow automation",
        "ai-powered automation",
    )

    description_matches = [
        term for term in technical_terms
        if term in description
    ]

    if relevant_role_context and description_matches:
        return True, [
            "automation/integration context",
            *description_matches,
        ]

    return False, []

    return bool(matched_keywords), matched_keywords




def get_location_status(job):
    location = normalize_text(job.get("location"))
    title = normalize_text(job.get("position"))
    description = normalize_text(job.get("description"))

    combined = f"{title} {location} {description}"

    # Judul secara eksplisit mengindikasikan pembatasan AS.
    if any(term in combined for term in (
        "title indicates u.s. restriction",
        "remote - u.s.",
        "remote us",
        "remote usa",
        "united states only",
        "us only",
        "usa only",
        "must be based in the us",
        "must reside in the us",
        "u.s. only",
    )):
        return "location_restriction_review"

    # Batasan negara yang eksplisit perlu diverifikasi.
    if any(term in combined for term in (
        "canada only",
        "uk only",
        "united kingdom only",
        "europe only",
        "north america only",
        "must be authorized to work in the us",
        "us work authorization required",
    )):
        return "location_restriction_review"

    # Jika API tidak memberikan lokasi, jangan menyimpulkan
    # bahwa kandidat Indonesia pasti memenuhi syarat.
    if (
        not location
        or "location restrictions unspecified" in location
    ):
        return "review_required"

    # Worldwide adalah indikasi cakupan luas, bukan jaminan.
    if any(term in location for term in (
        "worldwide",
        "work from anywhere",
        "global remote",
        "anywhere in the world",
        "international remote",
    )):
        return "review_required"

    # Lokasi remote atau zona waktu saja belum membuktikan
    # pembatasan negara.
    if any(term in location for term in (
        "remote",
        "distributed",
        "timezone restrictions",
        "asia",
        "apac",
    )):
        return "review_required"

    return "location_restriction_review"

def parse_date(value):
    """Parse explicit ISO strings, Unix timestamps, and RSS/RFC822 dates."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            stamp = float(value)
            if abs(stamp) > 100_000_000_000:  # milliseconds instead of seconds
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(tzinfo=None)
        text = str(value).strip()
        if not text:
            return None
        # Unix timestamp supplied as numeric text.
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            stamp = float(text)
            if abs(stamp) > 100_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(tzinfo=None)
        try:
            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                result = datetime.fromisoformat(text)
            else:
                result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            from email.utils import parsedate_to_datetime
            result = parsedate_to_datetime(text)
        if result.tzinfo:
            result = result.astimezone(timezone.utc).replace(tzinfo=None)
        return result
    except (ValueError, TypeError, OSError, OverflowError, IndexError):
        return None


def valid_job_url(url, source_name):
    if not url:
        return False

    parsed = urlparse(str(url).strip())

    if parsed.scheme != "https" or not parsed.hostname:
        return False

    provider = SOURCES.get(source_name, {}).get("provider")
    provider_domains = {
        "greenhouse": ("greenhouse.io",),
        "lever": ("lever.co",),
        "ashby": ("ashbyhq.com",),
    }
    if provider in provider_domains:
        host = parsed.hostname.lower().rstrip(".")
        allowed = provider_domains[provider]
        return (
            any(host == domain or host.endswith("." + domain) for domain in allowed)
            and parsed.username is None
            and parsed.password is None
        )

    allowed_hosts = {
        "RemoteOK": {"remoteok.com", "www.remoteok.com"},
        "Remotive": {"remotive.com", "www.remotive.com"},
        "Himalayas": {"himalayas.app", "www.himalayas.app"},
        "Jobicy": {'jobicy.com', 'www.jobicy.com'},
        "Arbeitnow": {'arbeitnow.co.uk', 'arbeitnow.com', 'www.arbeitnow.co.uk', 'www.arbeitnow.com'},
        "We Work Remotely": {'weworkremotely.com', 'www.weworkremotely.com'},
        "Remote.co": {'remote.co', 'www.remote.co'},
        "Jobspresso": {'jobspresso.co', 'www.jobspresso.co'},
        "Remote Landers": {'remotelanders.com', 'www.remotelanders.com'},
        "Python.org Job Board": {'python.org', 'www.python.org'},
    }

    allowed = allowed_hosts.get(source_name, set())

    return (
        parsed.hostname in allowed
        and parsed.username is None
        and parsed.password is None
    )


def save_jobs(jobs, source_metrics=None):
    inserted = 0
    duplicates = 0
    skipped = 0

    with get_connection() as conn:
        cursor = conn.cursor()

        try:
            source_ids = {
                name: get_source_id(cursor, name)
                for name in SOURCES
            }

            for job in jobs:
                source_name = job.get("source_name")

                if source_name not in source_ids:
                    skipped += 1
                    if source_metrics is not None and source_name in source_metrics:
                        source_metrics[source_name]["invalid_url_count"] += 1
                    continue

                job_url = str(job.get("url") or "").strip()

                if not valid_job_url(job_url, source_name):
                    skipped += 1
                    if source_metrics is not None and source_name in source_metrics:
                        source_metrics[source_name]["invalid_url_count"] += 1
                    continue

                # Skema jobs mempunyai unique index pada job_url.
                cursor.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE job_url = %s
                    LIMIT 1
                    """,
                    (job_url,),
                )

                existing_job = cursor.fetchone()

                if existing_job:
                    cursor.execute(
                        """
                        UPDATE jobs
                        SET posted_at = COALESCE(%s, posted_at),
                            closing_at = COALESCE(%s, closing_at)
                        WHERE id = %s
                        """,
                        (parse_date(job.get("date")), parse_date(job.get("closing_date")), existing_job[0]),
                    )
                    if source_name == "Himalayas":
                        refreshed_location = str(
                            job.get("location")
                            or "Location restrictions unspecified"
                        ).strip()[:255]

                        location_lower = refreshed_location.lower()

                        if (
                            "location restrictions unspecified" in location_lower
                            or "location restrictions present but names unavailable"
                            in location_lower
                        ):
                            refreshed_country = None

                        elif "australia" in location_lower:
                            refreshed_country = "Australia"

                        elif "singapore" in location_lower:
                            refreshed_country = "Singapore"

                        elif any(
                            word in location_lower
                            for word in (
                                "worldwide",
                                "anywhere",
                                "global",
                                "distributed",
                            )
                        ):
                            refreshed_country = "International Remote"

                        else:
                            candidate_country = refreshed_location.split(";", 1)[0].strip()
                            # Full location restrictions remain in the location column.
                            refreshed_country = (
                                candidate_country
                                if candidate_country and len(candidate_country) <= 100
                                else None
                            )

                        cursor.execute(
                            """
                            UPDATE jobs
                            SET location = %s,
                                country = %s
                            WHERE id = %s
                            """,
                            (
                                refreshed_location,
                                refreshed_country,
                                existing_job[0],
                            ),
                        )

                    duplicates += 1
                    if source_metrics is not None and source_name in source_metrics:
                        source_metrics[source_name]["database_duplicate_count"] += 1
                    continue

                company_name = str(
                    job.get("company") or "Unknown"
                ).strip()[:255]

                company_url = job.get("company_url")

                cursor.execute(
                    """
                    INSERT INTO companies (name, website)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE
                        id = LAST_INSERT_ID(id)
                    """,
                    (company_name, company_url),
                )

                company_id = cursor.lastrowid

                location = str(
                    job.get("location") or "Unknown"
                ).strip()[:255]

                location_lower = location.lower()
                country = None

                if "australia" in location_lower:
                    country = "Australia"
                elif "singapore" in location_lower:
                    country = "Singapore"
                elif any(
                    word in location_lower
                    for word in (
                        "worldwide",
                        "anywhere",
                        "global",
                        "distributed",
                    )
                ):
                    country = "International Remote"
                else:
                    country = None

                cursor.execute(
                    """
                    INSERT INTO jobs (
                        company_id,
                        source_id,
                        external_id,
                        title,
                        location,
                        country,
                        workplace_type,
                        description,
                        job_url,
                        posted_at,
                        closing_at,
                        status
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, 'new'
                    )
                    """,
                    (
                        company_id,
                        source_ids[source_name],
                        str(job.get("id") or "")[:255],
                        str(
                            job.get("position") or "Untitled"
                        ).strip()[:500],
                        location,
                        country,
                        "Remote",
                        str(job.get("description") or ""),
                        job_url,
                        parse_date(job.get("date")),
                        parse_date(job.get("closing_date")),
                    ),
                )

                inserted += 1
                if source_metrics is not None and source_name in source_metrics:
                    source_metrics[source_name]["inserted_count"] += 1

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            cursor.close()

    return inserted, duplicates, skipped


def main():
    run_id = str(uuid.uuid4())
    run_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    source_metrics = _new_source_metrics()
    _persist_source_run_start(source_metrics, run_id, run_started_at)

    try:
        profile_config = get_profile_settings()
        globals()["DYNAMIC_ROLE_KEYWORDS"] = profile_config["target_roles"]
        print("Profesi target aktif:", ", ".join(profile_config["target_roles"]))
    except Exception as exc:
        print(f"PERINGATAN: pengaturan profil tidak dapat dimuat; menggunakan keyword default. Detail: {type(exc).__name__}: {exc}")

    print("=" * 60)
    print("AI JOB SEEKER - MULTI-SOURCE DISCOVERY")
    print(f"Run ID: {run_id}")
    print("=" * 60)

    all_jobs = []

    static_sources = (
        ("RemoteOK", fetch_remoteok_jobs),
        ("Remotive", fetch_remotive_jobs),
        ("Himalayas", fetch_himalayas_jobs),
        ("Jobicy", fetch_jobicy_jobs),
        ("Arbeitnow", fetch_arbeitnow_jobs),
        ("We Work Remotely", fetch_we_work_remotely_jobs),
        ("Remote.co", fetch_remote_co_jobs),
        ("Jobspresso", fetch_jobspresso_jobs),
        ("Python.org Job Board", fetch_python_org_jobs),
        ("Remote Landers", fetch_remote_landers_jobs),
    )

    for source_name, fetch_function in static_sources:
        print(f"\nMengambil lowongan dari {source_name}...")
        metric = source_metrics[source_name]
        _start_source_timer(metric)
        try:
            source_jobs = fetch_function()
            if not isinstance(source_jobs, list):
                raise TypeError(f"Fetcher mengembalikan {type(source_jobs).__name__}, seharusnya list.")
            for job in source_jobs:
                job["source_name"] = source_name
            metric["fetched_count"] = len(source_jobs)
            metric["run_status"] = "success" if source_jobs else "empty"
            all_jobs.extend(source_jobs)
            print(f"{source_name}: {len(source_jobs)} lowongan diterima")
        except Exception as exc:
            metric["run_status"] = "failed"
            metric["error_message"] = f"{type(exc).__name__}: {exc}"
            print(f"{source_name} gagal diakses: {metric['error_message']}")
        finally:
            _stop_source_timer(metric)

    # Fetch employer boards concurrently with a bounded worker pool.
    if COMPANY_BOARDS:
        print(f"\nMengambil lowongan dari {len(COMPANY_BOARDS)} career board perusahaan (Greenhouse / Lever / Ashby)...")
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="company-board") as executor:
            future_map = {}
            for board in COMPANY_BOARDS:
                metric = source_metrics[board["name"]]
                _start_source_timer(metric)
                future_map[executor.submit(fetch_company_board_jobs, board)] = board

            for future in as_completed(future_map):
                board = future_map[future]
                source_name = board["name"]
                metric = source_metrics[source_name]
                try:
                    source_jobs = future.result()
                    if not isinstance(source_jobs, list):
                        raise TypeError(f"Fetcher mengembalikan {type(source_jobs).__name__}, seharusnya list.")
                    for job in source_jobs:
                        job["source_name"] = source_name
                    metric["fetched_count"] = len(source_jobs)
                    metric["run_status"] = "success" if source_jobs else "empty"
                    all_jobs.extend(source_jobs)
                except Exception as exc:
                    metric["run_status"] = "failed"
                    metric["error_message"] = f"{type(exc).__name__}: {exc}"
                finally:
                    _stop_source_timer(metric)

        successful = sum(1 for b in COMPANY_BOARDS if source_metrics[b["name"]]["run_status"] == "success")
        empty = sum(1 for b in COMPANY_BOARDS if source_metrics[b["name"]]["run_status"] == "empty")
        failed = sum(1 for b in COMPANY_BOARDS if source_metrics[b["name"]]["run_status"] == "failed")
        for board in COMPANY_BOARDS:
            metric = source_metrics[board["name"]]
            if metric["run_status"] == "failed":
                print(f"{board['name']}: gagal - {metric['error_message']}")
            else:
                print(f"{board['name']}: {metric['fetched_count']} lowongan diterima")
        print(
            f"Company board summary: {successful} berisi lowongan, "
            f"{empty} kosong, {failed} gagal dari {len(COMPANY_BOARDS)} board."
        )
    else:
        print("INFO: tidak ada company board tambahan yang dimuat.")

    # Deduplicate repeated records from the same source before role matching.
    unique_jobs = {}
    for job in all_jobs:
        source_name = str(job.get("source_name") or "")
        key = (source_name, str(job.get("id")))
        if key in unique_jobs:
            if source_name in source_metrics:
                source_metrics[source_name]["feed_duplicate_count"] += 1
            continue
        unique_jobs[key] = job
        if source_name in source_metrics:
            source_metrics[source_name]["unique_fetched_count"] += 1

    jobs = list(unique_jobs.values())
    matched_jobs = []

    for job in jobs:
        source_name = str(job.get("source_name") or "")
        role_match, keywords = get_role_match(job)
        if not role_match:
            if source_name in source_metrics:
                source_metrics[source_name]["rejected_role_count"] += 1
            continue
        job["matched_keywords"] = keywords
        job["eligibility_status"] = get_location_status(job)
        matched_jobs.append(job)
        if source_name in source_metrics:
            source_metrics[source_name]["matched_count"] += 1

    rejected_role = sum(m["rejected_role_count"] for m in source_metrics.values())
    print("\n" + "-" * 60)
    print("RINGKASAN")
    print("-" * 60)
    print(f"Total diterima        : {len(jobs)}")
    print(f"Cocok dengan target   : {len(matched_jobs)}")
    print(f"Tidak cocok role      : {rejected_role}")

    print("\nLOWONGAN YANG COCOK DENGAN TARGET")
    for job in matched_jobs[:20]:
        print(
            f"\n- {job.get('position', 'Untitled')}"
            f"\n  Perusahaan : {job.get('company', 'Unknown')}"
            f"\n  Sumber     : {job.get('source_name')}"
            f"\n  Lokasi     : {job.get('location') or 'Unknown'}"
            f"\n  Status     : {job['eligibility_status']}"
            f"\n  URL        : {job.get('url')}"
            f"\n  Keyword    : {', '.join(job['matched_keywords'])}"
        )

    print("\nMenyimpan lowongan yang cocok...")
    try:
        inserted, duplicates, skipped = save_jobs(matched_jobs, source_metrics=source_metrics)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        for metric in source_metrics.values():
            if metric["run_status"] in {"success", "empty"} and (metric["fetched_count"] or metric["matched_count"]):
                metric["run_status"] = "save_failed"
                metric["error_message"] = message
        _persist_source_run_logs(
            source_metrics, run_id,
            datetime.now(timezone.utc).replace(tzinfo=None),
        )
        raise

    print(f"Lowongan baru disimpan : {inserted}")
    print(f"Duplikat dilewati      : {duplicates}")
    print(f"URL dilewati           : {skipped}")

    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    _persist_source_run_logs(source_metrics, run_id, finished_at)

    print("\nHASIL PER SUMBER")
    for name, metric in source_metrics.items():
        print(
            f"{name}: status={metric['run_status']}, fetched={metric['fetched_count']}, "
            f"matched={metric['matched_count']}, inserted={metric['inserted_count']}, "
            f"duplicates={metric['database_duplicate_count']}, "
            f"duration_ms={metric['duration_ms']}"
        )
    print("\nProses selesai.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"Kesalahan jaringan: {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"Proses gagal: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
