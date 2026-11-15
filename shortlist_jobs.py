"""Read-only shortlist from the existing AI Job Seeker MySQL database.

Uses the project's existing db.get_connection() helper. Does not modify data.
"""
from __future__ import annotations

from typing import Any

from db import get_connection

PROFILE_ID = 1
MIN_SCORE = 40
LIMIT = 30

SQL = """
SELECT
    j.id AS job_id,
    j.title,
    c.name AS company,
    s.name AS source,
    j.country,
    j.location,
    jm.overall_score,
    jm.skill_score,
    jm.role_score,
    jm.eligibility_status,
    j.job_url
FROM jobs AS j
JOIN job_matches AS jm
    ON jm.job_id = j.id
JOIN (
    SELECT job_id, profile_id, MAX(scored_at) AS latest_scored_at
    FROM job_matches
    WHERE profile_id = %s
    GROUP BY job_id, profile_id
) AS latest
    ON latest.job_id = jm.job_id
   AND latest.profile_id = jm.profile_id
   AND latest.latest_scored_at = jm.scored_at
LEFT JOIN companies AS c
    ON c.id = j.company_id
LEFT JOIN job_sources AS s
    ON s.id = j.source_id
WHERE jm.profile_id = %s
  AND jm.overall_score >= %s
  AND LOWER(COALESCE(j.status, 'new')) <> 'closed'
  AND LOCATE('kdci', LOWER(COALESCE(c.name, ''))) = 0
  AND (
       LOCATE('automation', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('workflow', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('integration', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('python', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('n8n', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('power automate', LOWER(COALESCE(j.title, ''))) > 0
    OR LOCATE('business process', LOWER(COALESCE(j.title, ''))) > 0
  )
  AND NOT EXISTS (
      SELECT 1
      FROM applications AS a
      WHERE a.job_id = j.id
        AND a.profile_id = jm.profile_id
  )
ORDER BY
    CASE
      WHEN LOCATE('australia', LOWER(CONCAT(COALESCE(j.country, ''), ' ', COALESCE(j.location, '')))) > 0 THEN 1
      WHEN LOCATE('singapore', LOWER(CONCAT(COALESCE(j.country, ''), ' ', COALESCE(j.location, '')))) > 0 THEN 2
      WHEN LOCATE('worldwide', LOWER(CONCAT(COALESCE(j.country, ''), ' ', COALESCE(j.location, '')))) > 0 THEN 3
      WHEN LOCATE('international remote', LOWER(CONCAT(COALESCE(j.country, ''), ' ', COALESCE(j.location, '')))) > 0 THEN 3
      ELSE 4
    END,
    jm.overall_score DESC,
    j.title ASC
LIMIT 30
"""


def main() -> int:
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        # Placeholders correspond to the latest-score profile, active profile, and minimum score.
        cur.execute(SQL, (PROFILE_ID, PROFILE_ID, MIN_SCORE))
        rows: list[dict[str, Any]] = cur.fetchall()

        print("AI JOB SEEKER — READ-ONLY SHORTLIST (NON-KDCI)")
        print("=" * 72)
        print(f"Profile ID: {PROFILE_ID} | Minimum score: {MIN_SCORE} | Results: {len(rows)}")
        print("Database writes: 0 | Applications submitted: 0")
        for row in rows:
            print("-" * 72)
            print(f"Job ID       : {row.get('job_id')}")
            print(f"Title        : {row.get('title') or 'Unknown'}")
            print(f"Company      : {row.get('company') or 'Unknown'}")
            print(f"Source       : {row.get('source') or 'Unknown'}")
            print(f"Country      : {row.get('country') or 'Unknown'}")
            print(f"Location     : {row.get('location') or 'Unknown'}")
            print(f"Overall score: {row.get('overall_score')}")
            print(f"Skill score  : {row.get('skill_score')}")
            print(f"Role score   : {row.get('role_score')}")
            print(f"Eligibility  : {row.get('eligibility_status') or 'Unknown'}")
            print(f"URL          : {row.get('job_url') or 'Unknown'}")
        if not rows:
            print("Tidak ada lowongan yang cocok dengan filter saat ini.")
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("Database tidak diubah; tidak ada lamaran yang dikirim.")
        return 1
    finally:
        if cur is not None:
            cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
