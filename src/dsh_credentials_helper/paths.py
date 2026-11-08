"""Location of the credential document.

DeepSeek Harness keeps the document at <DSH_HOME>/.credentials.yaml,
where DSH_HOME defaults to ~/.dsh. A plugin child process does not
inherit DSH_HOME (the loader drops every DSH_* variable), so the harness
home is derived from the plugin's own install path when the variable is
unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

DSH_HOME_ENV = "DSH_HOME"
"""Environment variable that overrides the default harness home."""

DEFAULT_HOME_DIR_NAME = ".dsh"
"""Directory name of the default harness home under the user's home."""

CREDENTIALS_FILENAME = ".credentials.yaml"
"""Basename of the credential document inside the harness home."""


class CredentialsPathError(RuntimeError):
    """Raised when the harness home cannot be resolved safely."""


def resolve_dsh_home() -> Path:
    """Return the harness home directory.

    An explicit DSH_HOME value wins, but it must be absolute because the
    plugin process runs with the plugin directory as its working
    directory and cannot reproduce the harness process's own resolution.
    When the variable is unavailable, an installed plugin derives the
    home from its directory layout
    <home>/profiles/<profile>/mloader/plugins/<id>. Everything else
    falls back to ~/.dsh.

    :returns: The harness home directory.
    :rtype: pathlib.Path
    :raises CredentialsPathError: When an explicit value is not
        absolute.
    """
    configured = os.environ.get(DSH_HOME_ENV)
    if configured:
        home = Path(configured).expanduser()
        if not home.is_absolute():
            raise CredentialsPathError(
                f"{DSH_HOME_ENV} must be an absolute path when the plugin reads it"
            )
        return home
    derived = _home_from_install_location()
    if derived is not None:
        return derived
    return Path.home() / DEFAULT_HOME_DIR_NAME


def resolve_credentials_path() -> Path:
    """Return the absolute path of the credential document.

    :returns: <harness home>/.credentials.yaml resolved to an absolute
        path.
    :rtype: pathlib.Path
    :raises CredentialsPathError: When the harness home cannot be
        resolved.
    """
    return (resolve_dsh_home() / CREDENTIALS_FILENAME).resolve()


def _home_from_install_location() -> Path | None:
    """Derive the harness home from the plugin install layout, when it matches.

    :returns: The harness home, or None when the plugin does not run
        from a profile plugin directory.
    :rtype: pathlib.Path | None
    """
    plugin_directory = Path(__file__).resolve().parent.parent
    if plugin_directory.parent.name != "plugins":
        return None
    if plugin_directory.parent.parent.name != "mloader":
        return None
    profile_directory = plugin_directory.parent.parent.parent
    profiles_directory = profile_directory.parent
    if profiles_directory.name != "profiles":
        return None
    return profiles_directory.parent
