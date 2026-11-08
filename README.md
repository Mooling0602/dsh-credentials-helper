# dsh-credentials-helper

English | [简体中文](README.zh.md)

A [dsh-mloader](https://github.com/Mooling0602/dsh-mloader) Python plugin that exposes the DeepSeek Harness credential references through one human-facing slash command. It registers no tool, so the model cannot read or write the credential document through this plugin.

The plugin works on `<DSH_HOME>/.credentials.yaml`, the file the Harness credentials provider owns. It reads the `refs` mapping and can replace one value or add one entry. Writes are staged behind a confirmation step, the file stays owner-only, and the document is replaced atomically.

[![dsh-mloader-badge](https://img.shields.io/badge/powered_by-dsh--mloader-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/Mooling0602/dsh-mloader)
[![license-mit-badge](https://img.shields.io/github/license/Mooling0602/dsh-credentials-helper?style=flat-square)](https://github.com/Mooling0602/dsh-credentials-helper/blob/main/LICENSE)

## Requirements

- dsh with dsh-mloader installed.
- The Python backend, which creates the shared profile environment.
- Python 3.10 or later for the plugin code.

## Install

The plugin directory is the `src/` tree of this repository. Copy it into the profile plugin root while dsh runs, naming the copy after the plugin id:

```sh
mkdir -p <DSH_HOME>/profiles/<profile>/mloader/plugins
cp -r src <DSH_HOME>/profiles/<profile>/mloader/plugins/dsh-credentials-helper
```

The loader watches the plugin root and mounts the directory. When watching is disabled, load it with `/mloader load dsh-credentials-helper`. To install it as a package instead, build an archive from the same directory and hand it to the runtime command line or the loader install command:

```sh
python3 <dsh-mloader>/src/runtime/python_sdk/run_cli.py pack src -o dist/dsh-credentials-helper.mloader.pack
/mloader install dist/dsh-credentials-helper.mloader.pack
```

## Commands

| Command | Effect |
| --- | --- |
| `/cred set <KEY> <VALUE>` | Stage an update to the reference `KEY` and ask for confirmation. |
| `/cred set <KEY> <VALUE> --skip-confirm` | Write the update immediately. |
| `/cred read <KEY>` | Print the decoded value of `KEY`. |
| `/cred ok` or `/cred confirm` | Write the update that is waiting for confirmation. |
| `/cred help` | Show the usage text. |

`KEY` is a credential reference name, the same environment-variable-style name the Harness uses inside `refs` (`[A-Za-z_][A-Za-z0-9_]*`). `VALUE` is one argument, so quote a value that contains spaces. A new `KEY` is added to the `refs` mapping; an existing key keeps its position.

## Confirmation

The first `/cred set` stores the pending update in the plugin process and shows the reference name with masked current and new values. Nothing is written. The update is written when one of these happens:

- the operator runs `/cred ok` or `/cred confirm`;
- the operator runs the identical `/cred set` command again;
- the original command carried `--skip-confirm`.

A different `/cred set` replaces the pending update. The pending value lives only in the plugin process memory and is discarded when the plugin unloads.

## Masking and output

Every message except `/cred read` shows a masked value: nothing for eight characters or fewer, otherwise a short prefix and suffix. `/cred read` prints the decoded value because that is its purpose. Error messages never include a decoded value, and a parse error reports only the document path and a line number, never the offending text.

## Where the document lives

The Harness keeps the document at `<DSH_HOME>/.credentials.yaml`, where `DSH_HOME` defaults to `~/.dsh`. The loader starts a plugin child process without any `DSH_*` variable, so the plugin resolves the home in this order:

1. an explicit `DSH_HOME` value in the plugin environment, which must be absolute;
2. the install layout, when the plugin runs from `<home>/profiles/<profile>/mloader/plugins/dsh-credentials-helper`;
3. `~/.dsh`.

The first and last cases cover the default installation. The second case is what keeps a custom `DSH_HOME` working, because the plugin can derive the same home from its own location.

## Write safety

The store parses the refs mapping, rewrites only the target line, then checks that the refs mapping and every byte before and after the block are unchanged except for that one value. It supports the scalar styles the credentials writer emits and refuses a construct outside that subset. The update is refused when the file changed after it was read. The new text is written to an owner-only temporary file beside the document, flushed to disk, and renamed over the document, so a failure never leaves a half-written credentials file. Comments, the document version, the `records` section and the formatting of every other entry are kept.

## Security notes

- The plugin registers a command, not a tool. Command execution does not pass through the model and the handler runs with the plugin's own file access, so this plugin adds a human surface over the access a Python plugin already has.
- dsh records command lifecycle entries in the session event log. A `/cred set` line contains the new value as a command argument, and a `/cred read` result contains the value it printed. Commands are not sent to the model, but the log is persisted. Treat session storage with the same care as the credentials file, or rotate a value if that storage is exposed.
- The confirmation step prevents an accidental write. It is not authentication: any local process that runs as the same user could read or write the document directly.
- The store writes with mode 600. The Harness credentials provider refuses to serve a document that is readable beyond its owner.

## Development

The repository root is a uv project whose package lives under `src/`, so one sync installs the plugin package and the tools. The dev dependency group pulls the Python SDK from its repository; the loader always provides the SDK at run time, so the plugin does not depend on the SDK in its runtime dependencies.

```sh
uv sync
uv run python -m unittest discover -s tests -v
./check_code.sh
./format_code.sh --apply
```

`check_code.sh` runs ty, ruff check, the ruff format check and the docstring/code format check. `format_code.sh` checks formatting without writing, and `--apply` rewrites the files. Both scripts use the tools from the uv environment and fall back to `PATH`.

The plugin has no third-party runtime dependency. It parses the refs subset itself, so the shared profile environment only needs the interpreter.
