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


class CheckCommandTests(TemporaryDocumentTest):
    """Tests for /cred check, which reports state instead of throwing."""

    DOCUMENT = (
        'version: 1\nrefs:\n  TEST: "testing--a-long-secret-value-ngvr"\n'
        "  PLAIN: plain-value\n"
        "  EMPTY: null\n"
        "  BLANK:\n"
        "  NUM: 5\n"
        '  LONGER: "a value that is long enough to mask"\n'
        "records:\n  scope/id:\n    kind: grant\n"
    )

    def setUp(self):
        super().setUp()
        self.write_document(self.DOCUMENT)
        self.handler = CredentialCommandHandler(CredentialsStore(self.path), None)

    def check(self, line):
        return self.handler.handle(line)

    def test_a_valid_reference_reports_its_masked_value(self):
        result = self.check("check TEST")
        self.assertEqual(result["kind"], "success")
        self.assertEqual(
            result["text"].splitlines(),
            [
                "Key TEST has valid value.",
                f"file: {self.path}",
                "current: test...ngvr",
            ],
        )
        self.assertNotIn("testing--a-long-secret-value-ngvr", result["text"])

    def test_a_short_value_is_fully_masked(self):
        result = self.check("check PLAIN")
        self.assertIn("current: ********", result["text"])
        self.assertNotIn("plain-value", result["text"])

    def test_an_absent_reference_says_so(self):
        result = self.check("check UNKNOWN")
        self.assertEqual(result["kind"], "success")
        self.assertEqual(
            result["text"].splitlines(),
            ["UNKNOWN is not existing in refs.", f"file: {self.path}"],
        )

    def test_every_unusable_value_reports_null(self):
        for name in ["EMPTY", "BLANK", "NUM"]:
            with self.subTest(name=name):
                result = self.check("check " + name)
                self.assertEqual(result["kind"], "success")
                self.assertEqual(
                    result["text"].splitlines(),
                    [
                        f"{name} is existing in refs but with invalid value.",
                        f"file: {self.path}",
                        "current: <null>",
                    ],
                )

    def test_an_unusable_neighbour_does_not_hide_a_valid_reference(self):
        result = self.check("check LONGER")
        self.assertIn("has valid value", result["text"])

    def test_check_never_writes(self):
        before = self.read_document()
        for line in ["check TEST", "check UNKNOWN", "check EMPTY"]:
            self.check(line)
        self.assertEqual(self.read_document(), before)
        self.assertFalse(self.lock_path().exists())

    def test_a_missing_file_is_an_error_not_a_missing_key(self):
        self.path.unlink()
        result = self.check("check TEST")
        self.assertEqual(result["kind"], "error")
        self.assertIn("not found", result["text"])

    def test_check_rejects_a_missing_name_and_extra_arguments(self):
        for line in ["check", "check A B"]:
            with self.subTest(line=line):
                result = self.check(line)
                self.assertEqual(result["kind"], "error")
                self.assertIn("/cred check needs exactly one", result["text"])
                self.assertIn("Usage:", result["text"])

    def test_check_rejects_a_malformed_name(self):
        result = self.check("check 1BAD")
        self.assertEqual(result["kind"], "error")
        self.assertIn("invalid reference name", result["text"])


class RemoveCommandTests(TemporaryDocumentTest):
    """Tests for the destructive /cred remove subcommand."""

    def setUp(self):
        super().setUp()
        self.write_document(DOCUMENT)
        self.handler = CredentialCommandHandler(CredentialsStore(self.path), None)

    def run_command(self, line):
        return self.handler.handle(line)

    def test_removal_asks_for_confirmation_and_keeps_the_file(self):
        before = self.read_document()
        result = self.run_command("remove ALPHA")
        self.assertEqual(result["kind"], "success")
        self.assertIn("confirmation required: remove ALPHA", result["text"])
        self.assertIn("  ALPHA: old-...cret", result["text"])
        self.assertEqual(self.read_document(), before)

    def test_the_prompt_masks_the_current_value(self):
        result = self.run_command("remove BETA")
        # Eight characters is inside the fully masked range.
        self.assertIn("  BETA: ********", result["text"])
        self.assertNotIn("beta-old", result["text"])
        self.assertIn("'beta-old'", self.read_document())

    def test_ok_removes_the_entry(self):
        self.run_command("remove ALPHA")
        result = self.run_command("ok")
        self.assertEqual(result["kind"], "success")
        self.assertIn("  ALPHA", result["text"])
        self.assertNotIn("ALPHA", self.read_document())
        self.assertIn("BETA", self.read_document())

    def test_confirm_removes_the_entry(self):
        self.run_command("remove BETA")
        result = self.run_command("confirm")
        self.assertIn("  BETA", result["text"])
        self.assertNotIn("BETA", self.read_document())

    def test_repeating_the_command_removes_without_ok(self):
        self.run_command("remove ALPHA")
        result = self.run_command("remove ALPHA")
        self.assertIn("  ALPHA", result["text"])
        self.assertNotIn("ALPHA: old-alpha-secret", self.read_document())

    def test_skip_confirm_is_refused(self):
        result = self.run_command("remove ALPHA --skip-confirm")
        self.assertEqual(result["kind"], "error")
        self.assertIn("cannot skip confirmation", result["text"])
        self.assertIn("ALPHA: old-alpha-secret", self.read_document())

    def test_an_unknown_option_is_refused(self):
        result = self.run_command("remove ALPHA --force")
        self.assertEqual(result["kind"], "error")
        self.assertIn("unknown option --force", result["text"])

    def test_removing_an_absent_reference_says_so(self):
        result = self.run_command("remove MISSING")
        self.assertEqual(result["kind"], "success")
        self.assertIn("nothing to remove", result["text"])

    def test_the_confirmation_is_required_for_each_name(self):
        self.run_command("remove ALPHA")
        result = self.run_command("remove BETA")
        self.assertIn("confirmation required: remove BETA", result["text"])
        self.assertIn("ALPHA", self.read_document())

    def test_a_staged_removal_is_dropped_by_another_command(self):
        self.run_command("remove ALPHA")
        self.run_command("set GAMMA new-value --skip-confirm")
        result = self.run_command("ok")
        self.assertEqual(result["kind"], "error")
        self.assertIn("nothing is waiting", result["text"])
        self.assertIn("ALPHA: old-alpha-secret", self.read_document())

    def test_remove_rejects_a_missing_name_and_extra_arguments(self):
        for line in ["remove", "remove A B"]:
            with self.subTest(line=line):
                result = self.run_command(line)
                self.assertEqual(result["kind"], "error")
                self.assertIn("/cred remove needs exactly one", result["text"])
                self.assertIn("Usage:", result["text"])

    def test_remove_rejects_a_malformed_name(self):
        result = self.run_command("remove 1BAD")
        self.assertEqual(result["kind"], "error")
        self.assertIn("invalid reference name", result["text"])

    def test_a_missing_file_reports_nothing_to_remove(self):
        self.path.unlink()
        result = self.run_command("remove ALPHA")
        self.assertEqual(result["kind"], "success")
        self.assertIn("nothing to remove", result["text"])
        self.assertFalse(self.path.exists())


class RemoveWildcardTests(TemporaryDocumentTest):
    """Tests for deleting several references with a pattern."""

    DOCUMENT = (
        "version: 1\nrefs:\n"
        '  TEST_A: "aaaaaaaaaaaaaaaa"\n'
        '  TEST_B: "bbbbbbbbbbbbbbbb"\n'
        '  TEST_C: "short"\n'
        '  OTHER: "othersecretvalue"\n'
        '  TESTX: "xxxxxxxxxxxxxxxx"\n'
    )

    def setUp(self):
        super().setUp()
        self.write_document(self.DOCUMENT)
        self.handler = CredentialCommandHandler(CredentialsStore(self.path), None)

    def run_command(self, line):
        return self.handler.handle(line)

    def test_a_pattern_lists_every_match_before_writing(self):
        before = self.read_document()
        result = self.run_command("remove TEST_*")
        self.assertEqual(result["kind"], "success")
        self.assertIn("confirmation required: remove TEST_*", result["text"])
        self.assertIn("matches 3 reference(s):", result["text"])
        self.assertIn("  TEST_A: aaaa...aaaa", result["text"])
        self.assertIn("  TEST_B: bbbb...bbbb", result["text"])
        self.assertIn("  TEST_C: ********", result["text"])
        self.assertEqual(self.read_document(), before)

    def test_a_pattern_never_lists_a_non_matching_neighbour(self):
        result = self.run_command("remove TEST_*")
        self.assertNotIn("OTHER", result["text"])
        self.assertNotIn("TESTX", result["text"])

    def test_confirming_a_pattern_removes_exactly_the_matches(self):
        self.run_command("remove TEST_*")
        result = self.run_command("ok")
        self.assertIn("removed 3 reference(s):", result["text"])
        self.assertIn("  TEST_A", result["text"])
        text = self.read_document()
        self.assertNotIn("TEST_A", text)
        self.assertNotIn("TEST_B", text)
        self.assertNotIn("TEST_C", text)
        self.assertIn("OTHER", text)
        self.assertIn("TESTX", text)

    def test_a_question_mark_matches_one_character(self):
        result = self.run_command("remove TEST_?")
        self.assertIn("matches 3 reference(s):", result["text"])

    def test_a_bare_star_lists_every_reference(self):
        result = self.run_command("remove *")
        self.assertIn("matches 5 reference(s):", result["text"])

    def test_a_bare_star_leaves_the_section_addressable(self):
        self.run_command("remove *")
        self.run_command("ok")
        self.assertEqual(self.read_document(), "version: 1\nrefs:\n")

    def test_a_pattern_with_no_match_writes_nothing(self):
        before = self.read_document()
        result = self.run_command("remove ZZZ_*")
        self.assertEqual(result["kind"], "success")
        self.assertIn("no reference matches ZZZ_*", result["text"])
        self.assertEqual(self.read_document(), before)

    def test_a_repeated_pattern_confirms_the_removal(self):
        self.run_command("remove TEST_*")
        result = self.run_command("remove TEST_*")
        self.assertIn("removed 3 reference(s):", result["text"])
        self.assertNotIn("TEST_A", self.read_document())

    def test_a_pattern_removal_is_refused_with_skip_confirm(self):
        before = self.read_document()
        result = self.run_command("remove TEST_* --skip-confirm")
        self.assertEqual(result["kind"], "error")
        self.assertIn("cannot skip confirmation", result["text"])
        self.assertEqual(self.read_document(), before)

    def test_dots_in_a_target_are_literal_not_wildcards(self):
        result = self.run_command("remove TEST.A")
        self.assertEqual(result["kind"], "error")
        self.assertIn("invalid reference name", result["text"])

    def test_the_confirmation_names_the_pattern_and_the_file(self):
        result = self.run_command("remove TEST_*")
        self.assertIn(f"file: {self.path}", result["text"])
        self.assertIn("cannot be undone", result["text"])

    def test_a_pattern_keeps_unrelated_records(self):
        self.write_document(
            "version: 1\nrefs:\n  A: a\nrecords:\n  scope/id:\n    kind: api-key\n"
        )
        self.run_command("remove *")
        self.run_command("ok")
        self.assertIn("records:", self.read_document())
        self.assertIn("scope/id:", self.read_document())


class ErrorSplitTests(TemporaryDocumentTest):
    """A two-clause error reaches the client as two lines.

    The client renders a command outcome as a one-line summary that
    elides, and it offers the disclosure body only when the text
    contains a newline, so a whole clause would otherwise be unreadable.
    The split falls at the clause boundary, never at a column, because
    the client wraps a merely-wide line on its own.
    """

    def setUp(self):
        super().setUp()
        self.write_document('version: 1\nrefs:\n  TEST: "v"\n')
        self.handler = CredentialCommandHandler(CredentialsStore(self.path), None)

    def test_a_two_clause_error_gains_a_newline(self):
        result = self.handler.handle("check 1BAD")
        self.assertEqual(result["kind"], "error")
        self.assertIn("\n", result["text"])

    def test_the_split_falls_at_the_clause_boundary(self):
        result = self.handler.handle("check 1BAD")
        self.assertEqual(
            result["text"].splitlines(),
            [
                '/cred: invalid reference name "1BAD"',
                "use letters, digits and underscores, starting with a letter or underscore",
            ],
        )

    def test_the_second_clause_is_never_reflowed(self):
        result = self.handler.handle("check 1BAD")
        self.assertIn(
            "starting with a letter or underscore", result["text"].splitlines()[1]
        )

    def test_usage_output_is_not_resplit(self):
        result = self.handler.handle("check")
        self.assertIn("needs exactly one reference name", result["text"])
        self.assertIn(
            "Usage:\n/cred set <KEY> <VALUE> [--skip-confirm]", result["text"]
        )


if __name__ == "__main__":
    unittest.main()
