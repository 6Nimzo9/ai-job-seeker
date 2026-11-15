r"""
AI Job Seeker — Priority Application Queue
Safe first-stage module: it queues applications but NEVER submits them to job sites.

Usage:
    python .\priority_queue.py                 # dry run, no database writes
    python .\priority_queue.py --commit        # insert queue rows into applications

Assumptions:
- Existing db.py exports get_connection()
- Existing tables: jobs, job_matches, user_profile, applications
- Profile ID defaults to 1 (Remote Automation Engineer)
"""

import argparse
import sys
from typing import Any

from db import get_connection

DEFAULT_PROFILE_ID = 1
DEFAULT_MIN_SCORE = 70.0

# Conservative allowlist. Any other status is treated as needing human review.
CONFIRMED_ELIGIBILITY_STATUSES = {
    "eligible",
    "eligible_remote",
    "confirmed_eligible",
    "verified_eligible",
}

def normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def location_priority(country: Any, location: Any) -> int:
    country_text = str(country or "").strip().lower()
    location_text = str(location or "").strip().lower()
    text = f"{country_text} {location_text}"

    # Priority 1: Australia
    if "australia" in text or "australian" in text:
        return 1

    # Priority 2: Singapore
    if "singapore" in text:
        return 2

    # Priority 3: explicitly worldwide/international remote
    worldwide_terms = (
        "worldwide",
        "anywhere",
        "global remote",
        "remote worldwide",
        "international remote",
        "open to all countries",
        "anywhere in the world",
        "location unrestricted",
    )
    if any(term in text for term in worldwide_terms):
        return 3

    # Everything else is outside the active target queue.
    return 4


def determine_status(row: dict, min_score: float) -> tuple[str, str]:
    score = float(row.get("overall_score") or 0)
    eligibility = normalize(row.get("eligibility_status"))
    workplace = normalize(row.get("workplace_type"))
    country = str(row.get("country") or "")
    location = str(row.get("location") or "")
    title = str(row.get("title") or "")

    reasons = [f"Match score: {score:.1f}/100"]
    if score < min_score:
        reasons.append(f"Below minimum score ({min_score:.1f})")
        return "rejected", "; ".join(reasons)

    reasons.append(f"Target location priority: {location_priority(country, location)}")
    if workplace and workplace not in {"remote", "fully_remote", "remote_only", "work_from_home"}:
        reasons.append(f"Workplace type requires review: {row.get('workplace_type')}")
        return "pending_approval", "; ".join(reasons)

    if eligibility in CONFIRMED_ELIGIBILITY_STATUSES and workplace in {
        "remote", "fully_remote", "remote_only", "work_from_home"
    }:
        reasons.append("Eligibility marked confirmed by existing scoring data")
        reasons.append("Ready for human final check; no application is submitted by this script")
        return "ready_to_apply", "; ".join(reasons)

    reasons.append(f"Eligibility needs manual verification: {row.get('eligibility_status') or 'unknown'}")
    reasons.append("Verify country/work-rights requirements and the official application route")
    return "pending_approval", "; ".join(reasons)

def fetch_candidates(conn, profile_id: int, min_score: float) -> list[dict]:
    cur = conn.cursor()
    try:
        # Select only the latest match row for each job/profile.
        # Avoid relying on MySQL-specific JSON parsing or altering existing schema.
        cur.execute(
            """
            SELECT
                j.id AS job_id,
                j.title,
                j.country,
                j.location,
                j.workplace_type,
                j.job_url,
                j.description,
                jm.id AS match_id,
                jm.overall_score,
                jm.eligibility_status,
                jm.reasoning
            FROM jobs AS j
            JOIN job_matches AS jm
              ON jm.job_id = j.id
             AND jm.profile_id = %s
            WHERE NOT EXISTS (
                SELECT 1
                FROM job_matches AS newer
                WHERE newer.job_id = jm.job_id
                  AND newer.profile_id = jm.profile_id
                  AND (
                      newer.scored_at > jm.scored_at
                      OR (newer.scored_at = jm.scored_at AND newer.id > jm.id)
                  )
            )
              AND NOT EXISTS (
                SELECT 1
                FROM applications AS a
                WHERE a.job_id = j.id
                  AND a.profile_id = %s
            )
              AND COALESCE(j.status, 'new') <> 'closed'
              AND COALESCE(jm.overall_score, 0) >= %s
            ORDER BY
                CASE
                    WHEN LOCATE('australia', LOWER(COALESCE(j.country, ''))) > 0 THEN 1
                    WHEN LOCATE('singapore', LOWER(COALESCE(j.country, ''))) > 0 THEN 2
                    WHEN LOCATE('australia', LOWER(COALESCE(j.location, ''))) > 0 THEN 1
                    WHEN LOCATE('singapore', LOWER(COALESCE(j.location, ''))) > 0 THEN 2
                    WHEN LOWER(COALESCE(j.workplace_type, '')) IN
                        ('remote', 'fully_remote', 'remote_only', 'work_from_home')
                        THEN 3
                    ELSE 4
                END ASC,
                jm.overall_score DESC,
                j.discovered_at DESC
            """,
            (profile_id, profile_id, min_score),
        )
        columns = [item[0] for item in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()

def insert_application(conn, row: dict, profile_id: int, status: str, reason: str) -> None:
    cur = conn.cursor()
    try:
        # Double-check for duplicates at write time.
        cur.execute(
            """
            SELECT id FROM applications
            WHERE job_id = %s AND profile_id = %s
            LIMIT 1
            """,
            (row["job_id"], profile_id),
        )
        if cur.fetchone():
            return

        original_reason = str(row.get("reasoning") or "").strip()
        combined_reason = reason
        if original_reason:
            combined_reason += f"; Existing scoring note: {original_reason[:1200]}"

        cur.execute(
            """
            INSERT INTO applications
                (job_id, profile_id, match_score, match_reason,
                 application_status, cover_letter, applied_at, notes)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s)
            """,
            (
                row["job_id"],
                profile_id,
                float(row.get("overall_score") or 0),
                combined_reason[:6000],
                status,
                "Created by priority_queue.py. No application was submitted. "
                f"Job URL: {row.get('job_url') or ''}"[:2000],
            ),
        )
    finally:
        cur.close()

def main() -> int:
    parser = argparse.ArgumentParser(description="Build a safe AI Job Seeker application queue.")
    parser.add_argument("--profile-id", type=int, default=DEFAULT_PROFILE_ID)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--commit", action="store_true", help="Write queue rows to applications table.")
    args = parser.parse_args()

    if not 0 <= args.min_score <= 100:
        print("ERROR: --min-score must be between 0 and 100.")
        return 2

    conn = None
    try:
        conn = get_connection()
        candidates = fetch_candidates(conn, args.profile_id, args.min_score)
        candidates = [
            row for row in candidates
            if location_priority(row.get("country"), row.get("location")) <= 3
        ]

        candidates.sort(
            key=lambda row: (
                location_priority(row.get("country"), row.get("location")),
                -float(row.get("overall_score") or 0),
                str(row.get("title") or "").lower(),
            )
        )

        print(f"Profile ID : {args.profile_id}")
        print(f"Minimum score: {args.min_score:.1f}")
        print(f"Mode       : {'COMMIT (database writes enabled)' if args.commit else 'DRY RUN (no database writes)'}")
        print(f"Candidates : {len(candidates)}")
        print("-" * 100)

        planned = []
        for row in candidates:
            status, reason = determine_status(row, args.min_score)
            planned.append((row, status, reason))
            print(f"[{status.upper():16}] score={float(row.get('overall_score') or 0):5.1f} "
                  f"| {row.get('country') or row.get('location') or 'Location unknown'} "
                  f"| {row.get('title')}")
            print(f"  URL: {row.get('job_url') or '(no URL)'}")
            print(f"  Reason: {reason}")

        if not args.commit:
            print("\nDry run complete. No rows were inserted.")
            print("Review the output, then rerun with --commit to create queue entries.")
            return 0

        inserted = 0
        try:
            for row, status, reason in planned:
                # Low-score entries are excluded by SQL; 'rejected' is a defensive guard.
                if status == "rejected":
                    continue
                before = conn.affected_rows() if hasattr(conn, "affected_rows") else None
                insert_application(conn, row, args.profile_id, status, reason)
                # Count is approximate only if connector does not expose affected_rows.
                inserted += 1
            conn.commit()
            print(f"\nCommit complete. Queue processing attempted for {inserted} candidates.")
            print("IMPORTANT: this script only creates queue rows; it does not open websites or submit applications.")
            return 0
        except Exception:
            conn.rollback()
            raise
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("No successful commit was confirmed. Do not run repeatedly until the error is reviewed.")
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

if __name__ == "__main__":
    raise SystemExit(main())
