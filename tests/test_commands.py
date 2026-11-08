"""Tests for the /cred command handler."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dsh_credentials_helper.commands import CredentialCommandHandler
from dsh_credentials_helper.credentials import CredentialsStore

DOCUMENT = """version: 2
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


class CommandTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp(prefix="cred-command-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(directory, ignore_errors=True)
        )
        self.directory = directory
        self.path = directory / ".credentials.yaml"
        self.path.write_text(DOCUMENT, encoding="utf-8")
        os.chmod(self.path, 0o600)
        self.logger = RecordingLogger()
        self.handler = CredentialCommandHandler(
            CredentialsStore(self.path), self.logger
        )

    def run_command(self, line):
        return self.handler.handle(line)

    def file_text(self):
        return self.path.read_text(encoding="utf-8")

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

    def test_a_missing_file_errors_before_the_confirmation_step(self):
        self.path.unlink()
        result = self.run_command("set ALPHA new-value")
        self.assertEqual(result["kind"], "error")
        self.assertIn("not found", result["text"])
        self.assertEqual(self.run_command("ok")["kind"], "error")

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
