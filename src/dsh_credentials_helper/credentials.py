"""The credential document store.

The document at <DSH_HOME>/.credentials.yaml is owned by DeepSeek
Harness. Its refs mapping holds environment-variable-style names mapped
to secret strings, and its records mapping holds provider state. This
module only ever rewrites the one reference it is asked to change; every
other byte of the document, including comments and the formatting of
neighbouring entries, is kept.

DeepSeek Harness writes the document with a full YAML implementation, so
the scalar forms a value may take, and the shapes the refs section may
take, both follow that writer. The scalar subset itself lives in
:mod:`dsh_credentials_helper._yaml`; the writer lock and the atomic
replacement live in :mod:`dsh_credentials_helper._atomic`. This module
locates the refs section, rewrites one entry and verifies that nothing
else changed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ._atomic import document_lock, write_atomic
from ._errors import (
    CredentialsError,
    DocumentChanged,
    DocumentNotFound,
    ReferenceNotFound,
)
from ._yaml import (
    CR,
    KEY_PATTERN,
    LF,
    RefEntry,
    encode_scalar,
    leading_whitespace,
    line_error,
    parse_refs,
    strip_comment,
)
from .paths import resolve_credentials_path

REFERENCE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUOTED_KEY_PATTERN = re.compile(r"^[\"']([^\"']+)[\"'][ \t]*:")
_EMPTY_FLOW_PATTERN = re.compile(r"\{\s*\}")
"""Grammar DeepSeek Harness uses for a credential reference name."""

REFS_SECTION = "refs"
"""Top-level section that holds the reference mapping."""

_DOCUMENT_VERSION = "1"
"""Document layout this plugin writes when it has to create the section."""

_VERSION_ACCEPTED = ("1", "'1'", '"1"')
"""Version spellings that name the layout this plugin understands."""

_ENTRY_INDENT = "  "
"""Indentation used for an entry the store has to create from nothing."""

__all__ = [
    "REFERENCE_PATTERN",
    "REFS_SECTION",
    "CredentialsError",
    "CredentialsStore",
    "DocumentChanged",
    "DocumentNotFound",
    "ReferenceNotFound",
    "validate_reference_name",
]


class CredentialsStore:
    """Reads and updates one credential document.

    The store resolves the document path lazily, so a path problem
    becomes a command error instead of a failed plugin start. Writes
    take the same sibling lock file the harness uses, read the document
    under it, replace only the target entry, verify the rest of the
    document is unchanged, and commit with an atomic rename. A missing
    document is created in the version 1 layout, because the harness
    treats an absent document as an empty store.
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
            content, or when the writer lock cannot be taken.
        """
        validate_reference_name(name)
        if not isinstance(value, str) or value == "":
            raise CredentialsError("the new value must be a non-empty string")
        path = self.path
        with document_lock(path):
            existed = path.exists()
            fingerprint = _document_fingerprint(path) if existed else None
            if existed:
                text, refs = _load_document(path)
            else:
                text, refs = "", {}
            if refs.get(name) == value:
                return False
            expected = dict(refs)
            expected[name] = value
            rewritten, preserved_prefix, preserved_suffix = _rewrite_reference(
                text, path, name, value
            )
            _verify_rewrite(
                rewritten, path, expected, preserved_prefix, preserved_suffix
            )
            if existed:
                if _document_fingerprint(path) != fingerprint:
                    raise DocumentChanged(
                        "the credentials file changed while the update was "
                        "being prepared; run the command again"
                    )
            elif path.exists():
                raise DocumentChanged(
                    "the credentials file appeared while the update was "
                    "being prepared; run the command again"
                )
            write_atomic(path, rewritten)
            return True


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


@dataclass(frozen=True)
class _RefsLayout:
    """Where the refs section sits in one document, and in what shape.

    :param kind: One of ``block``, ``inline_empty`` or ``absent``.
    :type kind: str
    :param header: Index of the refs header line, or -1 when absent.
    :type header: int
    :param start: First line of the refs block.
    :type start: int
    :param end: One past the last line of the refs block.
    :type end: int
    :param version_index: Index of the top-level version line, or -1.
    :type version_index: int
    :param version_value: Text after the version colon, without a
        comment.
    :type version_value: str
    """

    kind: str
    header: int
    start: int
    end: int
    version_index: int
    version_value: str


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


def _read_document(path: Path) -> tuple[str, list[str], str, bool]:
    """Read the document and split it into lines.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :returns: The raw text, its lines, the line ending and whether the
        last line ends with a line ending.
    :rtype: tuple[str, list[str], str, bool]
    :raises DocumentNotFound: When the document does not exist.
    :raises CredentialsError: When the document cannot be read.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        raise DocumentNotFound(f"credentials file not found: {path}") from None
    except OSError as error:
        raise CredentialsError(
            f"cannot read {path}: {error.strerror or 'I/O error'}"
        ) from None
    lines, newline, trailing = _document_lines(text)
    return text, lines, newline, trailing


def _load_document(path: Path) -> tuple[str, dict[str, str]]:
    """Read and parse the refs mapping of one credential document.

    A document whose refs section is missing or empty resolves as a store
    with no references: the harness treats both as an empty store, and a
    write creates the section when it is needed.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :returns: The raw UTF-8 text and the parsed reference mapping.
    :rtype: tuple[str, dict[str, str]]
    :raises CredentialsError: When the file is absent, unreadable or
        invalid.
    """
    text, lines, _, _ = _read_document(path)
    layout = _locate_refs(lines, path)
    if layout.kind != "block":
        return text, {}
    entries = parse_refs(lines, layout.start, layout.end, path)
    return text, {name: entry.value for name, entry in entries.items()}


def _locate_refs(lines: list[str], path: Path) -> _RefsLayout:
    """Locate the top-level refs section in the document lines.

    The section may be a block mapping, an empty inline form the harness
    writes after the last reference is deleted, or absent entirely.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :returns: The section layout.
    :rtype: _RefsLayout
    :raises CredentialsError: When the header uses an unsupported form.
    """
    version_index = -1
    version_value = ""
    for index, line in enumerate(lines):
        if leading_whitespace(line) != "":
            continue
        quoted = _QUOTED_KEY_PATTERN.match(line)
        if quoted is not None:
            if quoted.group(1) == REFS_SECTION:
                raise line_error(
                    path,
                    index,
                    "the refs key must be written without quotes",
                )
            continue
        match = KEY_PATTERN.match(line)
        if match is None:
            continue
        name = match.group(1)
        rest = strip_comment(match.group(2)).strip()
        if name == "version" and version_index < 0:
            version_index = index
            version_value = rest
            continue
        if name != REFS_SECTION:
            continue
        if rest == "":
            start = index + 1
            return _RefsLayout(
                "block",
                index,
                start,
                _refs_block_end(lines, start),
                version_index,
                version_value,
            )
        if rest in ("null", "~") or _EMPTY_FLOW_PATTERN.fullmatch(rest):
            return _RefsLayout(
                "inline_empty",
                index,
                index + 1,
                index + 1,
                version_index,
                version_value,
            )
        raise line_error(
            path, index, "expected a block mapping below a bare refs header"
        )
    insertion = version_index + 1 if version_index >= 0 else len(lines)
    return _RefsLayout("absent", -1, insertion, insertion, version_index, version_value)


def _refs_block_end(lines: list[str], start: int) -> int:
    """Return one past the last line that belongs to the refs block.

    Blank lines, comments and indented lines continue the block, which
    is what keeps a comment between two entries from truncating the
    mapping.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: First line of the refs block.
    :type start: int
    :returns: One past the block's last line.
    :rtype: int
    """
    end = start
    while end < len(lines):
        line = lines[end]
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#") or leading_whitespace(line) != "":
            end += 1
            continue
        break
    return end


def _entry_indent(lines: list[str], start: int, end: int) -> str:
    """Return the indentation used by the existing refs entries.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: First line of the refs block.
    :type start: int
    :param end: One past the last line of the refs block.
    :type end: int
    :returns: The indentation of the first entry, of the first indented
        comment, or two spaces when the block has neither.
    :rtype: str
    """
    comment_indent = ""
    for index in range(start, end):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if stripped == "":
            continue
        if stripped.startswith("#"):
            if not comment_indent:
                comment_indent = line[: len(line) - len(stripped)]
            continue
        if KEY_PATTERN.match(stripped) is not None:
            indent = line[: len(line) - len(stripped)]
            if "\t" in indent:
                return _ENTRY_INDENT
            return indent
    if "\t" in comment_indent:
        return _ENTRY_INDENT
    return comment_indent or _ENTRY_INDENT


def _entry_insertion(
    lines: list[str], layout: _RefsLayout, entries: dict[str, RefEntry]
) -> int:
    """Return where a new entry belongs inside the refs block.

    A block with entries grows after its last entry, so a section
    comment written above the first entry stays attached to it. A block
    without entries grows after its last non-blank line.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param layout: The refs section layout.
    :type layout: _RefsLayout
    :param entries: Parsed entries of the block.
    :type entries: dict[str, RefEntry]
    :returns: The line index to insert at.
    :rtype: int
    """
    if entries:
        return max(entry.next_line for entry in entries.values())
    for index in range(layout.end - 1, layout.start - 1, -1):
        if lines[index].strip() != "":
            return index + 1
    return layout.start


def _create_refs_section(
    lines: list[str], path: Path, layout: _RefsLayout, entry_line: str
) -> tuple[list[str], list[str]]:
    """Add a refs section to a document that has none.

    The version 1 layout is only created when the document already
    declares that version, or when it is empty apart from comments: the
    harness rejects a version-less document that carries content, and
    this plugin never guesses at a layout it does not know.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :param layout: The refs section layout of the original document.
    :type layout: _RefsLayout
    :param entry_line: Fully indented entry line to insert.
    :type entry_line: str
    :returns: The new lines and the preserved prefix and suffix around
        the created section.
    :rtype: tuple[list[str], list[str]]
    :raises CredentialsError: When the document layout does not allow
        creating the section.
    """
    if layout.version_index >= 0:
        if layout.version_value not in _VERSION_ACCEPTED:
            declared = layout.version_value or "(none)"
            raise CredentialsError(
                f"refusing to write: {path} declares version {declared}; "
                "this plugin writes the version 1 layout"
            )
        insertion = layout.version_index + 1
        prefix = list(lines[:insertion])
        suffix = list(lines[insertion:])
        lines[insertion:insertion] = [f"{REFS_SECTION}:", entry_line]
        return prefix, suffix
    meaningful = [
        line
        for line in lines
        if line.strip() != "" and not line.lstrip(" \t").startswith("#")
    ]
    if meaningful:
        raise CredentialsError(
            f"refusing to write: {path} has no refs section and no version "
            "line; this plugin only creates the version 1 layout"
        )
    version_line = f"version: {_DOCUMENT_VERSION}"
    if not any(line.strip() for line in lines):
        lines[:] = [version_line, f"{REFS_SECTION}:", entry_line]
        return [version_line], []
    prefix = [*lines, version_line]
    lines.extend([version_line, f"{REFS_SECTION}:", entry_line])
    return prefix, []


def _after_refs_tail(lines: list[str], start: int) -> list[str]:
    """Return the content after the refs block, ignoring separators.

    Blank lines and comments between the refs block and the next section
    carry no content, so a write that folds them into the block changes
    nothing a parser sees.

    :param lines: Document lines without their line endings.
    :type lines: list[str]
    :param start: One past the last line of the refs block.
    :type start: int
    :returns: The lines from the first content line onwards.
    :rtype: list[str]
    """
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or stripped.startswith("#"):
            index += 1
            continue
        break
    return lines[index:]


def _rewrite_reference(
    text: str, path: Path, name: str, value: str
) -> tuple[str, list[str], list[str]]:
    """Rewrite or insert one refs entry and report the preserved bytes.

    :param text: Raw document text.
    :type text: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :param name: Validated reference name.
    :type name: str
    :param value: New non-empty value.
    :type value: str
    :returns: The rewritten text and the lines preserved before and
        after the refs section.
    :rtype: tuple[str, list[str], list[str]]
    :raises CredentialsError: When the document layout cannot be
        updated.
    """
    lines, newline, trailing = _document_lines(text)
    layout = _locate_refs(lines, path)
    scalar = encode_scalar(value)
    created_entry = f"{_ENTRY_INDENT}{name}: {scalar}"
    if layout.kind == "block":
        entries = parse_refs(lines, layout.start, layout.end, path)
        preserved_prefix = list(lines[: layout.header])
        preserved_suffix = list(lines[layout.end :])
        indent = _entry_indent(lines, layout.start, layout.end)
        entry_line = f"{indent}{name}: {scalar}"
        if name in entries:
            entry = entries[name]
            lines[entry.first_line : entry.next_line] = [entry_line]
        else:
            lines.insert(
                _entry_insertion(lines, layout, entries),
                entry_line,
            )
    elif layout.kind == "inline_empty":
        preserved_prefix = list(lines[: layout.header])
        preserved_suffix = list(lines[layout.end :])
        header_line = lines[layout.header]
        comment = header_line[len(strip_comment(header_line)) :]
        lines[layout.header] = f"{REFS_SECTION}:" + comment
        lines.insert(layout.header + 1, created_entry)
    else:
        preserved_prefix, preserved_suffix = _create_refs_section(
            lines, path, layout, created_entry
        )
    rewritten = newline.join(lines)
    if trailing or text == "":
        rewritten += newline
    return rewritten, preserved_prefix, preserved_suffix


def _verify_rewrite(
    rewritten: str,
    path: Path,
    expected: dict[str, str],
    preserved_prefix: list[str],
    preserved_suffix: list[str],
) -> None:
    """Verify that a rewrite changed only the entry it was asked to change.

    :param rewritten: The complete next document text.
    :type rewritten: str
    :param path: Document path used in error messages.
    :type path: pathlib.Path
    :param expected: The refs mapping the rewrite must produce.
    :type expected: dict[str, str]
    :param preserved_prefix: Lines that must still precede the refs
        section.
    :type preserved_prefix: list[str]
    :param preserved_suffix: Lines that must still follow the refs
        section.
    :type preserved_suffix: list[str]
    :raises CredentialsError: When the edit would change unrelated
        content.
    """
    lines, _, _ = _document_lines(rewritten)
    layout = _locate_refs(lines, path)
    if layout.kind != "block":
        raise CredentialsError(
            "refusing to write: the edit would not leave a refs block"
        )
    if preserved_prefix and lines[: layout.header] != preserved_prefix:
        raise CredentialsError(
            "refusing to write: the edit would change the document header"
        )
    if preserved_suffix and _after_refs_tail(lines, layout.end) != _after_refs_tail(
        preserved_suffix, 0
    ):
        raise CredentialsError(
            "refusing to write: the edit would change content after the refs block"
        )
    entries = parse_refs(lines, layout.start, layout.end, path)
    actual = {name: entry.value for name, entry in entries.items()}
    if actual != expected:
        raise CredentialsError(
            "refusing to write: the edit would change unrelated refs entries"
        )


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
        raise DocumentNotFound(f"credentials file not found: {path}") from None
    except OSError as error:
        raise CredentialsError(
            f"cannot inspect {path}: {error.strerror or 'I/O error'}"
        ) from None
    return (status.st_mtime_ns, status.st_size, status.st_ino)
