"""Entry point of the dsh-credentials-helper plugin.

The plugin directory is the src/ tree of this repository: the manifest,
this entry file and the package sit together, so copying that directory
or packing it produces a valid plugin without an assembly step. The
plugin exposes one human-facing slash command and registers no tool, so
the model cannot read the credential document through it.
"""

from __future__ import annotations

from dsh_mloader_sdk import Context, plugin

from dsh_credentials_helper.commands import COMMAND_NAME, CredentialCommandHandler
from dsh_credentials_helper.credentials import CredentialsStore


@plugin
def apply(ctx: Context) -> None:
    """Register the /cred command node.

    The node asks for two candidate names because the host hands out the
    first one that is still free. A taken name leaves the node
    registered under the fallback instead of silently unmounting the
    plugin.

    :param ctx: Capability context handed to the plugin.
    :type ctx: Context
    :returns: Nothing.
    :rtype: None
    """
    handler = CredentialCommandHandler(CredentialsStore(), ctx.logger)

    @ctx.commands.register(
        names=(COMMAND_NAME, "credentials"),
        node_id=COMMAND_NAME,
        description="Read a credential reference, or update one with a confirmation step.",
        input_hint="set <KEY> <VALUE> [--skip-confirm] | read <KEY> | ok",
    )
    def credential_command(raw_input: str) -> dict[str, str]:
        """Dispatch one /cred invocation.

        :param raw_input: Text after the command name, including the
            separator whitespace.
        :type raw_input: str
        :returns: A command result object with kind and text.
        :rtype: dict[str, str]
        """
        return handler.handle(raw_input)

    ctx.logger.info("dsh-credentials-helper mounted; requested the /cred command node")
