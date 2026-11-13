# dsh-credentials-helper

English | [简体中文](README.zh.md)

A [dsh-mloader](https://github.com/Mooling0602/dsh-mloader) Python plugin that exposes the DeepSeek Harness credential references through one human-facing slash command. It registers no tool, so the model cannot read or write the credential document through this plugin.

The plugin works on `<DSH_HOME>/.credentials.yaml`, the file the Harness credentials provider owns. It reads the `refs` mapping and can replace one value or add one entry. Writes are staged behind a confirmation step, the file stays owner-only, and the document is replaced atomically under the same writer lock the Harness uses. A document that has no `refs` section, or has the empty inline form the writer leaves after the last reference is deleted, is completed with a block mapping on the first write.

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

The loader watches the plugin root and mounts the directory. When watching is disabled, load it with `/mloader load dsh-credentials-helper`. The command asks for the name `/cred` and falls back to `/credentials` when another plugin already owns the first name. To install it as a package instead, build an archive from the same directory and hand it to the runtime command line or the loader install command:

```sh
python3 <dsh-mloader>/src/runtime/python_sdk/run_cli.py pack src -o dist/dsh-credentials-helper.mloader.pack
/mloader install dist/dsh-credentials-helper.mloader.pack
```

## Commands

| Command | Effect |
| --- | --- |
| `/cred set <KEY> <VALUE>` | Stage an update to the reference `KEY` and ask for confirmation. |
| `/cred set <KEY> <VALUE> --skip-confirm` | Write the update immediately. |
| `/cred set <KEY> -- <VALUE>` | Stage one update whose value starts with `--`. |
| `/cred read <KEY>` | Print the decoded value of `KEY`. |
| `/cred check <KEY>` | Report whether `KEY` exists and carries a usable value, masked. |
| `/cred remove <KEY>` | Stage the deletion of `KEY` and ask for confirmation. |
| `/cred remove <PATTERN>` | Stage the deletion of every reference matching `*`/`?` and list them all. |
| `/cred ok` or `/cred confirm` | Write the update or deletion that is waiting for confirmation. |
| `/cred help` | Show the usage text. |

`KEY` is a credential reference name, the same environment-variable-style name the Harness uses inside `refs` (`[A-Za-z_][A-Za-z0-9_]*`). `VALUE` is one argument, so quote a value that contains spaces and put `--` before a value that starts with two dashes. A new `KEY` is added to the `refs` mapping; an existing key keeps its position.

`/cred check` replaces a read failure with three answers: the key is absent, the key exists with a usable value, or the key exists with an unusable one (missing, empty, `null` or not a string). The third answer prints `<null>` and never echoes the stored text; the other two still mask a valid value. A document that cannot be read or parsed at all is still an error, because then no entry can be described.

`/cred remove` deletes entries and always takes the confirmation step: `--skip-confirm` is refused, because a deleted credential cannot be recovered from this command. The target may name one reference or use the wildcards `*` (any run of characters) and `?` (exactly one); everything else is literal, and a target without wildcards has to match a whole name, so `A.B` is refused as a malformed name instead of matching `AXB`.

The prompt lists every reference about to go, each with its masked current value, and nothing is written until `/cred ok`, `/cred confirm` or an identical repeat of the command. A `*` target is the widest delete the command can express and still takes confirmation, so it lists every key before anything happens. Removing references that are already absent writes nothing and says so. Each entry goes whole, including a multi-line value and the comment block written directly above it, which the harness reads as that entry's note; a comment separated from the entry by a blank line stays. Deleting every reference leaves the bare `refs:` header in place, so the section stays addressable.

One confirmation covers the whole list and the document is read and locked once, so a listing the operator approved cannot half-apply against a file something else changed in between.

## Confirmation

The first `/cred set` stores the pending update in the plugin process and shows the reference name with masked current and new values. Nothing is written. The update is written when one of these happens:

- the operator runs `/cred ok` or `/cred confirm`;
- the operator runs the identical `/cred set` command again;
- the original command carried `--skip-confirm`.

A different `/cred set` replaces the pending update. When the document does not exist yet, the confirmation shows `(not set)` as the current value and the staged write creates the document in the version 1 layout. The pending value lives only in the plugin process memory and is discarded when the plugin unloads.

## Masking and output

Every message except `/cred read` shows a masked value: nothing for twelve characters or fewer; a longer value shows a three- or four-character prefix and suffix, never more than half of it. `/cred read` prints the decoded value because that is its purpose, and `/cred check` reads a value only to decide between its three answers. Error messages never include a decoded value, and a parse error reports only the document path and a line number, never the offending text.

An error built from two clauses is sent as two lines, split between them rather than at a column width. The chat client shows a command outcome as a one-line summary that elides and only offers the disclosure body when the text contains a newline, so the second clause would otherwise be cut off with no way to read it; the client wraps a merely-wide line on its own, so folding the text here would break it at an arbitrary point and fight that wrapping. Usage text already spans several lines and is left alone.

## Where the document lives

The Harness keeps the document at `<DSH_HOME>/.credentials.yaml`, where `DSH_HOME` defaults to `~/.dsh`. The loader starts a plugin child process without any `DSH_*` variable, so the plugin resolves the home in this order:

1. an explicit `DSH_HOME` value in the plugin environment, which must be absolute;
2. the install layout, when the plugin runs from `<home>/profiles/<profile>/mloader/plugins/dsh-credentials-helper`;
3. `~/.dsh`.

The first and last cases cover the default installation. The second case is what keeps a custom `DSH_HOME` working, because the plugin can derive the same home from its own location.

## Write safety

The store takes the same cross-process writer lock the Harness uses (`<document>.lock`: created with `wx` semantics, records the holder's process id, removed when the cycle ends) and performs its whole read-modify-write under it, so the plugin and the Harness never replace each other's snapshot. A lock whose recorded process exited is taken over through a claim file, and a lock that stays held fails the command after a bounded wait instead of writing over a state the plugin could not read.

The scalar reader covers the forms the Harness writer emits: plain, single- and double-quoted scalars, including values wrapped over several lines and double-quoted escapes, and literal or folded block scalars with every chomping indicator. A construct outside that subset is refused instead of guessed at. The `refs` section may be a block mapping, the empty inline form the writer leaves after the last reference is deleted (`refs: {}`), or absent; a write to an absent section creates the version 1 layout, and only for a document that already declares that version or is empty apart from comments. Comments and blank lines never truncate the section, and the indentation of an existing entry is reused. YAML forbids tabs as indentation, so a tab-indented entry is refused with a message instead of being rewritten.

Only the target entry is replaced. The store then verifies that the refs mapping and every byte before and after the section are unchanged except for that value; when a writer that ignores the lock changed the file in the meantime, the fingerprint check refuses the update. The new text is written to an owner-only temporary file beside the document, flushed to disk, and renamed over the document, so a failure never leaves a half-written credentials file, and the directory is flushed where the platform allows it. On Windows, transient replacement failures are retried and the owner-only chmod step is skipped, because Windows has no descriptor-level mode. Comments, the document version, the `records` section and the formatting of every other entry are kept.

## Security notes

- The plugin registers a command, not a tool. Command execution does not pass through the model and the handler runs with the plugin's own file access, so this plugin adds a human surface over the access a Python plugin already has.
- dsh records command lifecycle entries in the session event log. A `/cred set` line contains the new value as a command argument, and a `/cred read` result contains the value it printed. Commands are not sent to the model, but the log is persisted. Treat session storage with the same care as the credentials file, or rotate a value if that storage is exposed.
- The confirmation step prevents an accidental write. It is not authentication, and the writer lock is coordination rather than a security boundary: any local process that runs as the same user could read or write the document directly.
- The store writes with mode 600. The Harness credentials provider refuses to serve a document that is readable beyond its owner.

## Development

The repository root is a uv project whose package lives under `src/`, so one sync installs the plugin package and the tools. The dev dependency group pulls the Python SDK from its repository; the loader always provides the SDK at run time, so the plugin does not depend on the SDK in its runtime dependencies.

```sh
uv sync
uv run python -m unittest discover -s tests -v
./check_code.sh
./format_code.sh --apply
```

`check_code.sh` runs ty, ruff check, the ruff format check and the docstring/code format check. `format_code.sh` checks formatting without writing, and `--apply` rewrites the files. Both scripts use the tools from the uv environment and fall back to `PATH`. The tests include a document captured from the Harness writer, so the scalar forms and the refs section shapes the writer produces stay covered.

The plugin has no third-party runtime dependency. It parses the refs subset itself, so the shared profile environment only needs the interpreter.
