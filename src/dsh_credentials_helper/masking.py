"""Masking for values that appear in confirmation output.

The only place that deliberately prints a decoded credential value is
/cred read. Every other surface, including the confirmation prompt and
the success message, goes through this module.
"""

from __future__ import annotations


def mask_value(value: str) -> str:
    """Return a short masked form of one secret value.

    Values of eight characters or fewer reveal nothing. Longer values
    keep a short prefix and suffix so the operator can recognise which
    credential is about to change without the full value reaching the
    chat card or a log.

    :param value: The secret value to mask.
    :type value: str
    :returns: A masked representation with at most 11 characters of the
        original value.
    :rtype: str
    """
    if not value:
        return "(empty)"
    length = len(value)
    if length <= 8:
        return "*" * 8
    visible = 4 if length >= 16 else 3
    return f"{value[:visible]}...{value[-visible:]}"
