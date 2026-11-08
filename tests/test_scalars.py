"""Tests for the YAML scalar subset the document parser understands."""

from __future__ import annotations

import unittest

from support import TemporaryDocumentTest

from dsh_credentials_helper.credentials import CredentialsError, CredentialsStore

DOCUMENT_TEMPLATE = "version: 1\nrefs:\n{entry}\n"


class ScalarStyleTests(TemporaryDocumentTest):
    def read_value(self, entry):
        self.write_document(DOCUMENT_TEMPLATE.format(entry=entry))
        return CredentialsStore(self.path).read_reference("K")

    def assert_value(self, entry, expected):
        self.assertEqual(self.read_value(entry), expected, entry)

    def test_plain_single_line(self):
        self.assert_value("  K: plain-value", "plain-value")

    def test_plain_wrapped_at_a_space(self):
        self.assert_value("  K: word one\n    word two", "word one word two")

    def test_plain_blank_line_becomes_a_newline(self):
        self.assert_value("  K: first\n\n    second", "first\nsecond")

    def test_plain_comment_ends_the_value(self):
        self.assert_value("  K: first\n    second # trailing comment", "first second")

    def test_single_quoted(self):
        self.assert_value("  K: 'single value'", "single value")

    def test_single_quoted_doubled_quote(self):
        self.assert_value("  K: 'it''s quoted'", "it's quoted")

    def test_single_quoted_wrapped(self):
        self.assert_value("  K: 'a\n    b'", "a b")

    def test_single_quoted_blank_line(self):
        self.assert_value("  K: 'a\n\n    b'", "a\nb")

    def test_double_quoted_escapes(self):
        self.assert_value('  K: "t\\tb\\nq\\"back\\\\slash"', 't\tb\nq"back\\slash')

    def test_double_quoted_hex_and_unicode_escapes(self):
        self.assert_value('  K: "\\x41\\u00e9"', "A\u00e9")

    def test_double_quoted_wrapped(self):
        self.assert_value('  K: "a\n    b"', "a b")

    def test_double_quoted_escaped_line_break_joins_without_a_space(self):
        self.assert_value('  K: "a\\\n    b"', "ab")

    def test_block_literal(self):
        self.assert_value("  K: |\n    line one\n    line two", "line one\nline two\n")

    def test_block_literal_strip(self):
        self.assert_value("  K: |-\n    no final newline", "no final newline")

    def test_block_literal_keep(self):
        self.assert_value("  K: |+\n    kept\n\n", "kept\n\n\n")

    def test_block_folded(self):
        self.assert_value(
            "  K: >\n    folded one\n    folded two", "folded one folded two\n"
        )

    def test_block_folded_blank_line(self):
        self.assert_value("  K: >\n    a\n\n    b", "a\nb\n")

    def test_block_indentation_indicator(self):
        self.assert_value(
            "  K: |2-\n      two spaces\n    normal", "  two spaces\nnormal"
        )

    def test_block_keep_before_the_next_entry(self):
        self.write_document('version: 1\nrefs:\n  K: |+\n    kept\n\n  J: "j"\n')
        store = CredentialsStore(self.path)
        self.assertEqual(store.read_reference("K"), "kept\n\n")
        self.assertEqual(store.read_reference("J"), "j")

    def test_block_literal_preserves_indented_lines(self):
        self.assert_value("  K: |\n    first\n      indented", "first\n  indented\n")


class ScalarErrorTests(TemporaryDocumentTest):
    def read_with(self, entry):
        self.write_document(DOCUMENT_TEMPLATE.format(entry=entry))
        return CredentialsStore(self.path).read_reference("K")

    def assert_refused(self, entry, message):
        with self.assertRaises(CredentialsError) as caught:
            self.read_with(entry)
        self.assertIn(message, str(caught.exception))

    def test_an_unterminated_single_quoted_value_is_refused(self):
        self.assert_refused("  K: 'open", "unterminated single-quoted")

    def test_an_unterminated_double_quoted_value_is_refused(self):
        self.assert_refused('  K: "open', "unterminated double-quoted")

    def test_a_dangling_escape_is_refused(self):
        self.assert_refused('  K: "value\\', "dangling escape")

    def test_an_unsupported_escape_is_refused(self):
        self.assert_refused('  K: "value\\q"', "unsupported escape")

    def test_an_invalid_hex_escape_is_refused(self):
        self.assert_refused('  K: "\\xzz"', "invalid hexadecimal escape")

    def test_an_escaped_break_needs_an_indented_line(self):
        self.assert_refused('  K: "a\\\n  b"', "escaped line break")

    def test_a_nested_mapping_is_refused(self):
        self.assert_refused("  K:\n    NESTED: value", "nested values")

    def test_a_missing_value_is_refused(self):
        self.assert_refused("  K:", "no scalar value")

    def test_a_null_value_is_refused(self):
        self.assert_refused("  K: null", "not a string")

    def test_a_number_value_is_refused(self):
        self.assert_refused("  K: 5", "not a string")

    def test_an_unsupported_block_scalar_header_is_refused(self):
        self.assert_refused("  K: |x\n    value", "unsupported block scalar header")

    def test_unexpected_text_after_a_quoted_value_is_refused(self):
        self.assert_refused("  K: 'value' trailing", "unexpected text")


if __name__ == "__main__":
    unittest.main()
