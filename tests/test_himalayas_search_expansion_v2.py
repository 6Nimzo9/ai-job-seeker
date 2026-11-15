import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "sources" / "himalayas.py"
spec = importlib.util.spec_from_file_location("himalayas_expansion_v2", MODULE_PATH)
himalayas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(himalayas)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class HimalayasSearchExpansionV2Tests(unittest.TestCase):
    def test_search_terms_cover_target_role_families(self):
        expected = {
            "AI automation specialist",
            "AI and workflow automation specialist",
            "AI workflow automation",
            "application integration",
            "integration engineer",
            "automation developer",
            "process automation",
        }
        self.assertTrue(expected.issubset(set(himalayas.SEARCH_TERMS)))
        self.assertEqual(himalayas.MAX_PAGES_PER_TERM, 3)

    @patch.object(himalayas.requests, "get")
    def test_fetches_three_pages_per_term_and_deduplicates(self, mock_get):
        job = {
            "guid": "https://example.test/job/1",
            "applicationLink": "https://himalayas.app/jobs/1",
            "title": "AI and Workflow Automation Specialist",
            "companyName": "Example Co",
            "description": "Build workflows with Python, APIs, and n8n.",
            "locationRestrictions": [],
            "timezoneRestrictions": [],
            "categories": [],
            "parentCategories": [],
            "pubDate": "2026-10-25",
        }
        # Each term returns the same record on page 1 and 2, then an empty page 3.
        mock_get.side_effect = [
            FakeResponse({"jobs": [job]}),
            FakeResponse({"jobs": [job]}),
            FakeResponse({"jobs": []}),
        ] * len(himalayas.SEARCH_TERMS)

        results = himalayas.fetch_himalayas_jobs()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], job["guid"])
        self.assertEqual(mock_get.call_count, len(himalayas.SEARCH_TERMS) * 3)
        queried_terms = {call.kwargs["params"]["q"] for call in mock_get.call_args_list}
        self.assertEqual(queried_terms, set(himalayas.SEARCH_TERMS))
        page_numbers = {call.kwargs["params"]["page"] for call in mock_get.call_args_list}
        self.assertEqual(page_numbers, {1, 2, 3})

    @patch.object(himalayas.requests, "get")
    def test_api_failure_is_handled_without_crashing(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("test network error")
        results = himalayas.fetch_himalayas_jobs()
        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, len(himalayas.SEARCH_TERMS))


if __name__ == "__main__":
    unittest.main()
