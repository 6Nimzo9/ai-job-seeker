"""Offline tests for the job query builder without requiring Flask or MySQL."""
import ast
from pathlib import Path
import unittest
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_job_query_builder():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    module_ast = ast.parse(source)
    helpers = [
        node for node in module_ast.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_base_join_sql", "_build_job_filter_clause", "build_job_query"}
    ]
    namespace = {"Any": Any, "PAGE_SIZE": 100}
    isolated_module = ast.Module(body=helpers, type_ignores=[])
    exec(compile(isolated_module, str(ROOT / "app.py"), "exec"), namespace)
    return namespace["build_job_query"]


class QueryBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_query = staticmethod(load_job_query_builder())

    def test_query_is_parameterized_and_sorted(self):
        sql, params = self.build_query(3, search="Python", limit=100)
        self.assertIn("LOWER(j.title) LIKE %s", sql)
        normalized_sql = " ".join(sql.split())
        self.assertIn(
            "ORDER BY jm.overall_score IS NULL ASC, jm.overall_score DESC",
            normalized_sql,
        )
        self.assertEqual(params[0], 3)
        self.assertIn("%python%", params)
        self.assertEqual(params[-2:], [100, 0])

    def test_filters_are_combined(self):
        sql, params = self.build_query(
            4, search="automation", source="Remotive",
            eligibility="worldwide_claimed", min_score=70, limit=50,
        )
        self.assertIn("s.name = %s", sql)
        self.assertIn("jm.eligibility_status = %s", sql)
        self.assertEqual(
            params,
            [4, "%automation%", "%automation%", "%automation%",
             "Remotive", "worldwide_claimed", 70, 50, 0],
        )

    def test_invalid_or_empty_filters_do_not_inject_sql(self):
        sql, params = self.build_query(1, search="' OR 1=1 --", limit=25)
        self.assertNotIn("' OR 1=1 --", sql)
        self.assertIn("%' or 1=1 --%", params)
        self.assertEqual(params[-2:], [25, 0])


if __name__ == "__main__":
    unittest.main()
