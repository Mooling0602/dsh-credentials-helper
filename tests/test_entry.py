"""Smoke tests for the plugin entry point."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ENTRY_PATH = Path(__file__).resolve().parents[1] / "src" / "entry.py"


class EntryTests(unittest.TestCase):
    def test_the_entry_point_exposes_apply(self):
        spec = importlib.util.spec_from_file_location(
            "credential_helper_entry", ENTRY_PATH
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.apply))


if __name__ == "__main__":
    unittest.main()
