"""Masking for values that appear in confirmation output.

The only place that deliberately prints a decoded credential value is
/cred read. Every other surface, including the confirmation prompt and
the success message, goes through this module.
"""

from __future__ import annotations


def mask_value(value: str) -> str:
    """Return a short masked form of one secret value.

    Values of twelve characters or fewer reveal nothing. Longer values
    keep a three- or four-character prefix and suffix so the operator
    can recognise which credential is about to change without the full
    value reaching the chat card or a log. The visible part never
    exceeds half of a value, so a short secret is not mostly readable
    from its mask.

    :param value: The secret value to mask.
    :type value: str
    :returns: A masked representation that shows at most eight
        characters of the original value.
    :rtype: str
    """
    if not value:
        return "(empty)"
    length = len(value)
    if length <= 12:
        return "*" * 8
    visible = 3 if length < 16 else 4
    return f"{value[:visible]}...{value[-visible:]}"
