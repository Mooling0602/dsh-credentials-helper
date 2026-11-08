"""The YAML scalar subset the credential document uses.

DeepSeek Harness writes ``<DSH_HOME>/.credentials.yaml`` with a full
YAML implementation, so a ``refs`` value may arrive in every style that
writer emits: plain, single-quoted, double-quoted and literal or folded
block scalars. Long values are wrapped across lines — a plain or single-
quoted scalar folds at a space, a double-quoted scalar continues after a
backslash, and a block scalar uses its own indentation. This module
parses exactly that subset and refuses any other construct instead of
guessing at its meaning.

The module also renders one scalar for the store. Every value is emitted
as a single-line double-quoted scalar, the form the harness reads back
unchanged and the form that cannot be broken by the value's own content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._errors import CredentialsError

LF = "\n"
CR = "\r"
TAB = "\t"
BACKSLASH = "\\"
DOUBLE_QUOTE = '"'
SINGLE_QUOTE = "'"
HASH = "#"

KEY_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*:(.*)$")
"""Pattern of one key and the text after its colon."""

_INDENT_CHARACTERS = " \t"
_COMMENT_PATTERN = re.compile(r"[ \t]#")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ESCAPE_WIDTHS = {"x": 2, "u": 4, "U": 8}
_SIMPLE_ESCAPES = {
    "0": chr(0),
    "a": chr(7),
    "b": chr(8),
    "t": TAB,
    "n": LF,
    "v": chr(11),
    "f": chr(12),
    "r": CR,
    "e": chr(27),
    " ": " ",
    DOUBLE_QUOTE: DOUBLE_QUOTE,
    "/": "/",
    BACKSLASH: BACKSLASH,
    "N": chr(0x85),
    "_": chr(0xA0),
    "L": chr(0x2028),
    "P": chr(0x2029),
}
"""Single-character YAML escape sequences supported in double-quoted values."""


@dataclass(frozen=True)
class RefEntry:
    """One reference entry and the document lines it occupies.

    :param value: Decoded secret value.
    :type value: str
    :param first_line: Index of the entry's first line.
    :type first_line: int
    :param next_line: Index one past the entry's last line.
    :type next_line: int
    """

    value: str
    first_line: int
    next_line: int


def leading_whitespace(line: str) -> str:
    """Return the leading space and tab characters of one line.

    :param line: One line without its line ending.
    :type line: str
    :returns: The leading indentation.
    :rtype: str
    """
    return line[: len(line) - len(line.lstrip(_INDENT_CHARACTERS))]


def strip_comment(text: str) -> str:
    """Remove a trailing YAML comment from one scalar or header.

    :param text: Text after an entry colon or a block scalar indicator.
    :type text: str
    :returns: The text before the comment marker.
    :rtype: str
    """
    match = _COMMENT_PATTERN.search(text)
    if match is None:
        return text
    return text[: match.start()]


def line_error(path: Path, index: int, detail: str) -> CredentialsError:
    """Build a parse error that names the path and line but never its content.

    :param path: Document path.
    :type path: pathlib.Path
    :param index: Zero-based line index.
    :type index: int
    :param detail: Description of the failed construct.
    :type detail: str
    :returns: The error to raise.
    :rtype: CredentialsError
    """
    return CredentialsError(f"cannot parse {path}: {detail} at line {index + 1}")


def parse_refs(
    lines: list[str],
    start: int,
    end: int,
    path: Path,
) -> dict[str, RefEntry]:
    """Parse the refs mapping with a strict YAML scalar subset.

    Blank lines and comments inside the block are ignored, and a value
    may continue over the following more-indented lines, matching how
    the harness writer wraps long values.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: First line of the refs block.
    :type start: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: Reference names mapped to their decoded entries.
    :rtype: dict[str, RefEntry]
    :raises CredentialsError: When the block is not a mapping of
        strings.
    """
    entries: dict[str, RefEntry] = {}
    indent: str | None = None
    index = start
    while index < end:
        line = lines[index]
        stripped = line.strip()
        if stripped == "" or stripped.startswith(HASH):
            index += 1
            continue
        leading = leading_whitespace(line)
        if TAB in leading:
            raise line_error(
                path,
                index,
                "the refs entry is indented with a tab; YAML indentation must use spaces",
            )
        if indent is None:
            if leading == "":
                raise line_error(path, index, "the refs entry is not indented")
            indent = leading
        elif leading != indent:
            raise line_error(path, index, "unexpected indentation in the refs block")
        match = KEY_PATTERN.match(line[len(indent) :])
        if match is None:
            raise line_error(path, index, "the refs entry is not a plain string key")
        key, raw = match.group(1), match.group(2).lstrip(_INDENT_CHARACTERS)
        if key in entries:
            raise line_error(
                path, index, f'reference "{key}" is declared more than once'
            )
        value, next_index = _parse_value(lines, index, end, indent, raw, path)
        entries[key] = RefEntry(value, index, next_index)
        index = next_index
    return entries


def encode_scalar(value: str) -> str:
    """Render one string as a single-line YAML double-quoted scalar.

    Every special character is escaped, so the emitted scalar stays on
    one line and reads back identically in the harness's YAML parser.

    :param value: The string to render.
    :type value: str
    :returns: A double-quoted YAML scalar.
    :rtype: str
    """
    characters = [DOUBLE_QUOTE]
    for character in value:
        code = ord(character)
        if character == BACKSLASH:
            characters.append(BACKSLASH + BACKSLASH)
        elif character == DOUBLE_QUOTE:
            characters.append(BACKSLASH + DOUBLE_QUOTE)
        elif character == LF:
            characters.append(BACKSLASH + "n")
        elif character == CR:
            characters.append(BACKSLASH + "r")
        elif character == TAB:
            characters.append(BACKSLASH + "t")
        elif code < 0x20 or code == 0x7F or code in (0x85, 0x2028, 0x2029):
            if code <= 0xFF:
                characters.append(BACKSLASH + "x" + format(code, "02x"))
            else:
                characters.append(BACKSLASH + "u" + format(code, "04x"))
        else:
            characters.append(character)
    characters.append(DOUBLE_QUOTE)
    return "".join(characters)


def _parse_value(
    lines: list[str],
    index: int,
    end: int,
    indent: str,
    raw: str,
    path: Path,
) -> tuple[str, int]:
    """Decode one refs value and return the next line index.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Text after the entry colon, without leading spaces.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the scalar style is outside the
        subset.
    """
    if raw == "" or raw.startswith(HASH):
        raise line_error(path, index, _empty_value_detail(lines, index, end, indent))
    first = raw[0]
    if first in ("|", ">"):
        return _parse_block_scalar(lines, index, end, indent, raw, path)
    if first == SINGLE_QUOTE:
        return _parse_single_quoted(lines, index, end, indent, raw, path)
    if first == DOUBLE_QUOTE:
        return _parse_double_quoted(lines, index, end, indent, raw, path)
    return _parse_plain(lines, index, end, indent, raw, path)


def _empty_value_detail(lines: list[str], index: int, end: int, indent: str) -> str:
    """Describe why a refs entry has no scalar value.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :returns: The error detail.
    :rtype: str
    """
    cursor = index + 1
    while cursor < end:
        line = lines[cursor]
        if line.strip() == "" or line.lstrip(_INDENT_CHARACTERS).startswith(HASH):
            cursor += 1
            continue
        if len(leading_whitespace(line)) > len(indent):
            return "nested values are not supported in the refs block"
        break
    return "the refs entry has no scalar value"


def _plain_content(text: str) -> tuple[str, bool]:
    """Split one plain scalar line into content and comment.

    :param text: Scalar text on one line.
    :type text: str
    :returns: The trimmed content and whether a comment followed.
    :rtype: tuple[str, bool]
    """
    uncommented = strip_comment(text)
    return uncommented.strip(_INDENT_CHARACTERS), uncommented != text


def _parse_plain(
    lines: list[str],
    index: int,
    end: int,
    indent: str,
    raw: str,
    path: Path,
) -> tuple[str, int]:
    """Decode one plain scalar, folding wrapped continuation lines.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Text after the entry colon, without leading spaces.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the value is not a string.
    """
    first, ended = _plain_content(raw)
    pieces = [first]
    pending_breaks = 0
    cursor = index + 1
    while not ended and cursor < end:
        line = lines[cursor]
        if line.strip() == "":
            pending_breaks += 1
            cursor += 1
            continue
        leading = leading_whitespace(line)
        if len(leading) <= len(indent):
            break
        content = line[len(leading) :]
        if content.startswith(HASH):
            break
        piece, ended = _plain_content(content)
        if piece == "" and not ended:
            pending_breaks += 1
            cursor += 1
            continue
        if pending_breaks:
            pieces.append(LF * pending_breaks)
            pending_breaks = 0
        else:
            pieces.append(" ")
        pieces.append(piece)
        cursor += 1
    value = "".join(pieces)
    if _plain_is_not_a_string(value):
        raise line_error(path, index, "the refs entry is not a string")
    return value, cursor


def _plain_is_not_a_string(value: str) -> bool:
    """Report whether a plain scalar would parse as null or a number.

    :param value: Plain scalar text.
    :type value: str
    :returns: True when the value is not a YAML string.
    :rtype: bool
    """
    if value.lower() in ("null", "~", "true", "false"):
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def _fold_quoted_break(
    lines: list[str],
    cursor: int,
    end: int,
    indent: str,
    path: Path,
    index: int,
    characters: list[str],
    style: str,
) -> tuple[int, str, int]:
    """Consume one line break inside a quoted scalar and fold it.

    A break between content lines folds to one space, and blank lines
    fold to that many newlines. Trailing whitespace before the break is
    dropped, as the YAML folding rules require.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param cursor: Index of the line that just ended.
    :type cursor: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :param index: Entry line index used in error messages.
    :type index: int
    :param characters: Decoded characters collected so far.
    :type characters: list[str]
    :param style: Scalar style named in an error message.
    :type style: str
    :returns: The next line index, its content and the next position.
    :rtype: tuple[int, str, int]
    :raises CredentialsError: When the scalar has no closing quote.
    """
    next_cursor = cursor + 1
    blank_lines = 0
    while next_cursor < end and lines[next_cursor].strip() == "":
        blank_lines += 1
        next_cursor += 1
    if next_cursor >= end:
        raise line_error(
            path,
            index,
            f"unterminated {style} value; the closing quote is missing",
        )
    leading = leading_whitespace(lines[next_cursor])
    if len(leading) <= len(indent):
        raise line_error(
            path,
            index,
            f"unterminated {style} value; its continuation lines must be indented",
        )
    while characters and characters[-1] in (" ", TAB):
        characters.pop()
    characters.append(LF * blank_lines if blank_lines else " ")
    return next_cursor, lines[next_cursor][len(leading) :], 0


def _parse_single_quoted(
    lines: list[str],
    index: int,
    end: int,
    indent: str,
    raw: str,
    path: Path,
) -> tuple[str, int]:
    """Decode one single-quoted scalar, folding wrapped continuation lines.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Text after the entry colon, without leading spaces.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the scalar is malformed or
        unterminated.
    """
    characters: list[str] = []
    cursor = index
    text = raw
    position = 1
    while True:
        while position < len(text):
            character = text[position]
            if character == SINGLE_QUOTE:
                if position + 1 < len(text) and text[position + 1] == SINGLE_QUOTE:
                    characters.append(SINGLE_QUOTE)
                    position += 2
                    continue
                tail = strip_comment(text[position + 1 :]).strip()
                if tail != "":
                    raise line_error(
                        path, index, "unexpected text after a single-quoted value"
                    )
                return "".join(characters), cursor + 1
            characters.append(character)
            position += 1
        cursor, text, position = _fold_quoted_break(
            lines, cursor, end, indent, path, index, characters, "single-quoted"
        )


def _parse_double_quoted(
    lines: list[str],
    index: int,
    end: int,
    indent: str,
    raw: str,
    path: Path,
) -> tuple[str, int]:
    """Decode one double-quoted scalar, including escapes and wrapping.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Text after the entry colon, without leading spaces.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the escape or scalar is malformed.
    """
    characters: list[str] = []
    cursor = index
    text = raw
    position = 1
    while True:
        while position < len(text):
            character = text[position]
            if character == DOUBLE_QUOTE:
                tail = strip_comment(text[position + 1 :]).strip()
                if tail != "":
                    raise line_error(
                        path, index, "unexpected text after a double-quoted value"
                    )
                return "".join(characters), cursor + 1
            if character != BACKSLASH:
                characters.append(character)
                position += 1
                continue
            position += 1
            if position >= len(text):
                cursor, text, position = _consume_escaped_break(
                    lines, cursor, end, indent, path, index
                )
                continue
            escape = text[position]
            if escape in _SIMPLE_ESCAPES:
                characters.append(_SIMPLE_ESCAPES[escape])
                position += 1
                continue
            if escape in _ESCAPE_WIDTHS:
                width = _ESCAPE_WIDTHS[escape]
                digits = text[position + 1 : position + 1 + width]
                if len(digits) != width or any(
                    item not in _HEX_DIGITS for item in digits
                ):
                    raise line_error(
                        path,
                        index,
                        "invalid hexadecimal escape in a double-quoted value",
                    )
                try:
                    characters.append(chr(int(digits, 16)))
                except ValueError:
                    raise line_error(
                        path, index, "invalid code point in a double-quoted value"
                    ) from None
                position += 1 + width
                continue
            raise line_error(path, index, "unsupported escape in a double-quoted value")
        cursor, text, position = _fold_quoted_break(
            lines, cursor, end, indent, path, index, characters, "double-quoted"
        )


def _consume_escaped_break(
    lines: list[str],
    cursor: int,
    end: int,
    indent: str,
    path: Path,
    index: int,
) -> tuple[int, str, int]:
    """Consume one backslash-escaped line break in a double-quoted scalar.

    The break and the next line's indentation are removed, so the two
    sides join without a space.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param cursor: Index of the line that just ended.
    :type cursor: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :param index: Entry line index used in error messages.
    :type index: int
    :returns: The next line index, its content and the next position.
    :rtype: tuple[int, str, int]
    :raises CredentialsError: When no indented line follows the escape.
    """
    next_cursor = cursor + 1
    if next_cursor >= end:
        raise line_error(
            path, index, "the double-quoted value ends with a dangling escape"
        )
    leading = leading_whitespace(lines[next_cursor])
    if len(leading) <= len(indent):
        raise line_error(
            path,
            index,
            "an escaped line break must be followed by an indented line",
        )
    return next_cursor, lines[next_cursor][len(leading) :], 0


def _parse_block_scalar(
    lines: list[str],
    index: int,
    end: int,
    indent: str,
    raw: str,
    path: Path,
) -> tuple[str, int]:
    """Decode one literal or folded block scalar with its chomping.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Index of the entry line.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Block scalar header, starting with a pipe or a greater-
        than sign.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the header or the content is
        malformed.
    """
    indicator = raw[0]
    header = strip_comment(raw[1:]).strip()
    chomp = ""
    content_offset = 0
    for character in header:
        if character in ("+", "-") and chomp == "":
            chomp = character
        elif character.isdigit():
            offset = int(character)
            if offset == 0 or content_offset:
                raise line_error(path, index, "unsupported block scalar header")
            content_offset = offset
        else:
            raise line_error(path, index, "unsupported block scalar header")

    content: list[str] = []
    cursor = index + 1
    while cursor < end:
        line = lines[cursor]
        if line.strip() == "":
            content.append("")
            cursor += 1
            continue
        leading = leading_whitespace(line)
        if len(leading) <= len(indent):
            break
        content.append(line)
        cursor += 1

    if content_offset:
        content_indent = len(indent) + content_offset
    else:
        content_indent = 0
        for line in content:
            if line.strip() != "":
                content_indent = len(leading_whitespace(line))
                break
    stripped: list[str] = []
    for line in content:
        if line.strip() == "":
            stripped.append("")
            continue
        leading = leading_whitespace(line)
        if len(leading) < content_indent:
            raise line_error(path, index, "inconsistent indentation in a block scalar")
        stripped.append(line[content_indent:])

    if indicator == "|":
        value = LF.join(stripped)
        if content:
            value += LF
    else:
        value = _fold_lines(stripped)
    return _chomp(value, chomp), cursor


def _fold_lines(lines: list[str]) -> str:
    """Apply YAML folded-scalar line folding.

    :param lines: Block content lines without their common indentation.
    :type lines: list[str]
    :returns: The folded text, including its final line breaks.
    :rtype: str
    """
    body = list(lines)
    trailing = 0
    while body and body[-1] == "":
        body.pop()
        trailing += 1
    parts: list[str] = []
    pending_breaks = 0
    for line in body:
        if line == "":
            pending_breaks += 1
            continue
        if parts:
            parts.append(LF * pending_breaks if pending_breaks else " ")
        parts.append(line)
        pending_breaks = 0
    folded = "".join(parts)
    if lines:
        folded += LF * (1 + trailing)
    return folded


def _chomp(value: str, chomp: str) -> str:
    """Apply the block scalar chomping indicator to decoded text.

    :param value: Decoded text with all of its final line breaks.
    :type value: str
    :param chomp: One of the chomping indicators, or an empty string.
    :type chomp: str
    :returns: The value after chomping.
    :rtype: str
    """
    if chomp == "+":
        return value
    stripped = value.rstrip(LF)
    if chomp == "-":
        return stripped
    return stripped + LF if stripped else ""
