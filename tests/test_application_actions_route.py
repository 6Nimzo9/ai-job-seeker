"""Offline route tests for explicit Application Review status changes.

A fake in-memory connector is used; no live MySQL database is contacted.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import secrets
import types
import unittest

try:
    import flask  # noqa: F401
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("Flask belum terpasang di lingkungan pengujian; route tests dilewati.") from exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STORE = {}


def reset_store(status="pending_approval", applied_at=None):
    STORE.clear()
    STORE.update({
        "id": 1, "job_id": 59, "profile_id": 1,
        "application_status": status, "applied_at": applied_at,
        "cover_letter": "Dear Hiring Manager,\n\nThank you for considering my application.",
        "notes": "Draft only; not submitted.",
        "match_score": 90.0, "match_reason": "Human review required.",
        "title": "Workflow Automation Engineer", "company_name": "KDCI Outsourcing",
        "country": "International Remote", "location": "Worldwide",
        "job_url": "https://example.com/jobs/59", "eligibility_status": "worldwide_claimed",
    })


class FakeCursor:
    def __init__(self):
        self.one = None
        self.many = []
        self.rowcount = -1

    def execute(self, sql, params=()):
        self.one, self.many, self.rowcount = None, [], -1
        normalized = " ".join(sql.split()).lower()

        # Dashboard initializes these tables during page rendering.
        # The fake database acknowledges DDL without creating real tables.
        if normalized.startswith("create table if not exists job_review_queue"):
            return
        elif normalized.startswith("create table if not exists source_run_logs"):
            return
        elif normalized.startswith(
            "select id, profile_name, target_roles, skills, preferred_countries"
        ):
            self.one = {
                "id": 1,
                "profile_name": params[0] if params else "Remote Automation Engineer",
                "target_roles": [
                    "AI Automation Engineer",
                    "n8n Workflow Architect",
                    "Python Automation Developer",
                    "Business Process Automation",
                    "Workflow Automation Engineer",
                ],
                "skills": [
                    "python", "n8n", "power automate", "sql",
                    "api integration", "workflow automation",
                    "business process automation", "artificial intelligence",
                ],
                "preferred_countries": [
                    "Australia", "Singapore", "Worldwide remote"
                ],
                "preferred_workplace_types": ["Remote"],
                "minimum_salary": "",
                "salary_currency": "AUD",
                "cv_path": str(ROOT / "cv" / "CV_English.pdf"),
            }
        elif "select id from user_profile" in normalized:
            self.one = {"id": 1}
        elif "count(distinct j.id) as total_jobs" in normalized:
            self.one = {
                "total_jobs": 75, "high_match": 1, "new_jobs": 0,
                "needs_review": 1, "location_restrictions": 0,
                "worldwide_claimed": 1, "location_not_matched": 0,
            }
        elif "select distinct jm.eligibility_status" in normalized:
            self.many = [{"eligibility_status": "worldwide_claimed"}]
        elif normalized.startswith("select name from job_sources"):
            self.many = []
        elif normalized.startswith("select s.name, s.source_url, s.source_type"):
            self.many = []
        elif normalized.startswith("select l.source_name, l.provider, l.source_type, l.run_status"):
            self.many = []
        elif normalized.startswith("select count(distinct j.id) as filtered_total"):
            self.one = {"filtered_total": 0}
        elif normalized.startswith("select job_id from job_review_queue"):
            self.many = []
        elif normalized.startswith("select job_id from applications where profile_id"):
            self.many = []
        elif normalized.startswith("select q.id as queue_id, q.job_id, q.created_at"):
            self.many = []
        elif normalized.startswith("select j.id,"):
            self.many = []
        elif "count(*) as total_applications" in normalized:
            status = STORE["application_status"]
            self.one = {
                "total_applications": 1,
                "pending_approval": int(status == "pending_approval"),
                "drafts_to_review": int(status in {"draft", "draft_review"}),
                "approved": int(status == "approved"),
                "archived": int(status == "archived"),
                "with_applied_at": int(STORE["applied_at"] is not None),
            }
        elif "select distinct application_status from applications" in normalized:
            self.many = [{"application_status": STORE["application_status"]}]
        elif "select a.id as application_id" in normalized:
            requested_status = params[1] if len(params) == 3 else ""
            if not requested_status or STORE["application_status"] == requested_status:
                self.many = [{
                    "application_id": STORE["id"], "job_id": STORE["job_id"],
                    "profile_id": STORE["profile_id"], "match_score": STORE["match_score"],
                    "match_reason": STORE["match_reason"],
                    "application_status": STORE["application_status"],
                    "cover_letter": STORE["cover_letter"], "applied_at": STORE["applied_at"],
                    "notes": STORE["notes"], "title": STORE["title"],
                    "company_name": STORE["company_name"], "country": STORE["country"],
                    "location": STORE["location"], "job_url": STORE["job_url"],
                    "eligibility_status": STORE["eligibility_status"],
                }]
        elif "select id, application_status, applied_at, cover_letter from applications" in normalized:
            if params == (1, 1):
                self.one = {
                    "id": STORE["id"], "application_status": STORE["application_status"],
                    "applied_at": STORE["applied_at"], "cover_letter": STORE["cover_letter"],
                }
        elif normalized.startswith("update applications"):
            new_status, note, _application_id, _profile_id, old_status = params
            if STORE["application_status"] != old_status or STORE["id"] != _application_id or STORE["profile_id"] != _profile_id:
                self.rowcount = 0
            else:
                STORE["application_status"] = new_status
                if "applied_at = current_timestamp" in normalized:
                    if STORE["applied_at"] is not None:
                        self.rowcount = 0
                        return
                    STORE["applied_at"] = datetime(2026, 10, 25, 9, 0, 0)
                STORE["notes"] += "\n" + note
                self.rowcount = 1
        else:
            raise AssertionError(f"Unhandled SQL in fake database: {sql}")

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def __enter__(self):
        # Mirror mysql.connector connection context-manager behavior so the
        # read-only dashboard query can use `with get_connection() as conn`.
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Do not suppress exceptions; always release the fake connection.
        self.close()
        return False

    def cursor(self, dictionary=False):
        return FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


fake_db = types.ModuleType("db")
fake_db.get_connection = FakeConnection
sys.modules.setdefault("db", fake_db)
import app as dashboard  # noqa: E402

# Deterministic, non-production signing key for isolated tests.
dashboard.app.secret_key = secrets.token_hex(32)
dashboard.get_connection = FakeConnection


class ApplicationActionRouteTests(unittest.TestCase):
    def setUp(self):
        reset_store()
        self.client = dashboard.app.test_client()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.csrf_token = session.get("_csrf_token")
            if not self.csrf_token:
                self.csrf_token = secrets.token_urlsafe(32)
                session["_csrf_token"] = self.csrf_token
        self.assertTrue(self.csrf_token)

    def post_action(self, action, **extra):
        data = {
            "csrf_token": self.csrf_token,
            "action": action,
            "return_app_status": "",
        }
        data.update(extra)
        return self.client.post("/applications/1/action", data=data, follow_redirects=True)

    def test_approve_changes_status_but_never_sets_applied_at(self):
        response = self.post_action("approve")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(STORE["application_status"], "approved")
        self.assertIsNone(STORE["applied_at"])
        self.assertIn("dashboard did not submit", STORE["notes"])
        self.assertIn(b"Draf disetujui", response.data)

    def test_archive_retains_the_database_record(self):
        response = self.post_action("archive")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(STORE["id"], 1)
        self.assertEqual(STORE["application_status"], "archived")
        self.assertIsNone(STORE["applied_at"])
        self.assertIn(b"Draf diarsipkan", response.data)

    def test_cannot_record_submission_without_checkbox_confirmation(self):
        STORE["application_status"] = "approved"
        response = self.post_action("mark_submitted")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(STORE["application_status"], "approved")
        self.assertIsNone(STORE["applied_at"])
        self.assertIn(b"Centang konfirmasi", response.data)

    def test_manual_submission_record_requires_approval_and_confirmation(self):
        STORE["application_status"] = "approved"
        response = self.post_action("mark_submitted", confirm_submitted="yes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(STORE["application_status"], "submitted")
        self.assertIsNotNone(STORE["applied_at"])
        self.assertIn("dashboard did not submit", STORE["notes"])
        self.assertIn(b"Pengiriman dicatat", response.data)

    def test_csrf_failure_does_not_change_status(self):
        response = self.client.post(
            "/applications/1/action",
            data={"csrf_token": "invalid", "action": "approve"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(STORE["application_status"], "pending_approval")
        self.assertIsNone(STORE["applied_at"])
        self.assertIn(b"token keamanan tidak valid", response.data)


if __name__ == "__main__":
    unittest.main()
