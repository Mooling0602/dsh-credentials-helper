#!/usr/bin/env bash
# Check or apply docformatter followed by ruff format to the plugin source.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd -- "$root"

if [[ -f "$root/.venv/bin/activate" ]]; then
    source "$root/.venv/bin/activate"
elif [[ -f "$root/.venv/Scripts/activate" ]]; then
    source "$root/.venv/Scripts/activate"
fi

if [[ $# -gt 1 || ( $# -eq 1 && $1 != --apply ) ]]; then
    printf 'Usage: %s [--apply]\n' "$0" >&2
    exit 2
fi
for tool in docformatter ruff; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Missing required tool: %s\n' "$tool" >&2
        exit 1
    fi
done

source_paths=(src tests)

run_docformatter() {
    local status=0
    docformatter --in-place -r "$@" --exclude .venv || status=$?
    if [[ $status -ne 0 && $status -ne 3 ]]; then
        printf 'docformatter failed with exit code %s\n' "$status" >&2
        return "$status"
    fi
}

# Run Ruff last so both formatters converge on blank lines.
if [[ ${1:-} == --apply ]]; then
    run_docformatter "${source_paths[@]}"
    ruff format --extend-exclude .venv "${source_paths[@]}"
    printf 'Formatted plugin docstrings and code.\n'
else
    tmp=$(mktemp -d)
    trap 'rm -rf -- "$tmp"' EXIT
    tar -C "$root" --exclude=.venv --exclude=__pycache__ --exclude='*.pyc' -cf - "${source_paths[@]}" | tar -C "$tmp" -xf -
    tmp_paths=()
    for path in "${source_paths[@]}"; do
        tmp_paths+=("$tmp/$path")
    done
    run_docformatter "${tmp_paths[@]}"
    ruff format --extend-exclude .venv "${tmp_paths[@]}" >/dev/null
    status=0
    for path in "${source_paths[@]}"; do
        diff -ru --exclude=.venv --exclude=__pycache__ --exclude='*.pyc' "$root/$path" "$tmp/$path" || {
            diff_status=$?
            if [[ $diff_status -ne 1 ]]; then
                exit "$diff_status"
            fi
            status=1
        }
    done
    if [[ $status -eq 0 ]]; then
        printf 'Plugin Python formatting is converged.\n'
    else
        printf 'Plugin Python formatting differs. Run %s --apply to apply it.\n' "$0" >&2
        exit 1
    fi
fi
