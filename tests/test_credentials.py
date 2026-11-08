"""Tests for the credential document store."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dsh_credentials_helper import credentials

DOCUMENT = """version: 2
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


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp(prefix="cred-store-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(directory, ignore_errors=True)
        )
        self.directory = directory
        self.path = directory / ".credentials.yaml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, 0o600)

    def store(self):
        return credentials.CredentialsStore(self.path)

    def test_it_reads_plain_single_and_double_quoted_values(self):
        self.write(DOCUMENT)
        self.assertEqual(self.store().read_reference("ALPHA"), "plain-secret")
        self.assertEqual(self.store().read_reference("BETA"), "single quoted")
        self.assertEqual(self.store().read_reference("GAMMA"), "double quoted")

    def test_it_reads_block_scalars_escapes_and_comments(self):
        lf = chr(10)
        quote = chr(34)
        backslash = chr(92)
        document = (
            "version: 2"
            + lf
            + "refs:"
            + lf
            + "  PLAIN: plain secret"
            + lf
            + "  SINGLE: 'it''s quoted'"
            + lf
            + "  DOUBLE: "
            + quote
            + "a"
            + backslash
            + "tb"
            + backslash
            + "nc"
            + backslash
            + quote
            + "d"
            + quote
            + lf
            + "  HEX: "
            + quote
            + backslash
            + "x41"
            + backslash
            + "u00e9"
            + quote
            + lf
            + "  BLOCK: |"
            + lf
            + "    line one"
            + lf
            + "    line two"
            + lf
            + "  STRIPPED: |-"
            + lf
            + "    no final newline"
            + lf
            + "  FOLD: >"
            + lf
            + "    folded one"
            + lf
            + "    folded two"
            + lf
            + "  COMMENTED: value # trailing comment"
            + lf
            + "  INNER: abc#def"
            + lf
            + "records:"
            + lf
            + "  scope/id:"
            + lf
            + "    token: x"
            + lf
        )
        self.write(document)
        store = self.store()
        self.assertEqual(store.read_reference("PLAIN"), "plain secret")
        self.assertEqual(store.read_reference("SINGLE"), "it's quoted")
        self.assertEqual(
            store.read_reference("DOUBLE"), "a" + chr(9) + "b" + lf + "c" + quote + "d"
        )
        self.assertEqual(store.read_reference("HEX"), "A\u00e9")
        self.assertEqual(
            store.read_reference("BLOCK"), "line one" + lf + "line two" + lf
        )
        self.assertEqual(store.read_reference("STRIPPED"), "no final newline")
        self.assertEqual(store.read_reference("FOLD"), "folded one folded two" + lf)
        self.assertEqual(store.read_reference("COMMENTED"), "value")
        self.assertEqual(store.read_reference("INNER"), "abc#def")

    def test_a_missing_reference_is_reported(self):
        self.write(DOCUMENT)
        self.assertIsNone(self.store().peek_reference("MISSING"))
        with self.assertRaises(credentials.ReferenceNotFound):
            self.store().read_reference("MISSING")

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(credentials.CredentialsError) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("not found", str(caught.exception))
        with self.assertRaises(credentials.CredentialsError):
            self.store().set_reference("ALPHA", "new-secret")

    def test_it_updates_one_reference_and_preserves_the_rest(self):
        self.write(DOCUMENT)
        store = self.store()
        before = {
            name: store.read_reference(name) for name in ("ALPHA", "BETA", "GAMMA")
        }
        self.assertTrue(store.set_reference("ALPHA", "new-secret"))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("version: 2", text)
        self.assertIn("# top level comment", text)
        self.assertIn("BETA: 'single quoted'", text)
        self.assertIn('GAMMA: "double quoted"', text)
        self.assertIn("token: keep-me", text)
        self.assertEqual(store.read_reference("ALPHA"), "new-secret")
        self.assertEqual(store.read_reference("BETA"), before["BETA"])
        self.assertEqual(store.read_reference("GAMMA"), before["GAMMA"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_it_adds_a_new_reference_inside_the_refs_block(self):
        self.write(DOCUMENT)
        self.assertTrue(self.store().set_reference("DELTA", "new-value"))
        text = self.path.read_text(encoding="utf-8")
        self.assertGreater(text.index("DELTA:"), text.index("GAMMA:"))
        self.assertLess(text.index("DELTA:"), text.index("records:"))
        self.assertEqual(self.store().read_reference("DELTA"), "new-value")

    def test_an_identical_value_is_not_written(self):
        self.write(DOCUMENT)
        before = self.path.read_text(encoding="utf-8")
        self.assertFalse(self.store().set_reference("ALPHA", "plain-secret"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_yaml_looking_values_round_trip_as_strings(self):
        self.write(DOCUMENT)
        values = ["true", "null", "123", "#not a comment", "a: b", " padded "]
        for index, value in enumerate(values):
            name = "VALUE" + str(index)
            self.assertTrue(self.store().set_reference(name, value))
            self.assertEqual(self.store().read_reference(name), value)

    def test_a_value_with_a_newline_round_trips(self):
        self.write(DOCUMENT)
        value = "line one" + chr(10) + "line two"
        self.assertTrue(self.store().set_reference("ALPHA", value))
        self.assertEqual(self.store().read_reference("ALPHA"), value)

    def test_quotes_backslashes_and_unicode_round_trip(self):
        self.write(DOCUMENT)
        value = "a" + chr(92) + "b" + chr(34) + "c" + "d\u00e9lta" + chr(9)
        self.assertTrue(self.store().set_reference("ALPHA", value))
        self.assertEqual(self.store().read_reference("ALPHA"), value)

    def test_invalid_names_and_empty_values_are_refused(self):
        self.write(DOCUMENT)
        for name in ["", "1BAD", "BAD-NAME", "BAD NAME"]:
            with self.assertRaises(credentials.CredentialsError):
                self.store().set_reference(name, "value")
        with self.assertRaises(credentials.CredentialsError):
            self.store().set_reference("ALPHA", "")

    def test_the_document_mode_is_tightened_to_owner_only(self):
        self.write(DOCUMENT)
        os.chmod(self.path, 0o644)
        self.store().set_reference("ALPHA", "new-secret")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_crlf_line_endings_survive(self):
        self.write(DOCUMENT.replace(chr(10), chr(13) + chr(10)))
        self.store().set_reference("ALPHA", "new-secret")
        text = self.path.read_bytes().decode("utf-8")
        self.assertEqual(text.count(chr(10)), text.count(chr(13) + chr(10)))

    def test_a_duplicate_reference_is_refused(self):
        self.write("version: 2\nrefs:\n  ALPHA: first\n  ALPHA: second\n")
        with self.assertRaises(credentials.CredentialsError) as caught:
            self.store().read_reference("ALPHA")
        self.assertIn("more than once", str(caught.exception))
        with self.assertRaises(credentials.CredentialsError):
            self.store().set_reference("ALPHA", "new-secret")

    def test_a_parser_error_does_not_echo_the_document(self):
        self.write("version: 2\nrefs:\n  LEAKY_SECRET: 'unterminated\n")
        with self.assertRaises(credentials.CredentialsError) as caught:
            self.store().read_reference("LEAKY_SECRET")
        message = str(caught.exception)
        self.assertNotIn("LEAKY_SECRET", message)

    def test_a_non_string_reference_is_rejected(self):
        self.write("version: 2\nrefs:\n  NUMBER: 5\n  FLAG: true\n")
        with self.assertRaises(credentials.CredentialsError) as caught:
            self.store().read_reference("NUMBER")
        self.assertIn("not a string", str(caught.exception))
        with self.assertRaises(credentials.CredentialsError):
            self.store().read_reference("FLAG")

    def test_a_multiline_plain_value_is_rejected(self):
        self.write("version: 2\nrefs:\n  MULTI: first\n    second\n")
        with self.assertRaises(credentials.CredentialsError):
            self.store().read_reference("MULTI")

    def test_a_flow_refs_mapping_is_rejected(self):
        self.write("version: 2\nrefs: {}\n")
        with self.assertRaises(credentials.CredentialsError):
            self.store().read_reference("ALPHA")

    def test_a_document_without_refs_is_rejected(self):
        self.write("version: 2\nrecords:\n  scope/id:\n    token: x\n")
        with self.assertRaises(credentials.CredentialsError):
            self.store().read_reference("ALPHA")

    def test_a_document_that_changes_before_the_write_is_refused(self):
        self.write(DOCUMENT)
        with (
            mock.patch.object(
                credentials, "_document_fingerprint", side_effect=[(1, 1, 1), (2, 2, 2)]
            ),
            self.assertRaises(credentials.DocumentChanged),
        ):
            self.store().set_reference("ALPHA", "new-secret")
        self.assertIn("ALPHA: plain-secret", self.path.read_text(encoding="utf-8"))

    def test_no_temporary_file_is_left_behind(self):
        self.write(DOCUMENT)
        self.store().set_reference("ALPHA", "new-secret")
        leftovers = [
            item.name for item in self.directory.iterdir() if item.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
