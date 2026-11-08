"""Atomic replacement and cross-process writer coordination.

DeepSeek Harness serializes every read-modify-write cycle on the
credential document through a sibling lock file named
``<document>.lock``: a writer creates it with ``wx`` semantics, records
its process id, and removes it when the cycle ends. A lock whose
recorded holder exited is taken over through a claim file so two
contenders cannot both remove it. This module implements the same
protocol, so the plugin and the harness never replace each other's
snapshot, and completes the write by renaming a fresh sibling over the
document, so a reader sees either the old or the new document and a
failure leaves the previous one untouched.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from ._errors import CredentialsError

OWNER_ONLY_MODE = 0o600
"""Permission bits of the lock file and of every document this module
writes."""

LOCK_WAIT_MS = 10_000
"""How long one command waits for the writer lock.

The harness may hold the lock for a slow record mutation, but a slash
command must answer before the plugin's own call timeout (15s in the
manifest), so the wait stays below that bound and reports a clear error
when it expires.
"""

_LOCK_RETRY_INITIAL_S = 0.02
"""First backoff step while contending for the writer lock."""

_LOCK_RETRY_MAX_S = 0.2
"""Longest backoff step while contending for the writer lock."""

_TAKEOVER_HASH_LENGTH = 16
"""Bytes of the record hash used in a lock takeover claim name."""

_WINDOWS_RENAME_RETRIES = 8
"""Attempts allowed for a transient Windows replacement failure."""

_WINDOWS_RENAME_INITIAL_S = 0.02
"""First retry delay for a transient Windows replacement failure."""

_WINDOWS_RENAME_MAX_S = 0.2
"""Longest retry delay for a transient Windows replacement failure."""

_WINDOWS_TRANSIENT_RENAME_ERRORS = frozenset({errno.EACCES, errno.EBUSY, errno.EPERM})
"""Replacement failures Windows reports while another handle closes."""


def _lock_path(path: Path) -> Path:
    """Return the lock file that serializes writers of one document.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :returns: The sibling lock path.
    :rtype: pathlib.Path
    """
    return path.with_name(path.name + ".lock")


def _read_lock_record(lock: Path) -> str | None:
    """Return the lock file content, or None when it cannot be read.

    :param lock: Lock file path.
    :type lock: pathlib.Path
    :returns: The recorded holder text, or None.
    :rtype: str | None
    """
    try:
        with lock.open("r", encoding="ascii", errors="replace", newline="") as handle:
            return handle.read()
    except OSError:
        return None


def _write_pid_record(descriptor: int) -> None:
    """Write this process's pid into an opened lock or claim file.

    :param descriptor: Open file descriptor.
    :type descriptor: int
    :returns: Nothing.
    :rtype: None
    :raises OSError: When the descriptor cannot be written.
    """
    os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))


def _create_exclusive(path: Path) -> int:
    """Create one file exclusively and return its descriptor.

    :param path: File to create.
    :type path: pathlib.Path
    :returns: The open descriptor.
    :rtype: int
    :raises FileExistsError: When the file already exists.
    :raises OSError: When the file cannot be created.
    """
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_ONLY_MODE)


def _unlink_quietly(path: Path) -> None:
    """Remove one path, reporting nothing when it is already gone.

    :param path: File to remove.
    :type path: pathlib.Path
    :returns: Nothing.
    :rtype: None
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        return


def _acquire_lock(lock: Path) -> bool:
    """Try once to create the lock file for this process.

    :param lock: Lock file path.
    :type lock: pathlib.Path
    :returns: True when the lock was created here, False on contention.
    :rtype: bool
    :raises CredentialsError: When the lock cannot be created for
        another reason, such as a missing parent directory.
    """
    try:
        descriptor = _create_exclusive(lock)
    except FileExistsError:
        return False
    except FileNotFoundError:
        raise CredentialsError(
            f"cannot create the writer lock at {lock}; the directory does not exist"
        ) from None
    except OSError as error:
        raise CredentialsError(
            f"cannot create the writer lock at {lock}: {error.strerror or 'I/O error'}"
        ) from None
    try:
        _write_pid_record(descriptor)
    finally:
        os.close(descriptor)
    return True


def _process_is_gone(pid: int) -> bool:
    """Report whether no live process owns one pid.

    A pid this process cannot probe is treated as alive: waiting for an
    unknown holder is safe, while taking over its lock is not.

    :param pid: Recorded holder process id.
    :type pid: int
    :returns: True only when the process is proven absent.
    :rtype: bool
    """
    if pid <= 0 or pid > 0x7FFFFFFF or pid == os.getpid():
        return False
    if sys.platform == "win32":
        return _windows_process_is_gone(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _windows_process_is_gone(pid: int) -> bool:
    """Report whether a Windows process id names no live process.

    ``os.kill`` cannot be probed with signal 0 on Windows, because every
    value other than the console events terminates the target, so the
    probe opens the process instead. Access denied means the process
    exists under another user.

    :param pid: Recorded holder process id.
    :type pid: int
    :returns: True only when the process is proven absent.
    :rtype: bool
    """
    import ctypes

    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    query_limited_information = 0x1000
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return False
    return bool(getattr(ctypes, "get_last_error")() != 5)  # noqa: B009


def _record_names_exited_holder(record: str | None) -> bool:
    """Report whether one lock record proves its holder exited.

    :param record: Lock file content, or None when unavailable.
    :type record: str | None
    :returns: True when the record is a pid that no longer exists.
    :rtype: bool
    """
    if record is None or not record.endswith("\n"):
        return False
    body = record[:-1]
    if not body or not body.isdigit():
        return False
    return _process_is_gone(int(body))


def _take_over_exited_lock(lock: Path) -> bool:
    """Remove the lock when the process recorded in it exited.

    A claim file named after the record hash serializes contenders, so a
    removal never deletes a lock another contender acquired after the
    dead holder's. The record and the probe are re-read under the claim.

    :param lock: Lock file path.
    :type lock: pathlib.Path
    :returns: True when this call removed the lock.
    :rtype: bool
    """
    record = _read_lock_record(lock)
    if not _record_names_exited_holder(record):
        return False
    if record is None:
        return False
    digest = hashlib.sha256(record.encode("ascii")).hexdigest()[:_TAKEOVER_HASH_LENGTH]
    claim = lock.with_name(lock.name + f".takeover-{digest}")
    try:
        descriptor = _create_exclusive(claim)
    except OSError:
        return False
    try:
        _write_pid_record(descriptor)
        os.close(descriptor)
        descriptor = -1
        if _read_lock_record(lock) != record:
            return False
        if not _record_names_exited_holder(record):
            return False
        try:
            os.unlink(lock)
        except OSError:
            return False
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _unlink_quietly(claim)


def _release_lock(lock: Path) -> None:
    """Remove this process's writer lock.

    The release is best effort: a lock left behind names a live process and
    only blocks other writers until this process exits. Retries absorb the
    transient Windows removal failures that a scanner or virus checker
    causes.

    :param lock: Lock file path.
    :type lock: pathlib.Path
    :returns: Nothing.
    :rtype: None
    """
    for attempt in range(3):
        try:
            os.unlink(lock)
            return
        except FileNotFoundError:
            return
        except OSError:
            if sys.platform != "win32" or attempt == 2:
                return
            time.sleep(_LOCK_RETRY_INITIAL_S)


@contextmanager
def document_lock(path: Path, wait_ms: int | None = None) -> Generator[None]:
    """Hold the writer lock of one credential document.

    The lock file is a ``wx``-created sibling that records this
    process's pid, matching the protocol the harness uses, so both
    writers serialize the same way. A contender waits with exponential
    backoff until the deadline and then raises :class:`CredentialsError`
    instead of writing over a state it could not read under the lock.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :param wait_ms: Longest wait before giving up; None uses
        :data:`LOCK_WAIT_MS`.
    :type wait_ms: int | None
    :returns: A context manager that releases the lock on exit.
    :rtype: collections.abc.Iterator[None]
    :raises CredentialsError: When the lock cannot be acquired.
    """
    lock = _lock_path(path)
    effective_wait = LOCK_WAIT_MS if wait_ms is None else wait_ms
    deadline = time.monotonic() + effective_wait / 1000
    delay = _LOCK_RETRY_INITIAL_S
    while not _acquire_lock(lock):
        if _take_over_exited_lock(lock):
            continue
        if time.monotonic() >= deadline:
            raise CredentialsError(
                f"timed out waiting for the writer lock at {lock}; "
                "another writer still holds it, so the update was not applied"
            )
        time.sleep(delay)
        delay = min(delay * 2, _LOCK_RETRY_MAX_S)
    try:
        yield
    finally:
        _release_lock(lock)


def _replace_with_retries(temporary: str, path: Path) -> None:
    """Replace the document with the temporary file, retrying on Windows.

    :param temporary: Temporary sibling that holds the new content.
    :type temporary: str
    :param path: Document path receiving the content.
    :type path: pathlib.Path
    :returns: Nothing.
    :rtype: None
    :raises OSError: When the replacement keeps failing.
    """
    delay = _WINDOWS_RENAME_INITIAL_S
    retries = 0
    while True:
        try:
            os.replace(temporary, path)
            return
        except OSError as error:
            if (
                sys.platform != "win32"
                or error.errno not in _WINDOWS_TRANSIENT_RENAME_ERRORS
                or retries >= _WINDOWS_RENAME_RETRIES
            ):
                raise
            retries += 1
            time.sleep(delay)
            delay = min(delay * 2, _WINDOWS_RENAME_MAX_S)


def _set_owner_only(descriptor: int) -> None:
    """Tighten a freshly created temporary file to owner-only access.

    Windows has no file-descriptor chmod, and the temporary file created
    beside the document inherits that directory's protection, so the
    step is skipped there instead of faked.

    :param descriptor: Open descriptor of the temporary file.
    :type descriptor: int
    :returns: Nothing.
    :rtype: None
    :raises OSError: When the chmod fails on a platform that has it.
    """
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, OWNER_ONLY_MODE)


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


def write_atomic(path: Path, text: str) -> None:
    """Replace the document with new text through an atomic rename.

    The temporary file is created beside the document, written with
    owner-only permission, flushed to disk and renamed over the target.
    A failure leaves the previous document untouched, and the directory
    entry is flushed where the platform allows it.

    :param path: Absolute document path.
    :type path: pathlib.Path
    :param text: Complete new document text.
    :type text: str
    :returns: Nothing.
    :rtype: None
    :raises CredentialsError: When the document cannot be written.
    """
    directory = path.parent
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=directory
        )
        _set_owner_only(descriptor)
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retries(temporary, path)
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
            _unlink_quietly(Path(temporary))
