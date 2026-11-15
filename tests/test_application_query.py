"""Offline tests for Application Review SQL construction and template rendering.

These tests do not require a live MySQL connection or a Flask server. They use
AST extraction for the small pure query builder and Jinja2 to compile/render the
HTML template with sample database-shaped rows.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


def load_application_query_builder():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)
    fn = next(
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_application_query"
    )
    isolated_module = ast.Module(body=[fn], type_ignores=[])
    namespace = {"Any": Any, "PAGE_SIZE": 100}
    exec(compile(isolated_module, str(ROOT / "app.py"), "exec"), namespace)
    return namespace["build_application_query"]


def load_location_splitter():
    import math

    source = (ROOT / "app.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)

    helper_names = {
        "_format_utc_offset",
        "_humanize_location_detail",
        "_split_location_metadata",
    }

    helpers = [
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef)
        and node.name in helper_names
    ]

    found_names = {node.name for node in helpers}

    if found_names != helper_names:
        raise RuntimeError(
            f"Missing location helpers: {helper_names - found_names}"
        )

    namespace = {
        "Any": Any,
        "math": math,
        "re": re,
        "LOCATION_META_SPLIT": re.compile(
            r";\s*(?=(?:Timezone restrictions:|Title indicates|Description indicates))",
            re.IGNORECASE,
        ),
    }

    isolated_module = ast.Module(body=helpers, type_ignores=[])
    exec(compile(isolated_module, str(ROOT / "app.py"), "exec"), namespace)

    return namespace["_split_location_metadata"]


def load_transition_helper():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)
    fn = next(
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef) and node.name == "determine_application_transition"
    )
    isolated_module = ast.Module(body=[fn], type_ignores=[])
    namespace = {"Any": Any, "ValueError": ValueError}
    exec(compile(isolated_module, str(ROOT / "app.py"), "exec"), namespace)
    return namespace["determine_application_transition"]


class ApplicationTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transition = staticmethod(load_transition_helper())

    def test_approve_only_changes_review_status(self):
        status, note, set_applied_at = self.transition(
            "approve", "pending_approval", None, True
        )
        self.assertEqual(status, "approved")
        self.assertFalse(set_applied_at)
        self.assertIn("did not submit", note)

    def test_approve_rejects_missing_letter_and_wrong_status(self):
        with self.assertRaises(ValueError):
            self.transition("approve", "pending_approval", None, False)
        with self.assertRaises(ValueError):
            self.transition("approve", "approved", None, True)

    def test_archive_retains_draft_but_does_not_archive_submitted_record(self):
        status, note, set_applied_at = self.transition(
            "archive", "pending_approval", None, True
        )
        self.assertEqual(status, "archived")
        self.assertFalse(set_applied_at)
        self.assertIn("not deleted", note)
        with self.assertRaises(ValueError):
            self.transition("archive", "submitted", "2026-10-25", True)

    def test_submitted_requires_approved_status_and_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            self.transition("mark_submitted", "pending_approval", None, True, True)
        with self.assertRaises(ValueError):
            self.transition("mark_submitted", "approved", None, True, False)
        status, note, set_applied_at = self.transition(
            "mark_submitted", "approved", None, True, True
        )
        self.assertEqual(status, "submitted")
        self.assertTrue(set_applied_at)
        self.assertIn("dashboard did not submit", note)

    def test_duplicate_submission_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.transition("mark_submitted", "approved", "2026-10-25", True, True)


class ApplicationQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_query = staticmethod(load_application_query_builder())

    def test_application_query_is_scoped_by_profile(self):
        sql, params = self.build_query(1, limit=100)
        self.assertIn("FROM applications a", sql)
        self.assertIn("WHERE a.profile_id = %s", sql)
        self.assertEqual(params, [1, 100])

    def test_status_filter_is_parameterized(self):
        sql, params = self.build_query(1, status="pending_approval", limit=25)
        self.assertIn("AND a.application_status = %s", sql)
        self.assertEqual(params, [1, "pending_approval", 25])

    def test_status_text_is_never_interpolated_into_sql(self):
        hostile = "' OR 1=1 --"
        sql, params = self.build_query(1, status=hostile)
        self.assertNotIn(hostile, sql)
        self.assertIn(hostile, params)

    def test_location_metadata_is_split_from_concise_display_label(self):
        split_location = load_location_splitter()
        display, details = split_location(
            "Worldwide; Timezone restrictions: -11, -10, -9, -8, -7, -6, -5, -4, -3.5, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9"
        )
        self.assertEqual(display, "Worldwide")
        self.assertTrue(
            details.startswith("Zona waktu yang dicantumkan iklan:")
        )
        self.assertIn("WIB Indonesia (Jakarta, UTC+7)", details)
        self.assertLess(len(display), 20)

    def test_dashboard_template_compiles_and_renders_saved_application(self):
        env = Environment(
            loader=FileSystemLoader(str(ROOT / "templates")),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("index.html")
        stats = {
            "total_jobs": 75, "high_match": 1, "new_jobs": 0, "needs_review": 1,
            "location_restrictions": 0, "worldwide_claimed": 1, "location_not_matched": 0,
        }
        application_stats = {
            "total_applications": 1, "pending_approval": 1,
            "drafts_to_review": 0, "with_applied_at": 0,
        }
        application = {
            "application_id": 1, "job_id": 59, "profile_id": 1,
            "match_score": 90.0, "match_reason": "Draft only; human review required.",
            "application_status": "pending_approval",
            "cover_letter": "Dear Hiring Manager,\n\nThank you for considering my application.",
            "applied_at": None, "notes": "Not submitted.",
            "title": "Workflow Automation Engineer", "company_name": "KDCI Outsourcing",
            "country": "International Remote",
            "location": "Worldwide; Timezone restrictions: -11, -10, -9, -8, -7, -6",
            "display_location": "Worldwide",
            "location_details": "Timezone restrictions: -11, -10, -9, -8, -7, -6",
            "job_url": "https://example.com/job/59", "eligibility_status": "worldwide_claimed",
        }
        html = template.render(
            url_for=lambda endpoint, **kwargs: (
                f"/applications/{kwargs.get('application_id')}/action"
                if endpoint == "application_action" else "/static/style.css"
            ),
            error=None, profile_found=True, page="review",
            stats=stats, jobs=[], sources=[], statuses=[],
            review_queue=[], submitted_applications=[],
            application_stats=application_stats, applications=[application],
            application_statuses=["pending_approval"], search="", selected_source="",
            selected_eligibility="", min_score=0, app_status="",
            eligibility_labels={"worldwide_claimed": "Worldwide — perlu verifikasi"},
            application_status_labels={"pending_approval": "Menunggu persetujuan"},
            csrf_token=lambda: "test-csrf-token",
            get_flashed_messages=lambda with_categories=False: [],
        )
        for expected in (
            "Application Review", "Workflow Automation Engineer", "Menunggu persetujuan",
            "Belum dikirim", "Dear Hiring Manager", "Worldwide",
            "Penjelasan lokasi &amp; jam kerja", "Timezone restrictions:",
            "Setujui draf", "Arsipkan", "/applications/1/action",
        ):
            self.assertIn(expected, html)


if __name__ == "__main__":
    unittest.main()
