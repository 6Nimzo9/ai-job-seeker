"""Prepare a local, human-reviewed application packet from an approved MySQL draft.

This tool does NOT submit applications, log in to job boards, or alter MySQL.
It only reads an approved application and writes a small local folder containing
its cover letter, a job brief, and a human application checklist.

Place this file next to the project's existing db.py and run, for example:
    python .\\prepare_application_packet.py --application-id 1 --profile-id 1 --open-listing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ALLOWED_PREPARE_STATUSES = {"approved"}
NOT_SUBMITTED_STATUSES = {"submitted", "applied"}
LOCATION_META_SPLIT = re.compile(
    r";\s*(?=(?:Timezone restrictions:|Title indicates|Description indicates))",
    re.IGNORECASE,
)


def concise_location(value: Any) -> tuple[str, str]:
    raw = str(value or "Unspecified").strip()
    parts = LOCATION_META_SPLIT.split(raw, maxsplit=1)
    display = parts[0].strip().rstrip(";") or "Unspecified"
    details = parts[1].strip() if len(parts) > 1 else ""
    return display, details


def validate_application(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Validate a database row for a preparation packet; return URL and warnings."""
    app_id = row.get("application_id", row.get("id", "unknown"))
    status = str(row.get("application_status") or "").strip().lower()
    if status not in ALLOWED_PREPARE_STATUSES:
        if status in NOT_SUBMITTED_STATUSES or row.get("applied_at") is not None:
            raise ValueError(
                f"Application {app_id} sudah tercatat terkirim; packet baru tidak dibuat."
            )
        raise ValueError(
            f"Application {app_id} berstatus '{status or 'unknown'}'. "
            "Setujui draf terlebih dahulu di dashboard."
        )

    letter = str(row.get("cover_letter") or "").strip()
    if not letter:
        raise ValueError(f"Application {app_id} tidak mempunyai cover letter.")
    if row.get("applied_at") is not None:
        raise ValueError(
            f"Application {app_id} memiliki applied_at; packet hanya untuk draf yang belum dikirim."
        )

    url = str(row.get("job_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("URL lowongan kosong atau tidak valid; packet tidak dibuat.")
    if parsed.username or parsed.password:
        raise ValueError("URL lowongan tidak boleh memuat username/password.")

    warnings: list[str] = []
    eligibility = str(row.get("eligibility_status") or "").strip().lower()
    if eligibility in {"worldwide_claimed", "australia_listed", "singapore_listed", "remote_eligibility_unverified", "location_restriction_review", "location_not_matched", "review_required"}:
        warnings.append(
            "Verifikasi negara yang diterima, izin kerja/visa, pembatasan lokasi, dan zona waktu pada sumber resmi. "
            "Label lokasi dari sumber bukan konfirmasi kelayakan pelamar."
        )
    else:
        warnings.append("Tetap pastikan lokasi dan hak kerja sesuai dengan lowongan resmi.")

    if "himalayas.app" in parsed.netloc.lower() or "himalayas" in str(row.get("source_name") or "").lower():
        warnings.append(
            "Sumber Himalayas: buka halaman lowongan dan ikuti Apply route yang disediakan sumber. "
            "Jika memakai Himalayas API, ketentuan API menyatakan setiap Apply action harus memakai applicationLink dari API."
        )
    if parsed.scheme == "http":
        warnings.append("Tautan memakai HTTP, bukan HTTPS. Periksa kembali keaslian dan keamanan alamat sebelum membuka.")
    return url, warnings


def load_application(application_id: int, profile_id: int) -> dict[str, Any] | None:
    """Read one application's record for the profile using the project's db.py."""
    # Delayed import keeps validation and file-writing tests independent of MySQL.
    from db import get_connection  # type: ignore

    conn = get_connection()
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                a.id AS application_id,
                a.job_id,
                a.profile_id,
                a.match_score,
                a.application_status,
                a.cover_letter,
                a.applied_at,
                a.notes,
                j.title,
                COALESCE(c.name, 'Unknown company') AS company_name,
                COALESCE(s.name, '') AS source_name,
                COALESCE(j.country, '') AS country,
                COALESCE(j.location, 'Unspecified') AS location,
                COALESCE(j.workplace_type, '') AS workplace_type,
                COALESCE(j.employment_type, '') AS employment_type,
                j.job_url,
                p.profile_name,
                p.cv_path,
                jm.eligibility_status
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN companies c ON c.id = j.company_id
            LEFT JOIN job_sources s ON s.id = j.source_id
            JOIN user_profile p ON p.id = a.profile_id
            LEFT JOIN job_matches jm
                ON jm.job_id = a.job_id AND jm.profile_id = a.profile_id
            WHERE a.id = %s AND a.profile_id = %s
            LIMIT 1
            """,
            (application_id, profile_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def build_brief(row: Mapping[str, Any], url: str, warnings: list[str], cv_exists: bool) -> str:
    location, location_details = concise_location(row.get("location"))
    lines = [
        f"# Application preparation — ID {row.get('application_id')}",
        "",
        "> Preparation only. This tool did not submit an application or change the database.",
        "",
        "## Job details",
        f"- **Role:** {row.get('title') or 'Unspecified'}",
        f"- **Company:** {row.get('company_name') or 'Unknown company'}",
        f"- **Job ID:** {row.get('job_id')}",
        f"- **Application ID:** {row.get('application_id')}",
        f"- **Source:** {row.get('source_name') or 'Unspecified'}",
        f"- **Country:** {row.get('country') or 'Unspecified'}",
        f"- **Location:** {location}",
        f"- **Workplace type:** {row.get('workplace_type') or 'Unspecified'}",
        f"- **Employment type:** {row.get('employment_type') or 'Unspecified'}",
        f"- **Match score:** {row.get('match_score') if row.get('match_score') is not None else 'Unscored'}/100",
        f"- **Review status:** {row.get('application_status')}",
        f"- **Recorded submission date:** {row.get('applied_at') or 'None — not submitted'}",
        f"- **CV file exists locally:** {'Yes' if cv_exists else 'No / path unavailable'}",
        f"- **CV path from profile:** {row.get('cv_path') or 'Not configured'}",
        f"- **Job listing URL:** {url}",
    ]
    if location_details:
        lines += ["", "## Location metadata from source", location_details]
    lines += ["", "## Checks before submission"]
    lines += [f"- [ ] {warning}" for warning in warnings]
    lines += [
        "- [ ] Read the full job description and confirm the role is still open.",
        "- [ ] Confirm the country of residence and work-rights/visa conditions.",
        "- [ ] Open the official application route from the job listing.",
        "- [ ] Confirm the attached CV is the intended English CV and is up to date.",
        "- [ ] Review `cover_letter.txt` and ensure every claim is accurate.",
        "- [ ] Complete any employer-specific questions truthfully.",
        "- [ ] Personally review the completed form before pressing Submit.",
        "- [ ] After the official site confirms submission, return to the dashboard and record it using the confirmation checkbox.",
        "",
        "## Safety",
        "This packet is a preparation aid only. It does not log in, fill forms, click Apply/Submit, or send anything.",
        "Do not mark an application as submitted unless the official site actually confirmed submission.",
        "",
    ]
    return "\n".join(lines)


def _write_cover_letter_pdf(pdf_path: str | Path, letter: str) -> None:
    """Write a readable A4 PDF copy of a cover letter using ReportLab."""
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    if not str(letter or "").strip():
        raise ValueError("Cover letter is empty; PDF was not created.")

    destination = Path(pdf_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    paragraph_style = ParagraphStyle(
        "CoverLetterBody",
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        spaceAfter=8,
        allowWidows=0,
        allowOrphans=0,
    )

    story = []
    for block in str(letter).strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        safe_text = escape(block).replace("\n", "<br/>")
        story.append(Paragraph(safe_text, paragraph_style))
        story.append(Spacer(1, 3))

    if not story:
        raise ValueError("Cover letter contains no readable text; PDF was not created.")

    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        leftMargin=2.25 * cm,
        rightMargin=2.25 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title="Cover Letter",
        author="AI Job Seeker",
    )
    document.build(story)


def prepare_packet(
    row: Mapping[str, Any],
    output_root: str | Path,
    *,
    copy_cv: bool = False,
) -> Path:
    """Write a local packet for an approved, not-yet-submitted draft."""
    url, warnings = validate_application(row)
    app_id = int(row.get("application_id", row.get("id")))
    packet_dir = Path(output_root).expanduser().resolve() / f"application_{app_id}"
    packet_preexisting = packet_dir.exists()
    if packet_preexisting:
        if not packet_dir.is_dir() or any(
            not child.is_file() or child.name not in {"cover_letter.txt", "cover_letter.pdf"}
            for child in packet_dir.iterdir()
        ):
            raise FileExistsError(
                f"Folder sudah berisi paket atau file lain: {packet_dir}. "
                "Untuk menghindari menimpa hasil lama, pilih --output-dir baru atau pindahkan folder lama secara manual."
            )
    cv_path_raw = str(row.get("cv_path") or "").strip()
    cv_path = Path(os.path.expandvars(os.path.expanduser(cv_path_raw))) if cv_path_raw else None
    cv_exists = bool(cv_path and cv_path.is_file())
    letter = str(row.get("cover_letter") or "").strip()
    brief = build_brief(row, url, warnings, cv_exists)

    # Validate fully before writing; reuse only a letter-only partial folder.
    if not packet_preexisting:
        packet_dir.mkdir(parents=True, exist_ok=False)
    try:
        cover_text_path = packet_dir / "cover_letter.txt"
        if not cover_text_path.exists():
            cover_text_path.write_text(letter + "\n", encoding="utf-8")
        from cover_letter_pdf import build_cover_letter_pdf
        cover_pdf_path = packet_dir / "cover_letter.pdf"
        if not cover_pdf_path.exists():
            build_cover_letter_pdf(
                cover_pdf_path, letter,
                job_title=str(row.get("title") or ""),
                company_name=str(row.get("company_name") or ""),
            )
        _write_cover_letter_pdf(packet_dir / "cover_letter.pdf", letter)
        (packet_dir / "application_brief.md").write_text(brief, encoding="utf-8")
        manifest = {
            "application_id": app_id,
            "job_id": int(row.get("job_id")),
            "profile_id": int(row.get("profile_id")),
            "title": str(row.get("title") or ""),
            "company_name": str(row.get("company_name") or ""),
            "source_name": str(row.get("source_name") or ""),
            "match_score": row.get("match_score"),
            "application_status": str(row.get("application_status") or ""),
            "applied_at": str(row.get("applied_at") or ""),
            "job_url": url,
            "preparation_only": True,
            "submission_performed": False,
            "prepared_at_local_date": date.today().isoformat(),
        }
        (packet_dir / "application_data.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        if copy_cv:
            if not cv_exists or cv_path is None:
                raise FileNotFoundError(
                    "--copy-cv dipilih tetapi file CV tidak ditemukan dari cv_path di user_profile. "
                    "Periksa cv_path atau jalankan ulang tanpa --copy-cv."
                )
            shutil.copy2(cv_path, packet_dir / cv_path.name)
    except Exception:
        # Only remove a folder created by this run. Keep pre-existing letter text/PDF intact.
        if not packet_preexisting:
            shutil.rmtree(packet_dir, ignore_errors=True)
        raise
    return packet_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an application packet from an approved draft; never submits applications."
    )
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--output-dir", default="application_packets")
    parser.add_argument("--open-listing", action="store_true", help="Open the job listing in the default browser; does not apply or submit.")
    parser.add_argument("--copy-cv", action="store_true", help="Copy the profile CV into the packet folder (opt-in; contains personal data).")
    args = parser.parse_args(argv)

    try:
        row = load_application(args.application_id, args.profile_id)
        if row is None:
            print("ERROR: Application ID tidak ditemukan untuk profile ID tersebut.", file=sys.stderr)
            return 2
        output = prepare_packet(row, args.output_dir, copy_cv=args.copy_cv)
        url, warnings = validate_application(row)
        print("AI JOB SEEKER — APPLICATION HANDOFF PACKET")
        print("=" * 48)
        print(f"Application ID : {row.get('application_id')}")
        print(f"Job ID         : {row.get('job_id')}")
        print(f"Role           : {row.get('title')}")
        print(f"Company        : {row.get('company_name')}")
        print(f"Status         : {row.get('application_status')} (approved; not submitted)")
        print(f"Packet folder  : {output}")
        print("Files          : cover_letter.txt, cover_letter.pdf, application_brief.md, application_data.json")
        print(f"CV copied      : {'Yes' if args.copy_cv else 'No (opt-in with --copy-cv)'}")
        print("Submission     : NOT performed")
        if warnings:
            print("\nCHECK BEFORE APPLYING:")
            for warning in warnings:
                print(f"- {warning}")
        if args.open_listing:
            opened = webbrowser.open(url)
            print(f"\nOpened job listing in browser: {'yes' if opened else 'request sent to browser; verify manually'}")
            print("Browser automation, form filling, and submission are not performed.")
        return 0
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: could not prepare packet: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
