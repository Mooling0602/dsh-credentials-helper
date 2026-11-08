#!/usr/bin/env bash
# Run static checks for the plugin Python source without modifying it.
set -uo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd -- "$root" || exit 1

if [[ -f "$root/.venv/bin/activate" ]]; then
    source "$root/.venv/bin/activate"
elif [[ -f "$root/.venv/Scripts/activate" ]]; then
    source "$root/.venv/Scripts/activate"
fi

source_paths=(src tests)
type_paths=(src)
status=0

for tool in ty ruff docformatter; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Missing required tool: %s\n' "$tool" >&2
        status=1
    fi
done

printf 'Running ty check\n'
ty check --exclude '**/.venv/**' "${type_paths[@]}" || status=1

printf 'Running ruff check\n'
ruff check --extend-exclude .venv "${source_paths[@]}" || status=1

printf 'Running ruff format check\n'
ruff format --check --extend-exclude .venv "${source_paths[@]}" || status=1

printf 'Checking combined docstring and code formatting\n'
"$root/format_code.sh" || status=1

exit "$status"
