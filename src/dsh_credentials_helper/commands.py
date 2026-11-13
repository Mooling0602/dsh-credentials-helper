"""The /cred slash command handler.

The handler owns one pending write at a time, which is what gives the
command its confirmation step. Nothing is written until the pending
update is confirmed with /cred ok, /cred confirm or an identical repeat
of the original command.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any, Protocol

from .credentials import (
    CredentialsError,
    CredentialsStore,
    DocumentNotFound,
    validate_reference_name,
)
from .masking import mask_value

COMMAND_NAME = "cred"
"""Name of the slash command node this plugin registers."""

USAGE = """Usage:
/cred set <KEY> <VALUE> [--skip-confirm]
/cred set <KEY> -- <VALUE>
/cred read <KEY>
/cred check <KEY>
/cred remove <KEY | PATTERN>
/cred ok | /cred confirm
/cred help"""
"""Short usage text for malformed invocations."""

_MISSING_VALUE = "(not set)"
"""Placeholder shown when the reference has no stored value yet."""

_NULL_VALUE = "<null>"
"""Mask shown by /cred check when the stored value is unusable."""


class _Logger(Protocol):
    """Subset of the SDK logger used by the handler."""

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Record one error line."""


@dataclass
class _PendingUpdate:
    """One write waiting for its confirmation step.

    A pending update is either a set or a delete, never both: the
    handler holds one at a time so a stale confirmation cannot apply an
    operation the operator has since replaced.

    :param action: The subcommand that stages it, ``set`` or ``remove``.
    :type action: str
    :param name: Reference name.
    :type name: str
    :param value: New value for a set; unused for a remove.
    :type value: str
    :param command: Normalised command text that stages it, so an
        identical repeat can confirm it.
    :type command: str
    """

    action: str
    name: str
    value: str
    command: str
    names: tuple[str, ...] = ()


class CredentialCommandHandler:
    """Dispatch /cred invocations against one credential document."""

    def __init__(self, store: CredentialsStore, logger: _Logger | None = None) -> None:
        self._store = store
        self._logger = logger
        self._pending: _PendingUpdate | None = None

    def handle(self, raw_input: str) -> dict[str, str]:
        """Dispatch the raw text after the command name.

        :param raw_input: Text after the registered command name,
            including the separator whitespace.
        :type raw_input: str
        :returns: A command result object with kind and text.
        :rtype: dict[str, str]
        """
        text = raw_input.strip()
        try:
            return self._dispatch(text)
        except _Usage as error:
            return _error(_wrap(f"{error}\n\n{USAGE}"))
        except CredentialsError as error:
            return _error(_wrap(f"/cred: {error}"))
        except Exception as error:  # noqa: BLE001 - unexpected errors stay opaque
            if self._logger is not None:
                detail = (
                    f"/cred failed with an unexpected error ({type(error).__name__})"
                )
                self._logger.error(detail)
            return _error("/cred: unexpected failure; see the dsh log")

    def _dispatch(self, text: str) -> dict[str, str]:
        if text == "":
            raise _Usage("missing subcommand")
        try:
            tokens = shlex.split(text)
        except ValueError:
            raise _Usage("the input has an unterminated quote") from None
        if not tokens:
            raise _Usage("missing subcommand")
        action = tokens[0]
        arguments = tokens[1:]
        if action in ("help", "--help", "-h"):
            if arguments:
                raise _Usage(f"/cred {action} takes no arguments")
            return _success(USAGE)
        if action == "set":
            return self._set(text, arguments)
        if action == "read":
            return self._read(arguments)
        if action == "check":
            return self._check(arguments)
        if action == "remove":
            return self._remove(text, arguments)
        if action in ("ok", "confirm"):
            return self._confirm(action, arguments)
        raise _Usage(f'unknown subcommand "{action}"')

    def _set(self, text: str, arguments: list[str]) -> dict[str, str]:
        skip_confirm = False
        positional: list[str] = []
        end_of_options = False
        for argument in arguments:
            if not end_of_options:
                if argument == "--":
                    end_of_options = True
                    continue
                if argument == "--skip-confirm":
                    if skip_confirm:
                        raise _Usage("--skip-confirm was given more than once")
                    skip_confirm = True
                    continue
                if argument.startswith("--"):
                    raise _Usage(f"unknown option {argument}")
            positional.append(argument)
        if len(positional) < 2:
            raise _Usage("/cred set needs a reference name and a value")
        if len(positional) > 2:
            raise _Usage(
                "the value contains whitespace; quote it as one argument, and "
                "put -- before a value that starts with --"
            )
        name, value = positional
        validate_reference_name(name)
        if value == "":
            raise _Usage("the value must not be empty")
        if skip_confirm:
            return self._apply_set(name, value)
        pending = self._pending
        if pending is not None and pending.command == text:
            if pending.action == "set":
                return self._apply_set(pending.name, pending.value)
            return self._apply_remove(pending.names)
        try:
            current = self._store.peek_reference(name)
        except DocumentNotFound:
            current = None
        self._pending = _PendingUpdate(
            action="set", name=name, value=value, command=text
        )
        current_text = _MISSING_VALUE if current is None else mask_value(current)
        return _success(
            "\n".join(
                [
                    f"confirmation required: set {name}",
                    f"current: {current_text}",
                    f"new: {mask_value(value)}",
                    "/cred ok or /cred confirm writes it; repeating the same command also writes it.",
                ]
            )
        )

    def _read(self, arguments: list[str]) -> dict[str, str]:
        if len(arguments) != 1:
            raise _Usage("/cred read needs exactly one reference name")
        value = self._store.read_reference(arguments[0])
        return _success(f"{value}\n")

    def _check(self, arguments: list[str]) -> dict[str, str]:
        if len(arguments) != 1:
            raise _Usage("/cred check needs exactly one reference name")
        name = arguments[0]
        scan = self._store.check_reference(name)
        path = self._store.path
        if not scan.exists:
            return _success(
                "\n".join(
                    [
                        f"{name} is not existing in refs.",
                        f"file: {path}",
                    ]
                )
            )
        if scan.value is None:
            return _success(
                "\n".join(
                    [
                        f"{name} is existing in refs but with invalid value.",
                        f"file: {path}",
                        f"current: {_NULL_VALUE}",
                    ]
                )
            )
        return _success(
            "\n".join(
                [
                    f"Key {name} has valid value.",
                    f"file: {path}",
                    f"current: {mask_value(scan.value)}",
                ]
            )
        )

    def _confirm(self, action: str, arguments: list[str]) -> dict[str, str]:
        if arguments:
            raise _Usage(f"/cred {action} takes no arguments")
        pending = self._pending
        if pending is None:
            raise _Usage("nothing is waiting for confirmation")
        if pending.action == "remove":
            return self._apply_remove(pending.names)
        return self._apply_set(pending.name, pending.value)

    def _remove(self, text: str, arguments: list[str]) -> dict[str, str]:
        """Stage a delete, or write it when the same removal is repeated.

        Deletion is destructive and unrecoverable from this command, so
        it always takes the confirmation step: every reference about to
        go is listed with its masked current value, and nothing is
        written until ``/cred ok``, ``/cred confirm`` or an identical
        repeat.

        The target may use ``*`` and ``?``, which match many references.
        An empty pattern such as ``*`` still takes the confirmation step
        and still lists every name, because it is the widest delete the
        command can express.

        :param text: The normalised command text, used to recognise an
            identical repeat.
        :type text: str
        :param arguments: Arguments after the subcommand.
        :type arguments: list[str]
        :returns: A command result object with kind and text.
        :rtype: dict[str, str]
        """
        options = [argument for argument in arguments if argument.startswith("--")]
        if options:
            if "--skip-confirm" in options:
                raise _Usage(
                    "/cred remove cannot skip confirmation; deleting a "
                    "credential is not recoverable, so confirm it with "
                    "/cred ok"
                )
            raise _Usage(f"unknown option {options[0]}")
        if len(arguments) != 1:
            raise _Usage("/cred remove needs exactly one reference name or pattern")
        pattern = arguments[0]
        if not _is_pattern(pattern):
            validate_reference_name(pattern)
        regex = _compile_pattern(pattern)
        pending = self._pending
        if pending is not None and pending.command == text:
            if pending.action == "remove":
                return self._apply_remove(pending.names)
            return self._apply_set(pending.name, pending.value)
        try:
            known = self._store.reference_names()
        except DocumentNotFound:
            known = []
        matched = [name for name in known if regex.fullmatch(name)]
        if not matched:
            self._pending = None
            if _is_pattern(pattern):
                return _success(
                    "\n".join(
                        [
                            f"nothing to remove: no reference matches {pattern}",
                            f"file: {self._store.path}",
                        ]
                    )
                )
            return _success(
                "\n".join(
                    [
                        f"nothing to remove: {pattern} is not set",
                        f"file: {self._store.path}",
                    ]
                )
            )
        current = {name: self._store.peek_reference(name) for name in matched}
        self._pending = _PendingUpdate(
            action="remove", name=pattern, value="", command=text, names=tuple(matched)
        )
        lines = [
            f"confirmation required: remove {pattern}",
            f"file: {self._store.path}",
        ]
        if _is_pattern(pattern):
            lines.append(f"matches {len(matched)} reference(s):")
        for name in matched:
            stored = current[name]
            shown = _NULL_VALUE if stored is None else mask_value(stored)
            lines.append(f"  {name}: {shown}")
        lines.append("Deleting a credential cannot be undone from this command.")
        lines.append(
            "/cred ok or /cred confirm removes them; repeating the same "
            "command also removes them."
        )
        return _success("\n".join(lines))

    def _apply_set(self, name: str, value: str) -> dict[str, str]:
        changed = self._store.set_reference(name, value)
        self._pending = None
        if changed:
            return _success(
                "\n".join(
                    [
                        f"updated: {name}",
                        f"value: {mask_value(value)}",
                        f"file: {self._store.path}",
                        "The credentials provider picks the change up without a restart.",
                    ]
                )
            )
        return _success(
            "\n".join(
                [
                    f"unchanged: {name}",
                    f"value: {mask_value(value)} is already stored; no file write was needed.",
                ]
            )
        )

    def _apply_remove(self, names: tuple[str, ...]) -> dict[str, str]:
        removed = self._store.delete_references(list(names))
        self._pending = None
        if not removed:
            return _success(
                "\n".join(
                    [
                        f"unchanged: {names[0]} was already absent; no file write was needed.",
                    ]
                )
            )
        lines = [
            f"removed {len(removed)} reference(s):",
            *[f"  {name}" for name in removed],
            f"file: {self._store.path}",
            "The credentials provider picks the change up without a restart.",
        ]
        return _success("\n".join(lines))


_PATTERN_CHARACTERS = "*?"
"""Wildcard characters a remove target may use."""

_ERROR_BREAK = "; "
"""Separator at which one long error is split into structured lines."""


def _is_pattern(pattern: str) -> bool:
    """Report whether one remove target uses wildcards.

    :param pattern: The target the operator typed.
    :type pattern: str
    :returns: True when the target contains a wildcard.
    :rtype: bool
    """
    return any(character in pattern for character in _PATTERN_CHARACTERS)


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one remove target into a whole-name matcher.

    ``*`` matches any run of characters and ``?`` matches exactly one.
    Everything else is literal, so a name containing ``.`` or ``+``
    needs no escaping at the prompt. A target without wildcards still
    has to match a whole name, which is what keeps it from deleting a
    reference that merely contains it.

    :param pattern: The target the operator typed.
    :type pattern: str
    :returns: A regular expression that matches whole names.
    :rtype: re.Pattern[str]
    """
    escaped = re.escape(pattern)
    return re.compile(escaped.replace(r"\*", ".*").replace(r"\?", "."))


class _Usage(Exception):
    """Raised for malformed command input."""


def _wrap(text: str) -> str:
    """Split one long error at a structural boundary.

    The chat client renders a command outcome as a one-line summary that
    elides, and it offers the disclosure body only when the text
    contains a newline, so a single long line is cut off with no way to
    read it. A message with two clauses is therefore split between them
    rather than folded at a column: the client already wraps a line that
    is merely too wide, and folding here would break it at an arbitrary
    point and fight the client's own wrapping.

    :param text: The error text.
    :type text: str
    :returns: The text, split into two lines when it has two clauses.
    :rtype: str
    """
    if "\n" in text:
        return text
    head, separator, tail = text.partition(_ERROR_BREAK)
    if not separator:
        return text
    return f"{head}\n{tail}"


def _success(text: str) -> dict[str, str]:
    return {"kind": "success", "text": text}


def _error(text: str) -> dict[str, str]:
    return {"kind": "error", "text": text}
