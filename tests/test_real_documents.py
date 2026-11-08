"""End-to-end tests against a document captured from the harness writer."""

from __future__ import annotations

import json
import unittest

from support import FIXTURES, TemporaryDocumentTest

from dsh_credentials_helper.credentials import CredentialsStore

FIXTURE = FIXTURES / "harness_document.yaml"
EXPECTED = FIXTURES / "harness_document_expected.json"
RECORDS_START = "records:"


class HarnessDocumentTests(TemporaryDocumentTest):
    def setUp(self):
        super().setUp()
        self.text = FIXTURE.read_text(encoding="utf-8")
        self.expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        self.write_document(self.text)
        self.store = CredentialsStore(self.path)

    def records_section(self):
        return self.text[self.text.index(RECORDS_START) :]

    def test_every_reference_the_writer_emitted_is_read_back(self):
        for name, value in self.expected.items():
            self.assertEqual(self.store.read_reference(name), value, name)

    def test_updating_one_value_keeps_the_rest_byte_identical(self):
        records = self.records_section()
        self.assertTrue(self.store.set_reference("ALPHA", "updated-value"))
        new_text = self.read_document()
        self.assertIn('  ALPHA: "updated-value"\n', new_text)
        self.assertEqual(new_text.count("\n"), self.text.count("\n"))
        self.assertIn(records, new_text)
        for name, value in self.expected.items():
            if name == "ALPHA":
                continue
            self.assertEqual(self.store.read_reference(name), value, name)

    def test_adding_one_value_keeps_the_records_section_byte_identical(self):
        records = self.records_section()
        self.assertTrue(self.store.set_reference("NEW_REFERENCE", "added-value"))
        new_text = self.read_document()
        self.assertEqual(new_text.count("\n"), self.text.count("\n") + 1)
        self.assertIn(records, new_text)
        self.assertEqual(self.store.read_reference("NEW_REFERENCE"), "added-value")
        self.assertEqual(new_text.count("NEW_REFERENCE:"), 1)

    def test_a_value_written_by_the_plugin_survives_a_writer_round_trip(self):
        value = 'a "quoted" value\\with tab\tand newline\nand unicode \u00e9'
        self.store.set_reference("ALPHA", value)
        self.assertEqual(self.store.read_reference("ALPHA"), value)
        text = self.read_document()
        self.assertIn('  ALPHA: "a \\"quoted\\" value', text)
        self.assertNotIn("\n" + value, text)

    def test_the_lock_file_is_released(self):
        self.store.set_reference("ALPHA", "updated-value")
        self.assertFalse(self.lock_path().exists())


if __name__ == "__main__":
    unittest.main()
