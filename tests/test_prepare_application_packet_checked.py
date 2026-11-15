import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

import application_eligibility_precheck as precheck
from prepare_application_packet_checked import decide_manual_review


class ManualReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "automatic_result": "MANUAL_REVIEW_REQUIRED",
            "deadline_text": "2026-12-21",
            "listing_open_confirmed": True,
            "country_eligible_confirmed": True,
            "restrictions_checked": True,
            "today": date(2026, 10, 25),
        }

    def test_valid_user_confirmed_review_allows_packet(self):
        allowed, reason, deadline = decide_manual_review(**self.base)
        self.assertTrue(allowed)
        self.assertEqual(deadline, date(2026, 12, 21))
        self.assertIn("manual", reason.lower())

    def test_expired_deadline_blocks(self):
        args = dict(self.base, deadline_text="2026-10-20")
        allowed, reason, deadline = decide_manual_review(**args)
        self.assertFalse(allowed)
        self.assertEqual(deadline, date(2026, 10, 20))
        self.assertIn("sudah lewat", reason.lower())

    def test_unknown_or_invalid_deadline_blocks(self):
        for value in ("", "UNKNOWN", "21 Dec 2026", "2026-13-40"):
            with self.subTest(value=value):
                allowed, _, _ = decide_manual_review(**dict(self.base, deadline_text=value))
                self.assertFalse(allowed)

    def test_closed_listing_blocks(self):
        allowed, _, _ = decide_manual_review(**dict(self.base, listing_open_confirmed=False))
        self.assertFalse(allowed)

    def test_country_not_confirmed_blocks(self):
        allowed, _, _ = decide_manual_review(**dict(self.base, country_eligible_confirmed=False))
        self.assertFalse(allowed)

    def test_location_and_work_rights_not_checked_blocks(self):
        allowed, _, _ = decide_manual_review(**dict(self.base, restrictions_checked=False))
        self.assertFalse(allowed)

    def test_automatic_expiry_cannot_be_overridden(self):
        allowed, reason, _ = decide_manual_review(**dict(self.base, automatic_result="BLOCKED_EXPIRED"))
        self.assertFalse(allowed)
        self.assertIn("BLOCKED_EXPIRED", reason)

    def test_unapproved_and_submitted_states_cannot_be_overridden(self):
        for status in ("BLOCKED_NOT_APPROVED", "BLOCKED_ALREADY_SUBMITTED"):
            with self.subTest(status=status):
                allowed, _, _ = decide_manual_review(**dict(self.base, automatic_result=status))
                self.assertFalse(allowed)

    def test_deadline_today_is_allowed_if_all_confirmations_hold(self):
        args = dict(self.base, deadline_text="2026-10-25", today=date(2026, 10, 25))
        allowed, _, deadline = decide_manual_review(**args)
        self.assertTrue(allowed)
        self.assertEqual(deadline, date(2026, 10, 25))

    def test_http_403_is_reported_as_manual_review_with_status_preserved(self):
        row = {
            "application_id": 1, "job_id": 59, "title": "Workflow Automation Engineer",
            "source_name": "Himalayas",
            "job_url": "https://himalayas.app/companies/kdci/jobs/workflow-automation-engineer?utm_source=api",
            "application_status": "approved", "applied_at": None,
        }
        error = urllib.error.HTTPError(row["job_url"], 403, "Forbidden", None, None)
        with patch.object(precheck, "get_application_row", return_value=row), \
             patch.object(precheck, "fetch_himalayas_listing", side_effect=error):
            result = precheck.run_precheck(1, 1, today=date(2026, 10, 25))
        self.assertEqual(result.http_status, 403)
        self.assertEqual(result.result, "MANUAL_REVIEW_REQUIRED")
        self.assertTrue(any("HTTP 403" in message for message in result.messages))

    def test_deadline_and_all_countries_can_be_parsed_when_page_is_readable(self):
        from application_eligibility_precheck import parse_deadline, detect_all_country_signal
        self.assertEqual(parse_deadline("Apply before Dec 21, 2026"), date(2026, 12, 21))
        self.assertTrue(detect_all_country_signal("Open to candidates from all countries."))


if __name__ == "__main__":
    unittest.main()
