r"""Save one already-reviewed AI Job Seeker cover letter to MySQL.

This helper does not call Ollama, does not generate/edit the letter, and never
submits an application. It saves only after the user types exactly SAVE DRAFT.
Place beside db.py and run, for example:
    python .\save_reviewed_draft.py --job-id 59 --profile-id 1 --letter-file .\drafts\job_59.txt
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from db import get_connection

VERIFIED_ELIGIBILITY_STATUSES = {
    "eligible", "eligible_remote", "confirmed_eligible", "verified_eligible",
}


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", text))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save one reviewed cover-letter draft to MySQL; never submits it."
    )
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--letter-file", required=True,
                        help="UTF-8 text file containing the exact reviewed cover letter")
    args = parser.parse_args()

    letter_path = os.path.abspath(os.path.expanduser(args.letter_file))
    if not os.path.isfile(letter_path):
        print(f"ERROR: surat tidak ditemukan: {letter_path}")
        return 2
    try:
        with open(letter_path, "r", encoding="utf-8-sig") as file:
            letter = file.read().strip()
    except OSError as exc:
        print(f"ERROR membaca file surat: {exc}")
        return 2

    if not letter:
        print("ERROR: file surat kosong; tidak ada yang disimpan.")
        return 2
    if len(letter) > 12000:
        print("ERROR: surat terlalu panjang (>12000 karakter); tidak ada yang disimpan.")
        return 2

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT j.id AS job_id, j.title, j.job_url,
                      COALESCE(j.status, 'open') AS job_status,
                      jm.overall_score, jm.eligibility_status, jm.reasoning,
                      jm.scored_at
               FROM jobs j
               JOIN job_matches jm ON jm.job_id = j.id
               WHERE j.id = %s AND jm.profile_id = %s
               ORDER BY jm.scored_at DESC
               LIMIT 1""",
            (args.job_id, args.profile_id),
        )
        job = cur.fetchone()
        if not job:
            print("ERROR: job/profile tidak ditemukan di job_matches. Tidak ada perubahan.")
            return 3
        if str(job.get("job_status") or "open").strip().lower() == "closed":
            print("ERROR: lowongan berstatus closed. Tidak ada perubahan.")
            return 3

        cur.execute(
            "SELECT id, application_status FROM applications "
            "WHERE job_id = %s AND profile_id = %s LIMIT 1",
            (args.job_id, args.profile_id),
        )
        existing = cur.fetchone()
        if existing:
            print(
                f"DITOLAK: kombinasi job/profile sudah tersimpan sebagai "
                f"applications.id={existing['id']} "
                f"(status={existing.get('application_status')}). Tidak ada perubahan."
            )
            return 4

        eligibility = str(job.get("eligibility_status") or "").strip().lower()
        app_status = (
            "draft_review" if eligibility in VERIFIED_ELIGIBILITY_STATUSES
            else "pending_approval"
        )
        match_score = float(job.get("overall_score") or 0)
        title = str(job.get("title") or "(tanpa judul)")
        url = str(job.get("job_url") or "")
        word_count = count_words(letter)

        print("\nAI JOB SEEKER — REVIEWED DRAFT SAVE")
        print("=" * 48)
        print("Aksi       : simpan draf ke MySQL saja")
        print("Pengiriman : TIDAK dilakukan")
        print(f"Job ID     : {args.job_id}")
        print(f"Profile ID : {args.profile_id}")
        print(f"Judul      : {title}")
        print(f"Skor       : {match_score:.2f}/100")
        print(f"Eligibility: {eligibility or 'unknown'}")
        print(f"Status baru: {app_status}")
        print(f"Panjang    : {word_count} kata")
        print(f"Surat      : {letter_path}")
        print(f"URL        : {url}")
        print("\nPastikan file berisi surat yang sudah kamu tinjau dan setujui.")
        confirmation = input("Ketik SAVE DRAFT untuk menyimpan tepat surat ini: ").strip()
        if confirmation != "SAVE DRAFT":
            print("Dibatalkan. Tidak ada perubahan pada database.")
            return 0

        match_reason = (
            f"Reviewed draft only; match score {match_score:.2f}/100; "
            f"eligibility status: {eligibility or 'unknown'}. "
            "Human approval required; application not submitted."
        )
        notes = (
            f"Saved from reviewed local text file. URL: {url}. "
            "Draft only; not submitted. Verify work-rights eligibility and official application route."
        )
        cur.execute(
            """INSERT INTO applications
               (job_id, profile_id, match_score, match_reason, application_status,
                cover_letter, applied_at, notes)
               VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)""",
            (args.job_id, args.profile_id, match_score, match_reason,
             app_status, letter, notes),
        )
        application_id = int(cur.lastrowid)
        conn.commit()

        cur.execute(
            """SELECT id, job_id, profile_id, application_status, applied_at
               FROM applications WHERE id = %s""",
            (application_id,),
        )
        saved = cur.fetchone()
        if not saved:
            raise RuntimeError("Insert tidak dapat diverifikasi setelah commit.")
        print("\nBERHASIL: draf tersimpan dan diverifikasi.")
        print(f"applications.id : {saved['id']}")
        print(f"job_id          : {saved['job_id']}")
        print(f"profile_id      : {saved['profile_id']}")
        print(f"status          : {saved['application_status']}")
        print("applied_at      : NULL (belum dikirim)")
        print("Submissions     : 0")
        return 0
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("Transaksi dibatalkan jika belum berhasil commit. Periksa status di database sebelum mengulang.")
        return 1
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
