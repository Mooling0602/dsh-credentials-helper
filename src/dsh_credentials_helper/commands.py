"""The /cred slash command handler.

The handler owns one pending write at a time, which is what gives the
command its confirmation step. Nothing is written until the pending
update is confirmed with /cred ok, /cred confirm or an identical repeat
of the original command.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Protocol

from .credentials import CredentialsError, CredentialsStore, validate_reference_name
from .masking import mask_value

COMMAND_NAME = "cred"
"""Name of the slash command node this plugin registers."""

USAGE = """Usage:
/cred set <KEY> <VALUE> [--skip-confirm]
/cred read <KEY>
/cred ok | /cred confirm"""
"""Short usage text for malformed invocations."""

_MISSING_VALUE = "(not set)"
"""Placeholder shown when the reference has no stored value yet."""


class _Logger(Protocol):
    """Subset of the SDK logger used by the handler."""

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        """Record one error line."""


@dataclass
class _PendingUpdate:
    """One write waiting for its confirmation step."""

    name: str
    value: str
    command: str


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
            return _error(f"{error}\n\n{USAGE}")
        except CredentialsError as error:
            return _error(f"/cred: {error}")
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
        if action in ("ok", "confirm"):
            return self._confirm(action, arguments)
        raise _Usage(f'unknown subcommand "{action}"')

    def _set(self, text: str, arguments: list[str]) -> dict[str, str]:
        skip_confirm = False
        positional: list[str] = []
        for argument in arguments:
            if argument == "--skip-confirm":
                if skip_confirm:
                    raise _Usage("--skip-confirm was given more than once")
                skip_confirm = True
            elif argument.startswith("--"):
                raise _Usage(f"unknown option {argument}")
            else:
                positional.append(argument)
        if len(positional) < 2:
            raise _Usage("/cred set needs a reference name and a value")
        if len(positional) > 2:
            raise _Usage("the value contains whitespace; quote it as one argument")
        name, value = positional
        validate_reference_name(name)
        if value == "":
            raise _Usage("the value must not be empty")
        if skip_confirm:
            return self._apply(name, value)
        if self._pending is not None and self._pending.command == text:
            return self._apply(self._pending.name, self._pending.value)
        current = self._store.peek_reference(name)
        self._pending = _PendingUpdate(name=name, value=value, command=text)
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

    def _confirm(self, action: str, arguments: list[str]) -> dict[str, str]:
        if arguments:
            raise _Usage(f"/cred {action} takes no arguments")
        if self._pending is None:
            raise _Usage("nothing is waiting for confirmation")
        return self._apply(self._pending.name, self._pending.value)

    def _apply(self, name: str, value: str) -> dict[str, str]:
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


class _Usage(Exception):
    """Raised for malformed command input."""


def _success(text: str) -> dict[str, str]:
    return {"kind": "success", "text": text}


def _error(text: str) -> dict[str, str]:
    return {"kind": "error", "text": text}
