"""Tests for the /cred command handler."""

from __future__ import annotations

import unittest
from pathlib import Path

from support import TemporaryDocumentTest

from dsh_credentials_helper.commands import CredentialCommandHandler
from dsh_credentials_helper.credentials import CredentialsStore

DOCUMENT = """version: 1
refs:
  ALPHA: old-alpha-secret
  BETA: 'beta-old'
records:
  scope/id:
    kind: api-key
    payload:
      token: keep-me
"""


class RecordingLogger:
    def __init__(self):
        self.errors = []

    def error(self, message, data=None):
        self.errors.append((message, data))


class CommandTests(TemporaryDocumentTest):
    def setUp(self):
        super().setUp()
        self.write_document(DOCUMENT)
        self.logger = RecordingLogger()
        self.handler = CredentialCommandHandler(
            CredentialsStore(self.path), self.logger
        )

    def run_command(self, line):
        return self.handler.handle(line)

    def file_text(self):
        return self.read_document()

    def test_usage_and_help(self):
        for line in [
            "",
            "unknown",
            "read",
            "read A B",
            "set",
            "set ALPHA",
            "set A B C",
        ]:
            result = self.run_command(line)
            self.assertEqual(result["kind"], "error", line)
            self.assertIn("Usage:", result["text"], line)
        help_result = self.run_command("help")
        self.assertEqual(help_result["kind"], "success")
        self.assertIn("/cred set", help_result["text"])
        self.assertIn("/cred help", help_result["text"])

    def test_unknown_options_are_refused(self):
        result = self.run_command("set ALPHA value --bogus")
        self.assertEqual(result["kind"], "error")
        self.assertIn("unknown option", result["text"])
        duplicate = self.run_command("set ALPHA value --skip-confirm --skip-confirm")
        self.assertEqual(duplicate["kind"], "error")

    def test_read_returns_the_value_and_missing_is_an_error(self):
        result = self.run_command("read ALPHA")
        self.assertEqual(result["kind"], "success")
        self.assertEqual(result["text"], "old-alpha-secret" + chr(10))
        missing = self.run_command("read MISSING")
        self.assertEqual(missing["kind"], "error")

    def test_the_first_set_asks_for_confirmation_and_keeps_the_file(self):
        before = self.file_text()
        result = self.run_command("set ALPHA new-alpha-secret")
        self.assertEqual(result["kind"], "success")
        self.assertIn("confirmation required", result["text"])
        self.assertIn("new-...cret", result["text"])
        self.assertNotIn("new-alpha-secret", result["text"])
        self.assertNotIn("old-alpha-secret", result["text"])
        self.assertEqual(self.file_text(), before)

    def test_repeating_the_same_command_writes(self):
        self.run_command("set ALPHA new-alpha-secret")
        result = self.run_command("set ALPHA new-alpha-secret")
        self.assertEqual(result["kind"], "success")
        self.assertTrue(result["text"].startswith("updated"))
        self.assertIn('ALPHA: "new-alpha-secret"', self.file_text())
        self.assertIn("token: keep-me", self.file_text())
        self.assertNotIn("new-alpha-secret", result["text"])

    def test_ok_writes_the_pending_update(self):
        self.run_command("set ALPHA new-alpha-secret")
        result = self.run_command("ok")
        self.assertEqual(result["kind"], "success")
        self.assertTrue(result["text"].startswith("updated"))
        self.assertIn('ALPHA: "new-alpha-secret"', self.file_text())

    def test_confirm_writes_the_pending_update(self):
        self.run_command("set ALPHA new-alpha-secret")
        result = self.run_command("confirm")
        self.assertEqual(result["kind"], "success")
        self.assertIn("new-alpha-secret", self.file_text())

    def test_ok_without_a_pending_update_is_an_error(self):
        for line in ["ok", "confirm"]:
            result = self.run_command(line)
            self.assertEqual(result["kind"], "error")
            self.assertIn("nothing is waiting", result["text"])

    def test_skip_confirm_writes_immediately(self):
        result = self.run_command("set ALPHA new-alpha-secret --skip-confirm")
        self.assertEqual(result["kind"], "success")
        self.assertTrue(result["text"].startswith("updated"))
        self.assertIn("new-alpha-secret", self.file_text())

    def test_skip_confirm_is_accepted_before_the_arguments(self):
        result = self.run_command("set --skip-confirm ALPHA new-alpha-secret")
        self.assertEqual(result["kind"], "success")
        self.assertIn("new-alpha-secret", self.file_text())

    def test_a_quoted_value_keeps_its_whitespace(self):
        result = self.run_command('set ALPHA "two words" --skip-confirm')
        self.assertEqual(result["kind"], "success")
        self.assertIn('ALPHA: "two words"', self.file_text())

    def test_a_new_reference_is_added(self):
        result = self.run_command("set DELTA delta-value --skip-confirm")
        self.assertEqual(result["kind"], "success")
        text = self.file_text()
        self.assertIn('DELTA: "delta-value"', text)
        self.assertLess(text.index("DELTA:"), text.index("records:"))

    def test_a_different_set_replaces_the_pending_update(self):
        self.run_command("set ALPHA first-value")
        prompt = self.run_command("set ALPHA second-value")
        self.assertIn("confirmation required", prompt["text"])
        result = self.run_command("ok")
        self.assertEqual(result["kind"], "success")
        self.assertIn('ALPHA: "second-value"', self.file_text())

    def test_writing_the_same_value_reports_unchanged(self):
        result = self.run_command("set ALPHA old-alpha-secret --skip-confirm")
        self.assertEqual(result["kind"], "success")
        self.assertTrue(result["text"].startswith("unchanged"))

    def test_a_malformed_name_is_refused(self):
        result = self.run_command("set 1BAD value")
        self.assertEqual(result["kind"], "error")
        self.assertIn("invalid reference name", result["text"])

    def test_an_empty_value_is_refused(self):
        result = self.run_command('set ALPHA ""')
        self.assertEqual(result["kind"], "error")

    def test_a_value_starting_with_two_dashes_needs_the_end_of_options(self):
        result = self.run_command("set ALPHA --weird-value")
        self.assertEqual(result["kind"], "error")
        self.assertIn("unknown option", result["text"])
        staged = self.run_command("set ALPHA -- --weird-value")
        self.assertIn("confirmation required", staged["text"])
        self.assertIn('ALPHA: "--weird-value"', self.file_text()) if False else None
        self.assertNotIn("--weird-value", self.file_text())
        written = self.run_command("ok")
        self.assertEqual(written["kind"], "success")
        self.assertIn('ALPHA: "--weird-value"', self.file_text())
        self.assertEqual(self.run_command("read ALPHA")["text"], "--weird-value\n")

    def test_a_value_starting_with_one_dash_needs_no_escape(self):
        result = self.run_command("set ALPHA -dash-value --skip-confirm")
        self.assertEqual(result["kind"], "success")
        self.assertIn('ALPHA: "-dash-value"', self.file_text())

    def test_a_missing_file_is_created_after_confirmation(self):
        self.path.unlink()
        staged = self.run_command("set ALPHA new-value")
        self.assertEqual(staged["kind"], "success")
        self.assertIn("current: (not set)", staged["text"])
        self.assertFalse(self.path.exists())
        written = self.run_command("ok")
        self.assertEqual(written["kind"], "success")
        self.assertEqual(self.file_text(), 'version: 1\nrefs:\n  ALPHA: "new-value"\n')

    def test_a_document_without_refs_gets_the_section_on_write(self):
        self.write_document("version: 1\nrecords:\n  scope/id:\n    kind: grant\n")
        self.run_command("set ALPHA new-value")
        result = self.run_command("ok")
        self.assertEqual(result["kind"], "success")
        self.assertIn('refs:\n  ALPHA: "new-value"\nrecords:', self.file_text())

    def test_an_unexpected_error_stays_opaque(self):
        class ExplodingStore:
            @property
            def path(self):
                return Path("/tmp/exploding")

            def peek_reference(self, name):
                raise RuntimeError("secret-value-must-not-leak")

            def read_reference(self, name):
                raise RuntimeError("secret-value-must-not-leak")

            def set_reference(self, name, value):
                raise RuntimeError("secret-value-must-not-leak")

        handler = CredentialCommandHandler(ExplodingStore(), self.logger)
        result = handler.handle("read ALPHA")
        self.assertEqual(result["kind"], "error")
        self.assertNotIn("secret-value", result["text"])
        self.assertTrue(self.logger.errors)
        message, data = self.logger.errors[0]
        self.assertIn("RuntimeError", message)
        self.assertIsNone(data)


if __name__ == "__main__":
    unittest.main()
