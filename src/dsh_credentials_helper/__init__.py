"""Command-line access to the DeepSeek Harness credential references.

The package supports the plugin's single command node. It never exposes
a tool, and it reads the credential document without printing any value
it was not explicitly asked for.
"""

from .commands import COMMAND_NAME, USAGE, CredentialCommandHandler
from .credentials import (
    CredentialsError,
    CredentialsStore,
    DocumentChanged,
    ReferenceNotFound,
    validate_reference_name,
)
from .paths import resolve_credentials_path, resolve_dsh_home

__all__ = [
    "COMMAND_NAME",
    "USAGE",
    "CredentialCommandHandler",
    "CredentialsError",
    "CredentialsStore",
    "DocumentChanged",
    "ReferenceNotFound",
    "resolve_credentials_path",
    "resolve_dsh_home",
    "validate_reference_name",
]
