"""Prepare an application packet only after an eligibility/expiry gate.

The gate never submits an application. If the listing checker cannot read the
page (for example, HTTP 403), this script requires an explicit, interactive
manual verification of the exact listing, deadline, country eligibility and
location/work-rights/time-zone constraints before it creates any files.

Place beside db.py, prepare_application_packet.py, and
application_eligibility_precheck.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from application_eligibility_precheck import run_precheck
from prepare_application_packet import load_application, prepare_packet

TERMINAL_BLOCKS = {"BLOCKED_NOT_APPROVED", "BLOCKED_ALREADY_SUBMITTED", "BLOCKED_EXPIRED"}


def decide_manual_review(
    *,
    automatic_result: str,
    deadline_text: str,
    listing_open_confirmed: bool,
    country_eligible_confirmed: bool,
    restrictions_checked: bool,
    today: date | None = None,
) -> tuple[bool, str, date | None]:
    """Return (allowed, reason, parsed_deadline); never override hard blocks."""
    if automatic_result in TERMINAL_BLOCKS:
        return False, f"Pemeriksaan otomatis memblokir tahap ini: {automatic_result}.", None

    try:
        deadline = datetime.strptime(deadline_text.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False, "Tanggal deadline tidak valid/kosong. Gunakan YYYY-MM-DD dari halaman resmi.", None

    current_day = today or date.today()
    if deadline < current_day:
        return False, f"Deadline {deadline.isoformat()} sudah lewat; paket tidak dibuat.", deadline
    if not listing_open_confirmed:
        return False, "Status listing belum dikonfirmasi terbuka pada halaman resmi.", deadline
    if not country_eligible_confirmed:
        return False, "Penerimaan Indonesia/semua negara belum dikonfirmasi pada halaman resmi.", deadline
    if not restrictions_checked:
        return False, "Syarat lokasi, izin kerja/visa, dan zona waktu belum diperiksa.", deadline
    return True, "Pemeriksaan manual dikonfirmasi oleh pengguna; bukan verifikasi otomatis.", deadline


def _ask_yes(input_fn: Callable[[str], str], prompt: str) -> bool:
    return input_fn(prompt).strip().upper() == "YES"


def collect_manual_review(
    automatic_result: str,
    *,
    input_fn: Callable[[str], str] = input,
    today: date | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Collect explicit manual confirmations after displaying the automatic result."""
    print("\nAUTOMATIC PRE-CHECK DID NOT CONFIRM ALL FACTS.")
    print("A fetch error such as HTTP 403 is NOT proof that a listing is expired.")
    print("Open the saved listing URL in your browser and inspect the official page.")
    print("To proceed, confirm each item truthfully; any answer other than YES blocks packet creation.")

    listing_open = _ask_yes(
        input_fn,
        "1) Exact role/company match and official page currently shows an active Apply route? Type YES: ",
    )
    deadline_text = input_fn(
        "2) Deadline shown on the official listing (YYYY-MM-DD; do not guess): "
    ).strip()
    country_eligible = _ask_yes(
        input_fn,
        "3) Official page explicitly says all countries OR explicitly includes Indonesia? Type YES: ",
    )
    restrictions_checked = _ask_yes(
        input_fn,
        "4) You checked full location, work-rights/visa and timezone requirements and found no explicit disqualifier? Type YES: ",
    )
    allowed, reason, parsed_deadline = decide_manual_review(
        automatic_result=automatic_result,
        deadline_text=deadline_text,
        listing_open_confirmed=listing_open,
        country_eligible_confirmed=country_eligible,
        restrictions_checked=restrictions_checked,
        today=today,
    )
    record = {
        "result": "MANUAL_REVIEW_CONFIRMED" if allowed else "MANUAL_REVIEW_BLOCKED",
        "method": "interactive_user_confirmation",
        "checked_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "checked_on": (today or date.today()).isoformat(),
        "automatic_result": automatic_result,
        "deadline": parsed_deadline.isoformat() if parsed_deadline else None,
        "listing_open_confirmed_by_user": listing_open,
        "country_eligibility_confirmed_by_user": country_eligible,
        "location_visa_timezone_checked_by_user": restrictions_checked,
        "decision_reason": reason,
        "warning": "User-confirmed only; not independently verified by this script and not a visa/work-rights guarantee.",
    }
    return allowed, record


def _print_precheck(result: Any) -> None:
    print("AI JOB SEEKER — ELIGIBILITY GATE FOR APPLICATION PACKET")
    print("=" * 61)
    print(f"Application ID : {result.application_id}")
    print(f"Job ID         : {result.job_id}")
    print(f"Role           : {result.title}")
    print(f"Source         : {result.source}")
    print(f"Date checked   : {result.checked_on}")
    print(f"HTTP status    : {result.http_status if result.http_status is not None else 'Not verified'}")
    print(f"Deadline       : {result.deadline or 'Not detected'}")
    print(f"Result         : {result.result}")
    for message in result.messages:
        print(f"- {message}")
    print("Database writes: 0")
    print("Form filling   : No")
    print("Submissions    : 0")


def _record_manual_review(packet_dir: Path, record: dict[str, Any]) -> None:
    """Persist a local audit file and append a traceable note to the brief."""
    (packet_dir / "eligibility_precheck.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    brief_path = packet_dir / "application_brief.md"
    with brief_path.open("a", encoding="utf-8") as stream:
        stream.write("\n## Eligibility pre-check record\n")
        stream.write("- Result: MANUAL_REVIEW_CONFIRMED\n")
        stream.write(f"- Checked on: {record['checked_on']}\n")
        stream.write(f"- Deadline manually read from official listing: {record['deadline']}\n")
        stream.write("- Listing open/title/company match: user confirmed\n")
        stream.write("- Country eligibility: user confirmed from the official listing\n")
        stream.write("- Location/work-rights/visa/time-zone requirements: user confirms reviewed\n")
        stream.write("- Important: this is a user-entered manual check, not independent automated verification or a guarantee of eligibility.\n")

    manifest_path = packet_dir / "application_data.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["eligibility_precheck"] = record
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Eligibility-gated application packet preparation; never submits applications."
    )
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--output-dir", default="application_packets_checked")
    parser.add_argument("--copy-cv", action="store_true", help="Copy CV into the packet (personal data).")
    parser.add_argument("--open-listing", action="store_true", help="Open only the official listing in your browser.")
    args = parser.parse_args(argv)

    try:
        row = load_application(args.application_id, args.profile_id)
        if row is None:
            print("ERROR: Application ID tidak ditemukan untuk profile ID tersebut.", file=sys.stderr)
            return 2

        result = run_precheck(args.application_id, args.profile_id)
        _print_precheck(result)
        if result.result in TERMINAL_BLOCKS:
            print("\nBLOCKED: tidak ada paket yang dibuat dan tidak ada data database yang diubah.")
            return 2

        url = str(row.get("job_url") or "").strip()
        if args.open_listing:
            opened = webbrowser.open(url)
            print(f"\nOfficial listing opened: {'yes' if opened else 'browser open requested; verify manually'}")
            print("Periksa halaman yang terbuka sebelum menjawab konfirmasi manual berikutnya.")

        allowed, manual_record = collect_manual_review(result.result)
        if not allowed:
            print(f"\nBLOCKED: {manual_record['decision_reason']}")
            print("Packet created : No")
            print("Database writes: 0")
            print("Submissions    : 0")
            return 3

        packet_dir = prepare_packet(row, args.output_dir, copy_cv=args.copy_cv)
        try:
            _record_manual_review(packet_dir, manual_record)
        except Exception:
            # Do not leave a packet that lacks its requested review audit note.
            import shutil
            shutil.rmtree(packet_dir, ignore_errors=True)
            raise

        print("\nMANUAL REVIEW PASSED — PACKET CREATED")
        print(f"Packet folder  : {packet_dir}")
        print("Files          : cover_letter.txt, cover_letter.pdf, application_brief.md, application_data.json, eligibility_precheck.json")
        print(f"CV copied      : {'Yes' if args.copy_cv else 'No'}")
        print("Database writes: 0")
        print("Form filling   : No")
        print("Submissions    : 0")
        print("No form was filled and no submission was performed.")
        return 0
    except (ValueError, FileExistsError, FileNotFoundError, OSError, LookupError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: could not prepare checked packet: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
