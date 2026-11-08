"""Tests for the value mask used in confirmation output."""

from __future__ import annotations

import unittest

from dsh_credentials_helper.masking import mask_value


class MaskValueTests(unittest.TestCase):
    def test_an_empty_value_is_named(self):
        self.assertEqual(mask_value(""), "(empty)")

    def test_short_values_reveal_nothing(self):
        for length in range(1, 13):
            value = "abcdefghijkl"[:length]
            masked = mask_value(value)
            self.assertEqual(masked, "*" * 8, length)
            for character in set(value):
                self.assertNotIn(character, masked, length)

    def test_a_thirteen_character_value_shows_three_and_three(self):
        self.assertEqual(mask_value("abcdefghijklm"), "abc...klm")

    def test_a_fifteen_character_value_shows_three_and_three(self):
        self.assertEqual(mask_value("abcdefghijklmno"), "abc...mno")

    def test_a_sixteen_character_value_shows_four_and_four(self):
        self.assertEqual(mask_value("abcdefghijklmnop"), "abcd...mnop")

    def test_a_long_value_shows_four_and_four(self):
        self.assertEqual(mask_value("a" * 64), "aaaa...aaaa")

    def test_the_visible_part_never_exceeds_half_the_value(self):
        for length in range(13, 65):
            value = "".join(chr(ord("a") + index % 26) for index in range(length))
            masked = mask_value(value)
            visible = sum(1 for character in value if character in masked)
            self.assertLessEqual(visible * 2, length, masked)
            self.assertNotIn(value, masked, length)


if __name__ == "__main__":
    unittest.main()
