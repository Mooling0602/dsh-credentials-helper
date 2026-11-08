"""Errors shared by the credential helper modules.

The classes live in their own module so the document parser and the file
store can raise the same types without importing each other. They are
re-exported from :mod:`dsh_credentials_helper.credentials`, which is the
name the rest of the plugin uses.
"""

from __future__ import annotations


class CredentialsError(RuntimeError):
    """Raised when the document cannot be read or updated safely."""


class DocumentNotFound(CredentialsError):
    """Raised when the credential document does not exist yet."""


class ReferenceNotFound(CredentialsError):
    """Raised when the requested reference is absent from the document."""


class DocumentChanged(CredentialsError):
    """Raised when the document changed between the read and the write."""
