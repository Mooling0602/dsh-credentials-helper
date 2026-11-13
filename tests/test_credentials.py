"""Tests for the credential document store."""

from __future__ import annotations

import os
import stat
import unittest
from unittest import mock

from support import TemporaryDocumentTest

from dsh_credentials_helper import credentials
from dsh_credentials_helper.credentials import (
    CredentialsError,
    CredentialsStore,
    DocumentChanged,
    DocumentNotFound,
    ReferenceNotFound,
)

DOCUMENT = """version: 1
# top level comment
refs:
  ALPHA: plain-secret
  BETA: 'single quoted'
  GAMMA: "double quoted"
records:
  scope/id:
    kind: api-key
    payload:
      token: keep-me
"""


class StoreTests(TemporaryDocumentTest):
    def store(self):
        return CredentialsStore(self.path)

    def test_it_reads_plain_single_and_double_quoted_values(self):
        self.write_document(DOCUMENT)
        self.assertEqual(self.store().read_reference("ALPHA"), "plain-secret")
        self.assertEqual(self.store().read_reference("BETA"), "single quoted")
        self.assertEqual(self.store().read_reference("GAMMA"), "double quoted")

    def test_a_missing_reference_is_reported(self):
        self.write_document(DOCUMENT)
        self.assertIsNone(self.store().peek_reference("MISSING"))
        with self.assertRaises(ReferenceNotFound):
            self.store().read_reference("MISSING")

    def test_a_missing_file_is_reported_by_a_read(self):
        with self.assertRaises(DocumentNotFound) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("not found", str(caught.exception))

    def test_a_write_creates_a_missing_document(self):
        self.assertTrue(self.store().set_reference("ALPHA", "created"))
        self.assertEqual(
            self.read_document(), 'version: 1\nrefs:\n  ALPHA: "created"\n'
        )
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.store().read_reference("ALPHA"), "created")

    def test_it_updates_one_reference_and_preserves_the_rest(self):
        self.write_document(DOCUMENT)
        store = self.store()
        before = {
            name: store.read_reference(name) for name in ("ALPHA", "BETA", "GAMMA")
        }
        self.assertTrue(store.set_reference("ALPHA", "new-secret"))
        text = self.read_document()
        self.assertIn("version: 1", text)
        self.assertIn("# top level comment", text)
        self.assertIn("BETA: 'single quoted'", text)
        self.assertIn('GAMMA: "double quoted"', text)
        self.assertIn("token: keep-me", text)
        self.assertEqual(store.read_reference("ALPHA"), "new-secret")
        self.assertEqual(store.read_reference("BETA"), before["BETA"])
        self.assertEqual(store.read_reference("GAMMA"), before["GAMMA"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_it_adds_a_new_reference_inside_the_refs_block(self):
        self.write_document(DOCUMENT)
        self.assertTrue(self.store().set_reference("DELTA", "new-value"))
        text = self.read_document()
        self.assertGreater(text.index("DELTA:"), text.index("GAMMA:"))
        self.assertLess(text.index("DELTA:"), text.index("records:"))
        self.assertEqual(self.store().read_reference("DELTA"), "new-value")

    def test_an_identical_value_is_not_written(self):
        self.write_document(DOCUMENT)
        before = self.read_document()
        self.assertFalse(self.store().set_reference("ALPHA", "plain-secret"))
        self.assertEqual(self.read_document(), before)

    def test_yaml_looking_values_round_trip_as_strings(self):
        self.write_document(DOCUMENT)
        values = ["true", "null", "123", "#not a comment", "a: b", " padded "]
        for index, value in enumerate(values):
            name = "VALUE" + str(index)
            self.assertTrue(self.store().set_reference(name, value))
            self.assertEqual(self.store().read_reference(name), value)

    def test_a_value_with_a_newline_round_trips(self):
        self.write_document(DOCUMENT)
        value = "line one\nline two"
        self.assertTrue(self.store().set_reference("ALPHA", value))
        self.assertEqual(self.store().read_reference("ALPHA"), value)

    def test_quotes_backslashes_unicode_and_tabs_round_trip(self):
        self.write_document(DOCUMENT)
        value = 'a\\b"c' + "\td\u00e9lta\n"
        self.assertTrue(self.store().set_reference("ALPHA", value))
        self.assertEqual(self.store().read_reference("ALPHA"), value)

    def test_invalid_names_and_empty_values_are_refused(self):
        self.write_document(DOCUMENT)
        for name in ["", "1BAD", "BAD-NAME", "BAD NAME"]:
            with self.assertRaises(CredentialsError):
                self.store().set_reference(name, "value")
        with self.assertRaises(CredentialsError):
            self.store().set_reference("ALPHA", "")

    def test_the_document_mode_is_tightened_to_owner_only(self):
        self.write_document(DOCUMENT)
        os.chmod(self.path, 0o644)
        self.store().set_reference("ALPHA", "new-secret")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_crlf_line_endings_survive(self):
        self.write_document(DOCUMENT.replace("\n", "\r\n"))
        self.store().set_reference("ALPHA", "new-secret")
        text = self.read_document()
        self.assertEqual(text.count("\n"), text.count("\r\n"))

    def test_a_duplicate_reference_is_refused(self):
        self.write_document("version: 1\nrefs:\n  ALPHA: first\n  ALPHA: second\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("more than once", str(caught.exception))
        with self.assertRaises(CredentialsError):
            self.store().set_reference("ALPHA", "new-secret")

    def test_a_parser_error_does_not_echo_the_document(self):
        self.write_document("version: 1\nrefs:\n  LEAKY_SECRET: 'unterminated\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().read_reference("LEAKY_SECRET")
        message = str(caught.exception)
        self.assertNotIn("LEAKY_SECRET", message)
        self.assertNotIn("unterminated\n", message)

    def test_a_non_string_reference_is_rejected(self):
        self.write_document("version: 1\nrefs:\n  NUMBER: 5\n  FLAG: true\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().read_reference("NUMBER")
        self.assertIn("not a string", str(caught.exception))
        with self.assertRaises(CredentialsError):
            self.store().read_reference("FLAG")

    def test_a_wrapped_plain_value_is_folded(self):
        self.write_document("version: 1\nrefs:\n  MULTI: first\n    second\n")
        self.assertEqual(self.store().read_reference("MULTI"), "first second")

    def test_a_flow_refs_mapping_is_rejected(self):
        self.write_document("version: 1\nrefs: {ALPHA: x}\n")
        with self.assertRaises(CredentialsError):
            self.store().read_reference("ALPHA")

    def test_the_empty_flow_refs_mapping_the_writer_emits_is_accepted(self):
        self.write_document(
            "version: 1\nrefs: {}\nrecords:\n  scope/id:\n"
            "    kind: grant\n    payload:\n      a: 1\n"
        )
        self.assertIsNone(self.store().peek_reference("ALPHA"))
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        text = self.read_document()
        self.assertIn('refs:\n  ALPHA: "v"\n', text)
        self.assertIn("scope/id:", text)
        self.assertEqual(self.store().read_reference("ALPHA"), "v")

    def test_an_inline_null_refs_mapping_is_accepted(self):
        self.write_document("version: 1\nrefs: null\n")
        self.assertIsNone(self.store().peek_reference("ALPHA"))
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        self.assertEqual(self.read_document(), 'version: 1\nrefs:\n  ALPHA: "v"\n')

    def test_a_document_without_refs_is_read_as_an_empty_store(self):
        self.write_document(
            "version: 1\nrecords:\n  scope/id:\n    kind: grant\n"
            "    payload:\n      a: 1\n"
        )
        self.assertIsNone(self.store().peek_reference("ALPHA"))
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        text = self.read_document()
        self.assertIn('version: 1\nrefs:\n  ALPHA: "v"\nrecords:', text)
        self.assertIn("scope/id:", text)

    def test_an_empty_document_is_created_in_the_writer_layout(self):
        self.write_document("")
        self.assertIsNone(self.store().peek_reference("ALPHA"))
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        self.assertEqual(self.read_document(), 'version: 1\nrefs:\n  ALPHA: "v"\n')

    def test_a_comment_only_document_is_completed(self):
        self.write_document("# local note\n\n")
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        self.assertEqual(
            self.read_document(),
            '# local note\n\nversion: 1\nrefs:\n  ALPHA: "v"\n',
        )

    def test_content_without_a_version_line_is_refused(self):
        self.write_document("records:\n  scope/id:\n    kind: grant\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().set_reference("ALPHA", "v")
        self.assertIn("version 1 layout", str(caught.exception))
        self.assertIn("records:", self.read_document())

    def test_an_inline_empty_header_keeps_its_comment(self):
        self.write_document("version: 1\nrefs: {}  # empty for now\nrecords: {}\n")
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        text = self.read_document()
        self.assertIn('refs: # empty for now\n  ALPHA: "v"\n', text)
        self.assertIn("records: {}", text)
        self.assertEqual(self.store().read_reference("ALPHA"), "v")

    def test_a_quoted_refs_key_is_refused(self):
        self.write_document("version: 1\n'refs':\n  ALPHA: x\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("without quotes", str(caught.exception))
        with self.assertRaises(CredentialsError):
            self.store().set_reference("ALPHA", "v")

    def test_line_break_like_unicode_is_escaped_in_the_written_scalar(self):
        self.write_document(DOCUMENT)
        value = "a" + chr(0x85) + "b" + chr(0x2028) + "c"
        self.assertTrue(self.store().set_reference("ALPHA", value))
        text = self.read_document()
        self.assertIn("\\x85", text)
        self.assertIn("\\u2028", text)
        self.assertEqual(self.store().read_reference("ALPHA"), value)

    def test_an_unknown_document_version_is_refused(self):
        self.write_document("version: 2\nrecords:\n  scope/id:\n    kind: grant\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().set_reference("ALPHA", "v")
        self.assertIn("version 2", str(caught.exception))

    def test_a_four_space_indented_refs_block_is_updated_in_place(self):
        self.write_document("version: 1\nrefs:\n    ALPHA: a\n    BETA: b\n")
        self.assertTrue(self.store().set_reference("BETA", "new"))
        self.assertTrue(self.store().set_reference("GAMMA", "value"))
        text = self.read_document()
        self.assertIn('    BETA: "new"', text)
        self.assertIn('    GAMMA: "value"', text)
        self.assertIn("    ALPHA: a", text)

    def test_a_comment_only_refs_block_uses_the_comment_indentation(self):
        self.write_document(
            "version: 1\nrefs:\n    # section comment\nrecords:\n  a: b\n"
        )
        self.assertTrue(self.store().set_reference("ALPHA", "v"))
        self.assertIn('    # section comment\n    ALPHA: "v"', self.read_document())

    def test_a_tab_indented_refs_entry_is_refused_with_a_clear_error(self):
        self.write_document("version: 1\nrefs:\n\tALPHA: a\n")
        with self.assertRaises(CredentialsError) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("tab", str(caught.exception))
        with self.assertRaises(CredentialsError):
            self.store().set_reference("ALPHA", "v")

    def test_a_column_zero_comment_does_not_truncate_the_refs_block(self):
        self.write_document(
            "version: 1\nrefs:\n  ALPHA: x\n# section comment\n"
            "  BETA: old-beta\nrecords: {}\n"
        )
        store = self.store()
        self.assertEqual(store.peek_reference("BETA"), "old-beta")
        self.assertTrue(store.set_reference("BETA", "new-beta"))
        text = self.read_document()
        self.assertEqual(text.count("BETA:"), 1)
        self.assertIn('  BETA: "new-beta"', text)
        self.assertIn("# section comment", text)
        self.assertEqual(store.read_reference("ALPHA"), "x")

    def test_a_document_that_changes_before_the_write_is_refused(self):
        self.write_document(DOCUMENT)
        with (
            mock.patch.object(
                credentials, "_document_fingerprint", side_effect=[(1, 1, 1), (2, 2, 2)]
            ),
            self.assertRaises(DocumentChanged),
        ):
            self.store().set_reference("ALPHA", "new-secret")
        self.assertIn("ALPHA: plain-secret", self.read_document())

    def test_no_temporary_file_is_left_behind(self):
        self.write_document(DOCUMENT)
        self.store().set_reference("ALPHA", "new-secret")
        leftovers = [
            item.name for item in self.directory.iterdir() if item.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])


class CheckReferenceTests(TemporaryDocumentTest):
    """Tests for the lenient per-reference scan behind /cred check."""

    def test_it_reports_a_valid_reference(self):
        self.write_document('version: 1\nrefs:\n  ALPHA: "secret-value"\n')
        scan = CredentialsStore(self.path).check_reference("ALPHA")
        self.assertTrue(scan.exists)
        self.assertEqual(scan.value, "secret-value")

    def test_it_reports_an_absent_reference(self):
        self.write_document(DOCUMENT)
        scan = CredentialsStore(self.path).check_reference("MISSING")
        self.assertFalse(scan.exists)
        self.assertIsNone(scan.value)

    def test_an_unusable_entry_is_present_without_a_value(self):
        self.write_document(
            "version: 1\nrefs:\n  OK: fine\n  NULLED: null\n"
            "  BLANK:\n  NUMBER: 5\n  NESTED:\n    A: b\n"
        )
        store = CredentialsStore(self.path)
        for name in ["NULLED", "BLANK", "NUMBER", "NESTED"]:
            with self.subTest(name=name):
                scan = store.check_reference(name)
                self.assertTrue(scan.exists)
                self.assertIsNone(scan.value)
        self.assertEqual(store.check_reference("OK").value, "fine")

    def test_a_wrapped_and_quoted_value_is_scanned_like_the_strict_parse(self):
        self.write_document('version: 1\nrefs:\n  K: "a\n    b"\n')
        self.assertEqual(CredentialsStore(self.path).check_reference("K").value, "a b")

    def test_a_block_scalar_is_scanned(self):
        self.write_document("version: 1\nrefs:\n  K: |\n    line one\n    line two\n")
        scan = CredentialsStore(self.path).check_reference("K")
        self.assertEqual(scan.value, "line one\nline two\n")

    def test_a_duplicate_entry_is_still_refused(self):
        self.write_document("version: 1\nrefs:\n  K: a\n  K: b\n")
        with self.assertRaises(CredentialsError) as caught:
            CredentialsStore(self.path).check_reference("K")
        self.assertIn("more than once", str(caught.exception))

    def test_a_flow_refs_mapping_is_still_refused(self):
        self.write_document("version: 1\nrefs: {K: x}\n")
        with self.assertRaises(CredentialsError):
            CredentialsStore(self.path).check_reference("K")

    def test_a_missing_document_raises(self):
        with self.assertRaises(DocumentNotFound):
            CredentialsStore(self.path).check_reference("K")

    def test_a_tab_indented_block_is_refused(self):
        self.write_document("version: 1\nrefs:\n\tK: a\n")
        with self.assertRaises(CredentialsError) as caught:
            CredentialsStore(self.path).check_reference("K")
        self.assertIn("tab", str(caught.exception))

    def test_the_scan_does_not_write(self):
        self.write_document(DOCUMENT)
        before = self.read_document()
        CredentialsStore(self.path).check_reference("ALPHA")
        self.assertEqual(self.read_document(), before)
        self.assertFalse(self.lock_path().exists())


class DeleteReferenceTests(TemporaryDocumentTest):
    """Tests for deleting one reference from the document."""

    def setUp(self):
        super().setUp()
        self.write_document(DOCUMENT)

    def store(self):
        return CredentialsStore(self.path)

    def test_a_delete_removes_only_its_entry(self):
        self.assertTrue(self.store().delete_reference("ALPHA"))
        text = self.read_document()
        self.assertNotIn("ALPHA", text)
        self.assertIn("BETA: 'single quoted'", text)
        self.assertIn('GAMMA: "double quoted"', text)
        self.assertIn("keep-me", text)

    def test_deleting_an_absent_reference_changes_nothing(self):
        before = self.read_document()
        self.assertFalse(self.store().delete_reference("MISSING"))
        self.assertEqual(self.read_document(), before)

    def test_a_comment_above_an_entry_goes_with_it(self):
        self.write_document("version: 1\nrefs:\n  # note A\n  A: a\n  B: b\n")
        self.store().delete_reference("A")
        self.assertEqual(self.read_document(), "version: 1\nrefs:\n  B: b\n")

    def test_a_comment_separated_by_a_blank_line_stays(self):
        self.write_document("version: 1\nrefs:\n  # section note\n\n  A: a\n  B: b\n")
        self.store().delete_reference("A")
        self.assertIn("# section note", self.read_document())

    def test_deleting_the_last_reference_keeps_the_section(self):
        self.write_document('version: 1\nrefs:\n  ONLY: "only"\n')
        self.assertTrue(self.store().delete_reference("ONLY"))
        text = self.read_document()
        self.assertNotIn("only", text)
        self.assertIn("refs:", text)

    def test_a_multi_line_value_is_removed_whole(self):
        self.write_document("version: 1\nrefs:\n  X: |\n    one\n    two\n  Y: y\n")
        self.store().delete_reference("X")
        text = self.read_document()
        self.assertNotIn("one", text)
        self.assertNotIn("two", text)
        self.assertIn("Y: y", text)

    def test_a_document_without_refs_reports_absent(self):
        self.write_document("version: 1\nrecords:\n  a/b:\n    kind: api-key\n")
        self.assertFalse(self.store().delete_reference("A"))
        self.assertIn("records:", self.read_document())

    def test_a_missing_document_reports_absent(self):
        self.path.unlink()
        self.assertFalse(self.store().delete_reference("A"))
        self.assertFalse(self.path.exists())

    def test_a_delete_rejects_a_malformed_name(self):
        with self.assertRaises(CredentialsError):
            self.store().delete_reference("1BAD")

    def test_a_concurrent_change_aborts_the_delete(self):
        original = self.read_document()
        real_fingerprint = credentials._document_fingerprint
        calls = {"count": 0}

        def fingerprint(path):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_fingerprint(path)
            return (0, 0, 0)

        with (
            mock.patch.object(credentials, "_document_fingerprint", fingerprint),
            self.assertRaises(DocumentChanged),
        ):
            self.store().delete_reference("ALPHA")
        self.assertEqual(self.read_document(), original)


class DeleteReferencesTests(TemporaryDocumentTest):
    """Tests for deleting several references in one pass."""

    def setUp(self):
        super().setUp()
        self.write_document(
            "version: 1\nrefs:\n  A: a\n  B: b\n  C: c\nrecords:\n  s/i:\n    kind: api-key\n"
        )

    def store(self):
        return CredentialsStore(self.path)

    def test_a_batch_removes_every_named_entry(self):
        removed = self.store().delete_references(["A", "C"])
        self.assertEqual(removed, ["A", "C"])
        text = self.read_document()
        self.assertNotIn("A: a", text)
        self.assertNotIn("C: c", text)
        self.assertIn("B: b", text)

    def test_a_batch_reports_only_the_names_that_were_present(self):
        removed = self.store().delete_references(["A", "MISSING"])
        self.assertEqual(removed, ["A"])

    def test_a_batch_with_no_present_name_writes_nothing(self):
        before = self.read_document()
        self.assertEqual(self.store().delete_references(["X", "Y"]), [])
        self.assertEqual(self.read_document(), before)

    def test_a_batch_keeps_the_records_section(self):
        self.store().delete_references(["A", "B", "C"])
        text = self.read_document()
        self.assertIn("records:", text)
        self.assertIn("s/i:", text)
        self.assertIn("refs:\n", text)

    def test_a_batch_removes_comments_with_their_entries(self):
        self.write_document(
            "version: 1\nrefs:\n  # note A\n  A: a\n  # note B\n  B: b\n"
        )
        self.store().delete_references(["A", "B"])
        self.assertEqual(self.read_document(), "version: 1\nrefs:\n")

    def test_a_batch_rejects_a_malformed_name_before_writing(self):
        before = self.read_document()
        with self.assertRaises(CredentialsError):
            self.store().delete_references(["A", "1BAD"])
        self.assertEqual(self.read_document(), before)

    def test_a_batch_of_no_names_writes_nothing(self):
        before = self.read_document()
        self.assertEqual(self.store().delete_references([]), [])
        self.assertEqual(self.read_document(), before)

    def test_the_names_come_back_in_document_order(self):
        self.assertEqual(self.store().reference_names(), ["A", "B", "C"])

    def test_names_of_a_missing_document_raise(self):
        self.path.unlink()
        with self.assertRaises(DocumentNotFound):
            self.store().reference_names()


if __name__ == "__main__":
    unittest.main()
