"""The credential document store.

The document at <DSH_HOME>/.credentials.yaml is owned by DeepSeek
Harness. Its refs mapping holds environment-variable-style names mapped
to secret strings, and its records mapping holds provider state. This
module only ever rewrites the one reference line it is asked to change;
every other byte of the document, including comments and the formatting
of neighbouring entries, is kept.

The module parses the refs block itself so the plugin needs no third-
party runtime dependency. Scalar styles the writer emits are supported:
plain, single-quoted, double-quoted and block literals. A construct
outside that subset is refused instead of guessed at.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

REFERENCE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""Grammar DeepSeek Harness uses for a credential reference name."""

REFS_SECTION = "refs"
"""Top-level section that holds the reference mapping."""

LF = chr(10)
CR = chr(13)
TAB = chr(9)
BACKSLASH = chr(92)
DOUBLE_QUOTE = chr(34)
SINGLE_QUOTE = chr(39)
HASH = "#"

_KEY_LINE_PATTERN = re.compile("^([A-Za-z_][A-Za-z0-9_]*)[ " + TAB + "]*:(.*)$")
_OWNER_ONLY_MODE = 0o600
"""Permission bits of every document this module writes."""

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


class CredentialsError(RuntimeError):
    """Raised when the document cannot be read or updated safely."""


class ReferenceNotFound(CredentialsError):
    """Raised when the requested reference is absent from the document."""


class DocumentChanged(CredentialsError):
    """Raised when the document changed between the read and the write."""


def validate_reference_name(name: str) -> None:
    """Validate one credential reference name.

    :param name: Candidate reference name.
    :type name: str
    :raises CredentialsError: When the name is outside the harness
        grammar.
    """
    if not REFERENCE_PATTERN.fullmatch(name):
        raise CredentialsError(
            f'invalid reference name "{name}"; use letters, digits and '
            "underscores, starting with a letter or underscore"
        )


class CredentialsStore:
    """Reads and updates one credential document.

    The store resolves the document path lazily, so a path problem
    becomes a command error instead of a failed plugin start. Writes are
    atomic, stay owner-only, and are refused when the document changed
    after it was read.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Absolute path of the credential document.

        :returns: The resolved document path.
        :rtype: pathlib.Path
        :raises CredentialsPathError: When the harness home cannot be
            resolved.
        """
        if self._path is not None:
            return self._path
        from .paths import resolve_credentials_path

        return resolve_credentials_path()

    def read_reference(self, name: str) -> str:
        """Return the decoded value of one reference.

        :param name: Reference name.
        :type name: str
        :returns: The secret value.
        :rtype: str
        :raises ReferenceNotFound: When the reference is absent.
        :raises CredentialsError: When the name or the document is
            invalid.
        """
        value = self.peek_reference(name)
        if value is None:
            raise ReferenceNotFound(f'reference "{name}" is not set in {self.path}')
        return value

    def peek_reference(self, name: str) -> str | None:
        """Return the decoded value of one reference, or None when absent.

        :param name: Reference name.
        :type name: str
        :returns: The secret value, or None when the reference is
            absent.
        :rtype: str | None
        :raises CredentialsError: When the name or the document is
            invalid.
        """
        validate_reference_name(name)
        path = self.path
        _, refs = _load_document(path)
        return refs.get(name)

    def set_reference(self, name: str, value: str) -> bool:
        """Write one reference, preserving the rest of the document.

        :param name: Reference name.
        :type name: str
        :param value: New non-empty secret value.
        :type value: str
        :returns: True when the document was written, False when the
            stored value was already identical.
        :rtype: bool
        :raises CredentialsError: When the name, the value or the
            document is invalid, when the edit would change unrelated
            content, or when the document changed after it was read.
        """
        validate_reference_name(name)
        if not isinstance(value, str) or value == "":
            raise CredentialsError("the new value must be a non-empty string")
        path = self.path
        fingerprint = _document_fingerprint(path)
        text, refs = _load_document(path)
        if refs.get(name) == value:
            return False

        rewritten = _rewrite_reference(text, name, value, refs, path)
        if _document_fingerprint(path) != fingerprint:
            raise DocumentChanged(
                "the credentials file changed while the update was being prepared; "
                "run the command again"
            )
        _write_atomic(path, rewritten)
        return True


def _load_document(path: Path) -> tuple[str, dict[str, str]]:
    """Read and parse the refs mapping of one credential document.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :returns: The raw UTF-8 text and the parsed reference mapping.
    :rtype: tuple[str, dict[str, str]]
    :raises CredentialsError: When the file is absent, unreadable or
        invalid.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise CredentialsError(f"credentials file not found: {path}") from None
    except OSError as error:
        raise CredentialsError(
            f"cannot read {path}: {error.strerror or 'I/O error'}"
        ) from None
    lines, _, _ = _document_lines(text)
    _, start, end = _refs_block(lines, path)
    return text, _parse_refs(lines, start, end, path)


def _document_lines(text: str) -> tuple[list[str], str, bool]:
    """Split document text without normalising its line endings.

    :param text: Raw document text.
    :type text: str
    :returns: The lines, the line ending in use and whether the last
        line ends with a line ending.
    :rtype: tuple[list[str], str, bool]
    """
    newline = CR + LF if CR + LF in text else LF
    trailing = text.endswith(newline)
    lines = text.split(newline)
    if trailing:
        lines = lines[:-1]
    return lines, newline, trailing


def _leading_whitespace(line: str) -> str:
    """Return the leading space and tab characters of one line.

    :param line: One line without its line ending.
    :type line: str
    :returns: The leading indentation.
    :rtype: str
    """
    end = 0
    while end < len(line) and line[end] in (" ", TAB):
        end += 1
    return line[:end]


def _strip_comment(text: str) -> str:
    """Remove a trailing YAML comment from one scalar or header.

    :param text: Text after an entry colon or a block scalar indicator.
    :type text: str
    :returns: The text before the comment marker.
    :rtype: str
    """
    index = 0
    while index + 1 < len(text):
        if text[index] in (" ", TAB) and text[index + 1] == HASH:
            return text[:index]
        index += 1
    return text


def _line_error(path: Path, index: int, detail: str) -> CredentialsError:
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


def _refs_block(lines: list[str], path: Path) -> tuple[int, int, int]:
    """Locate the top-level refs mapping in the document lines.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: Index of the header line, first block line and one past
        the block.
    :rtype: tuple[int, int, int]
    :raises CredentialsError: When the header is absent or uses a flow
        mapping.
    """
    header = -1
    for index, line in enumerate(lines):
        match = re.match("^([A-Za-z_][A-Za-z0-9_]*)[ " + TAB + "]*:", line)
        if match is None or match.group(1) != REFS_SECTION:
            continue
        rest = _strip_comment(line[match.end() :]).strip()
        if rest == "":
            header = index
            break
        raise _line_error(
            path, index, "expected a block mapping below a bare refs header"
        )
    if header < 0:
        raise CredentialsError(f"cannot parse {path}: the document has no refs section")

    start = header + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() == "" or _leading_whitespace(line) != "":
            end += 1
            continue
        break
    return header, start, end


def _parse_refs(lines: list[str], start: int, end: int, path: Path) -> dict[str, str]:
    """Parse the refs mapping with a strict YAML scalar subset.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: First line of the refs block.
    :type start: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: Reference names mapped to decoded string values.
    :rtype: dict[str, str]
    :raises CredentialsError: When the block is not a mapping of
        strings.
    """
    entries: dict[str, str] = {}
    indent: str | None = None
    index = start
    while index < end:
        line = lines[index]
        stripped = line.strip()
        if stripped == "" or stripped.startswith(HASH):
            index += 1
            continue
        leading = _leading_whitespace(line)
        if indent is None:
            if leading == "":
                raise _line_error(path, index, "the refs entry is not indented")
            indent = leading
        elif leading != indent:
            raise _line_error(path, index, "unexpected indentation in the refs block")
        match = _KEY_LINE_PATTERN.match(line[len(indent) :])
        if match is None:
            raise _line_error(path, index, "the refs entry is not a plain string key")
        key, raw = match.group(1), match.group(2).lstrip(" " + TAB)
        if key in entries:
            raise _line_error(
                path, index, f'reference "{key}" is declared more than once'
            )
        value, index = _parse_value(lines, index, end, indent, raw, path)
        entries[key] = value
    return entries


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
    :param raw: Text after the entry colon.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the scalar style is outside the
        subset.
    """
    if raw == "":
        if index + 1 < end and _leading_whitespace(lines[index + 1]) != "":
            raise _line_error(
                path, index, "nested values are not supported in the refs block"
            )
        raise _line_error(path, index, "the refs entry has no scalar value")
    first = raw[0]
    if first in ("|", ">"):
        return _parse_block_scalar(lines, index, end, indent, raw, path)
    if first == SINGLE_QUOTE:
        return _parse_single_quoted(raw, index, path)
    if first == DOUBLE_QUOTE:
        return _parse_double_quoted(raw, index, path)
    return _parse_plain(lines, index, end, indent, raw, path)


def _parse_single_quoted(raw: str, index: int, path: Path) -> tuple[str, int]:
    """Decode one single-quoted scalar on a single line.

    :param raw: Scalar text including the opening quote.
    :type raw: str
    :param index: Entry line index used in error messages.
    :type index: int
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the scalar is malformed or multi-
        line.
    """
    characters: list[str] = []
    position = 1
    while position < len(raw):
        character = raw[position]
        if character == SINGLE_QUOTE:
            if position + 1 < len(raw) and raw[position + 1] == SINGLE_QUOTE:
                characters.append(SINGLE_QUOTE)
                position += 2
                continue
            tail = _strip_comment(raw[position + 1 :]).strip()
            if tail != "":
                raise _line_error(
                    path, index, "unexpected text after a single-quoted value"
                )
            return "".join(characters), index + 1
        characters.append(character)
        position += 1
    raise _line_error(
        path,
        index,
        "unterminated single-quoted value; multi-line values are not supported",
    )


def _parse_double_quoted(raw: str, index: int, path: Path) -> tuple[str, int]:
    """Decode one double-quoted scalar on a single line.

    :param raw: Scalar text including the opening quote.
    :type raw: str
    :param index: Entry line index used in error messages.
    :type index: int
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the scalar uses an unsupported escape
        or is malformed.
    """
    characters: list[str] = []
    position = 1
    while position < len(raw):
        character = raw[position]
        if character == DOUBLE_QUOTE:
            tail = _strip_comment(raw[position + 1 :]).strip()
            if tail != "":
                raise _line_error(
                    path, index, "unexpected text after a double-quoted value"
                )
            return "".join(characters), index + 1
        if character != BACKSLASH:
            characters.append(character)
            position += 1
            continue
        position += 1
        if position >= len(raw):
            raise _line_error(
                path, index, "the double-quoted value ends with a dangling escape"
            )
        escape = raw[position]
        if escape in _SIMPLE_ESCAPES:
            characters.append(_SIMPLE_ESCAPES[escape])
            position += 1
            continue
        widths = {"x": 2, "u": 4, "U": 8}
        if escape in widths:
            width = widths[escape]
            digits = raw[position + 1 : position + 1 + width]
            if len(digits) != width or any(
                item not in "0123456789abcdefABCDEF" for item in digits
            ):
                raise _line_error(
                    path, index, "invalid hexadecimal escape in a double-quoted value"
                )
            try:
                characters.append(chr(int(digits, 16)))
            except ValueError:
                raise _line_error(
                    path, index, "invalid code point in a double-quoted value"
                ) from None
            position += 1 + width
            continue
        raise _line_error(path, index, "unsupported escape in a double-quoted value")
    raise _line_error(
        path,
        index,
        "unterminated double-quoted value; multi-line values are not supported",
    )


def _parse_plain(
    lines: list[str], index: int, end: int, indent: str, raw: str, path: Path
) -> tuple[str, int]:
    """Decode one plain scalar on a single line.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Entry line index.
    :type index: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param indent: Indentation of the entry line.
    :type indent: str
    :param raw: Text after the entry colon.
    :type raw: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The decoded value and the next line index.
    :rtype: tuple[str, int]
    :raises CredentialsError: When the value is empty, multi-line or not
        a string.
    """
    value = _strip_comment(raw).rstrip()
    if value == "":
        raise _line_error(path, index, "the refs entry has an empty value")
    if (
        index + 1 < end
        and lines[index + 1].strip() != ""
        and len(_leading_whitespace(lines[index + 1])) > len(indent)
    ):
        raise _line_error(path, index, "multi-line plain values are not supported")
    if _plain_is_not_a_string(value):
        raise _line_error(path, index, "the refs entry is not a string")
    return value, index + 1


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


def _parse_block_scalar(
    lines: list[str], index: int, end: int, indent: str, raw: str, path: Path
) -> tuple[str, int]:
    """Decode one literal or folded block scalar.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param index: Entry line index.
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
    header = _strip_comment(raw[1:]).strip()
    chomp = ""
    content_offset = 0
    for character in header:
        if character in ("+", "-") and chomp == "":
            chomp = character
        elif character.isdigit() and content_offset == 0:
            content_offset = int(character)
        else:
            raise _line_error(path, index, "unsupported block scalar header")
    content: list[str] = []
    cursor = index + 1
    while cursor < end:
        line = lines[cursor]
        if line.strip() == "":
            content.append("")
            cursor += 1
            continue
        if len(_leading_whitespace(line)) <= len(indent):
            break
        content.append(line)
        cursor += 1

    content_indent = len(indent) + content_offset if content_offset else 0
    if not content_indent:
        for line in content:
            if line.strip() != "":
                content_indent = len(_leading_whitespace(line))
                break
    stripped_lines: list[str] = []
    for line in content:
        if line.strip() == "":
            stripped_lines.append("")
            continue
        leading = _leading_whitespace(line)
        if len(leading) < content_indent:
            raise _line_error(path, index, "inconsistent indentation in a block scalar")
        stripped_lines.append(line[content_indent:])

    if indicator == "|":
        value = LF.join(stripped_lines)
    else:
        value = _fold_lines(stripped_lines)
    if chomp == "-":
        value = value.rstrip(LF)
    else:
        value = value.rstrip(LF)
        if content:
            value += LF
    return value, cursor


def _fold_lines(lines: list[str]) -> str:
    """Apply YAML folded-scalar line folding.

    :param lines: Block content lines without their common indentation.
    :type lines: list[str]
    :returns: The folded text.
    :rtype: str
    """
    parts: list[str] = []
    previous_blank = True
    for line in lines:
        if line == "":
            parts.append(LF)
            previous_blank = True
            continue
        if parts and not previous_blank:
            parts.append(" ")
        parts.append(line)
        previous_blank = False
    return "".join(parts)


def _rewrite_reference(
    text: str, name: str, value: str, refs: dict[str, str], path: Path
) -> str:
    """Rewrite or insert one refs entry and verify the surrounding bytes.

    :param text: Raw document text.
    :type text: str
    :param name: Validated reference name.
    :type name: str
    :param value: New non-empty value.
    :type value: str
    :param refs: Reference mapping parsed from the same text.
    :type refs: dict[str, str]
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The rewritten document text.
    :rtype: str
    :raises CredentialsError: When the edit would change unrelated
        content.
    """
    lines, newline, trailing = _document_lines(text)
    _, start, end = _refs_block(lines, path)
    header_lines = list(lines[:start])
    suffix_lines = list(lines[end:])
    indent = _entry_indent(lines, start, end)
    pattern = re.compile("^([ " + TAB + "]+)" + re.escape(name) + "[ " + TAB + "]*:")
    matches = [index for index in range(start, end) if pattern.match(lines[index])]
    if len(matches) > 1:
        raise CredentialsError(
            f'refusing to write: the refs section declares "{name}" more than once'
        )

    scalar = _encode_scalar(value)
    if matches:
        lines[matches[0]] = f"{indent}{name}: {scalar}"
    else:
        insertion = start
        for index in range(start, end):
            if lines[index].strip() != "":
                insertion = index + 1
        lines.insert(insertion, f"{indent}{name}: {scalar}")

    rewritten = newline.join(lines)
    if trailing:
        rewritten += newline

    new_lines, _, _ = _document_lines(rewritten)
    _, new_start, new_end = _refs_block(new_lines, path)
    if new_lines[:new_start] != header_lines:
        raise CredentialsError(
            "refusing to write: the edit would change the document header"
        )
    if new_lines[new_end:] != suffix_lines:
        raise CredentialsError(
            "refusing to write: the edit would change content after the refs block"
        )
    expected = dict(refs)
    expected[name] = value
    if _parse_refs(new_lines, new_start, new_end, path) != expected:
        raise CredentialsError(
            "refusing to write: the edit would change unrelated refs entries"
        )
    return rewritten


def _entry_indent(lines: list[str], start: int, end: int) -> str:
    """Return the indentation used by the existing refs entries.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: First line of the refs block.
    :type start: int
    :param end: One past the last line of the refs block.
    :type end: int
    :returns: The indentation of the first entry, or two spaces when the
        block is empty.
    :rtype: str
    """
    for index in range(start, end):
        match = _KEY_LINE_PATTERN.match(lines[index])
        if match is not None:
            return _leading_whitespace(lines[index])
    return "  "


def _encode_scalar(value: str) -> str:
    """Render one string as a single-line YAML double-quoted scalar.

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
        elif code < 0x20 or code == 0x7F:
            characters.append(BACKSLASH + "x" + format(code, "02x"))
        else:
            characters.append(character)
    characters.append(DOUBLE_QUOTE)
    return "".join(characters)


def _document_fingerprint(path: Path) -> tuple[int, int, int]:
    """Return the filesystem identity of the document.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :returns: Modification time, size and inode of the document.
    :rtype: tuple[int, int, int]
    :raises CredentialsError: When the file cannot be inspected.
    """
    try:
        status = os.stat(path)
    except FileNotFoundError:
        raise CredentialsError(f"credentials file not found: {path}") from None
    except OSError as error:
        raise CredentialsError(
            f"cannot inspect {path}: {error.strerror or 'I/O error'}"
        ) from None
    return (status.st_mtime_ns, status.st_size, status.st_ino)


def _write_atomic(path: Path, text: str) -> None:
    """Replace the document with new text through an atomic rename.

    The temporary file is created beside the document, written with
    owner-only permission, flushed to disk and renamed over the target.
    A failure leaves the previous document untouched.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :param text: Complete new document text.
    :type text: str
    :raises CredentialsError: When the document cannot be written.
    """
    directory = path.parent
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=directory
        )
        os.fchmod(descriptor, _OWNER_ONLY_MODE)
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _sync_directory(directory)
    except OSError as error:
        raise CredentialsError(
            f"cannot write {path}: {error.strerror or 'I/O error'}"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _sync_directory(directory: Path) -> None:
    """Flush one directory entry when the platform supports it.

    :param directory: Directory that received the renamed document.
    :type directory: pathlib.Path
    :returns: Nothing.
    :rtype: None
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
