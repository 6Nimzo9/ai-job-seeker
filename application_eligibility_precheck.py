"""Read-only expiry and location pre-check for an approved job application.

Phase 1 supports public Himalayas listing pages. Other sources are deliberately
reported as MANUAL_REVIEW_REQUIRED rather than guessed. This tool does not
modify MySQL, create a packet, fill forms, or submit an application.

Place beside db.py and prepare_application_packet.py, then run:
    python .\\application_eligibility_precheck.py --application-id 1 --profile-id 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlparse

TOOL_VERSION = "1.0.0"
HIMALAYAS_HOSTS = {"himalayas.app", "www.himalayas.app"}
MAX_PAGE_BYTES = 2_000_000
USER_AGENT = (
    "AIJobSeekerPrecheck/1.0 (+read-only public listing check; "
    "no login, form filling, or application submission)"
)


@dataclass
class PrecheckResult:
    result: str
    application_id: int | str
    job_id: int | str
    title: str
    source: str
    listing_url: str
    checked_on: str
    http_status: int | None
    deadline: str | None
    days_until_deadline: int | None
    title_match: bool | None
    open_to_all_countries_signal: bool | None
    messages: list[str]


class VisibleTextParser(HTMLParser):
    """Collect visible page text while ignoring script/style payloads."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._skip_stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_stack.append(tag)
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and tag in self._skip_stack:
            # HTML can contain nested/ill-formed tags; pop from the last matching tag.
            pos = len(self._skip_stack) - 1 - self._skip_stack[::-1].index(tag)
            del self._skip_stack[pos]
            self._skip_depth = len(self._skip_stack)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _parse_date(value: str) -> date | None:
    value = re.sub(r"\s+", " ", value.strip().replace("\u00a0", " "))
    formats = (
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
        "%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


_DEADLINE_PATTERNS = (
    re.compile(
        r"\b(?:apply\s+before|apply\s+by|application\s+deadline|deadline|valid\s+until)"
        r"\s*:?\s*(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:apply\s+before|apply\s+by|application\s+deadline|deadline|valid\s+until)"
        r"\s*:?\s*\d{4}-\d{2}-\d{2}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:apply\s+before|apply\s+by|application\s+deadline|deadline|valid\s+until)"
        r"\s*:?\s*\d{1,2}\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\s+\d{4}\b",
        re.IGNORECASE,
    ),
)


def parse_deadline(text: str) -> date | None:
    """Parse an explicit application deadline; never infer from an 'expired?' button."""
    normalized = re.sub(r"\s+", " ", text.replace("\u00a0", " "))
    for pattern in _DEADLINE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        fragment = match.group(0)
        # Strip the label, keeping the date portion.
        date_part = re.sub(
            r"^.*?\b(?:apply\s+before|apply\s+by|application\s+deadline|deadline|valid\s+until)\s*:?\s*",
            "", fragment, flags=re.IGNORECASE,
        )
        parsed = _parse_date(date_part)
        if parsed:
            return parsed
    return None


def detect_all_country_signal(text: str) -> bool:
    """Only return True for explicit broad eligibility wording, not just 'Remote'."""
    normalized = re.sub(r"\s+", " ", text).lower()
    patterns = (
        r"open to candidates from all countries",
        r"open to applicants from all countries",
        r"candidates from all countries",
        r"applicants from all countries",
        r"open to all countries",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def fetch_himalayas_listing(url: str, timeout: int = 15) -> tuple[int, str, str]:
    """GET a public Himalayas HTML listing only; no login or application actions."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in HIMALAYAS_HOSTS:
        raise ValueError("Live pre-check hanya mengakses HTTPS pada domain resmi himalayas.app.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        method="GET",
    )
    # Redirects are verified after the GET too; there are no credentials or POST actions.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        final = urlparse(final_url)
        if final.scheme != "https" or (final.hostname or "").lower() not in HIMALAYAS_HOSTS:
            raise ValueError("Listing dialihkan ke domain lain; pemeriksaan otomatis dihentikan.")
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError("Halaman listing melebihi batas ukuran yang diizinkan.")
        charset = response.headers.get_content_charset() or "utf-8"
        html = body.decode(charset, errors="replace")
        return int(response.status), final_url, visible_text(html)


def evaluate_listing(
    *,
    application_row: Mapping[str, Any],
    text: str,
    http_status: int,
    checked_on: date | None = None,
) -> PrecheckResult:
    """Evaluate a fetched official listing without mutating the application."""
    today = checked_on or date.today()
    app_id = application_row.get("application_id", application_row.get("id", "unknown"))
    job_id = application_row.get("job_id", "unknown")
    title = str(application_row.get("title") or "Unspecified")
    source = str(application_row.get("source_name") or "Unspecified")
    url = str(application_row.get("job_url") or "")
    messages: list[str] = []
    status = str(application_row.get("application_status") or "").strip().lower()
    if status != "approved":
        return PrecheckResult(
            "BLOCKED_NOT_APPROVED", app_id, job_id, title, source, url, today.isoformat(),
            http_status, None, None, None, None,
            ["Draf belum berstatus approved. Setujui dan tinjau draf terlebih dahulu."],
        )
    if application_row.get("applied_at") is not None:
        return PrecheckResult(
            "BLOCKED_ALREADY_SUBMITTED", app_id, job_id, title, source, url, today.isoformat(),
            http_status, None, None, None, None,
            ["Database mencatat tanggal pengiriman; packet persiapan baru tidak dibutuhkan."],
        )
    if http_status < 200 or http_status >= 300:
        return PrecheckResult(
            "MANUAL_REVIEW_REQUIRED", app_id, job_id, title, source, url, today.isoformat(),
            http_status, None, None, None, None,
            [f"Halaman sumber mengembalikan HTTP {http_status}; status lowongan tidak dapat dipastikan."],
        )

    title_match = title.lower() in text.lower()
    if not title_match:
        messages.append("Judul lowongan tidak ditemukan persis pada halaman sumber; pastikan URL sesuai aplikasi.")

    deadline_date = parse_deadline(text)
    days_left = (deadline_date - today).days if deadline_date else None
    all_country = detect_all_country_signal(text)

    if deadline_date is None:
        messages.append("Batas lamaran tidak ditemukan di halaman; verifikasi manual sebelum menyiapkan pengajuan.")
    elif days_left is not None and days_left < 0:
        messages.append(f"Batas lamaran sudah lewat pada {deadline_date.isoformat()}; jangan lanjutkan paket lamaran ini.")
        return PrecheckResult(
            "BLOCKED_EXPIRED", app_id, job_id, title, source, url, today.isoformat(),
            http_status, deadline_date.isoformat(), days_left, title_match, all_country, messages,
        )
    else:
        messages.append(f"Batas lamaran tercantum {deadline_date.isoformat()} ({days_left} hari dari tanggal pemeriksaan).")

    if all_country:
        messages.append("Halaman secara eksplisit menyatakan terbuka untuk kandidat dari semua negara.")
    else:
        messages.append("Pernyataan penerimaan semua negara tidak terdeteksi; lokasi dan izin kerja perlu diperiksa manual.")

    # This is only a pre-check, never a guarantee that visa/work-rights/time-zone details fit.
    if deadline_date is not None and days_left is not None and days_left >= 0 and all_country and title_match:
        messages.append("Hasil: lanjutkan ke pemeriksaan manual akhir. Ini bukan persetujuan visa atau jaminan diterima.")
        result = "READY_FOR_MANUAL_REVIEW"
    else:
        messages.append("Hasil: pemeriksaan manual diperlukan; jangan menganggap status kerja/lokasi sudah terverifikasi.")
        result = "MANUAL_REVIEW_REQUIRED"

    messages.append("Pemeriksaan ini hanya membaca halaman publik; tidak mengubah MySQL, tidak membuka formulir, dan tidak mengirim lamaran.")
    return PrecheckResult(
        result, app_id, job_id, title, source, url, today.isoformat(), http_status,
        deadline_date.isoformat() if deadline_date else None, days_left,
        title_match, all_country, messages,
    )


def get_application_row(application_id: int, profile_id: int) -> dict[str, Any] | None:
    """Reuse the handoff tool's parameterized, profile-scoped read-only query."""
    from prepare_application_packet import load_application  # type: ignore

    row = load_application(application_id, profile_id)
    return dict(row) if row else None


def run_precheck(application_id: int, profile_id: int, *, today: date | None = None, timeout: int = 15) -> PrecheckResult:
    row = get_application_row(application_id, profile_id)
    if not row:
        raise LookupError(f"Application ID {application_id} tidak ditemukan untuk Profile ID {profile_id}.")

    url = str(row.get("job_url") or "").strip()
    parsed = urlparse(url)
    checked_on = today or date.today()
    source = str(row.get("source_name") or "").lower()

    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in HIMALAYAS_HOSTS:
        return PrecheckResult(
            "MANUAL_REVIEW_REQUIRED", application_id, row.get("job_id", "unknown"),
            str(row.get("title") or "Unspecified"), str(row.get("source_name") or "Unspecified"),
            url, checked_on.isoformat(), None, None, None, None, None,
            ["Pemeriksaan langsung otomatis saat ini hanya mendukung URL HTTPS resmi Himalayas.",
             "Gunakan halaman sumber resmi untuk memeriksa deadline, negara yang diterima, dan persyaratan lokasi.",
             "Database tidak diubah dan lamaran tidak dikirim."],
        )

    try:
        http_status, final_url, text = fetch_himalayas_listing(url, timeout=timeout)
        row["job_url"] = final_url
        result = evaluate_listing(application_row=row, text=text, http_status=http_status, checked_on=checked_on)
        return result
    except urllib.error.HTTPError as exc:
        # Preserve the actual HTTP status (e.g. 403) instead of showing
        # "Not verified" when the server explicitly refused this read-only fetch.
        return PrecheckResult(
            "MANUAL_REVIEW_REQUIRED", application_id, row.get("job_id", "unknown"),
            str(row.get("title") or "Unspecified"), str(row.get("source_name") or "Unspecified"),
            url, checked_on.isoformat(), int(exc.code), None, None, None, None,
            [f"Halaman resmi menolak pemeriksaan otomatis: HTTP {exc.code} {exc.reason}.",
             "Buka halaman resmi secara manual. HTTP 403 bukan bukti lowongan kedaluwarsa.",
             "Database tidak diubah dan lamaran tidak dikirim."],
        )
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return PrecheckResult(
            "MANUAL_REVIEW_REQUIRED", application_id, row.get("job_id", "unknown"),
            str(row.get("title") or "Unspecified"), str(row.get("source_name") or "Unspecified"),
            url, checked_on.isoformat(), None, None, None, None, None,
            [f"Halaman resmi tidak dapat diverifikasi otomatis: {type(exc).__name__}: {exc}",
             "Buka halaman resmi secara manual. Jangan menafsirkan kegagalan fetch sebagai bukti lowongan kedaluwarsa.",
             "Database tidak diubah dan lamaran tidak dikirim."],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only listing expiry/location pre-check. Never submits an application.")
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--json", action="store_true", help="Print the result as JSON for later integration.")
    args = parser.parse_args(argv)

    try:
        result = run_precheck(args.application_id, args.profile_id, timeout=args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("AI JOB SEEKER — READ-ONLY ELIGIBILITY & EXPIRY PRE-CHECK")
        print("=" * 61)
        print(f"Application ID       : {result.application_id}")
        print(f"Job ID               : {result.job_id}")
        print(f"Role                 : {result.title}")
        print(f"Source               : {result.source}")
        print(f"Date checked         : {result.checked_on}")
        print(f"HTTP status          : {result.http_status if result.http_status is not None else 'Not verified'}")
        print(f"Application deadline : {result.deadline or 'Not detected'}")
        print(f"Days until deadline  : {result.days_until_deadline if result.days_until_deadline is not None else 'Unknown'}")
        print(f"Title matched        : {result.title_match if result.title_match is not None else 'Unknown'}")
        print(f"All-country signal   : {result.open_to_all_countries_signal if result.open_to_all_countries_signal is not None else 'Unknown'}")
        print(f"RESULT               : {result.result}")
        print("\nNOTES")
        for item in result.messages:
            print(f"- {item}")
        print("\nMySQL writes : 0")
        print("Form filling : No")
        print("Submissions  : 0")

    if result.result == "BLOCKED_EXPIRED":
        return 2
    if result.result in {"BLOCKED_NOT_APPROVED", "BLOCKED_ALREADY_SUBMITTED"}:
        return 4
    if result.result == "READY_FOR_MANUAL_REVIEW":
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
