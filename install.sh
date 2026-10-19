#!/bin/sh
# Delegate fresh module installation to the installed Core lifecycle.
set -eu
case "$0" in */*) script_directory=${0%/*} ;; *) script_directory=. ;; esac
module_root=$(CDPATH= cd -- "$script_directory" && pwd)
root=
plan=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "--root requires a directory." >&2; exit 2; }
            root=$2; shift 2 ;;
        --plan) plan=true; shift ;;
        -h|--help)
            echo "Usage: sh install.sh [--root DATA_DIRECTORY] [--plan]"
            echo "Requires Contextopus Core >=1.5,<2. Omit --root to use its bound default root."
            exit 0 ;;
        *) echo "Unknown option: $1. Use --help." >&2; exit 2 ;;
    esac
done
if ! command -v contextopus >/dev/null 2>&1; then
    echo "Install Contextopus Core >=1.5,<2 first: https://github.com/pikiby/contextopus-core#install-contextopus" >&2
    exit 127
fi
if [ -e "$module_root/.git" ]; then
    echo "Build the module first: python scripts/build-module.py dist/workspace" >&2
    echo "Then run: sh dist/workspace/install.sh [--root DATA_DIRECTORY] [--plan]" >&2
    exit 2
fi
if [ -n "$root" ]; then set -- --root "$root"; else set --; fi
set -- "$@" modules install workspace --source "$module_root"
if [ "$plan" = true ]; then set -- "$@" --plan; fi
exec contextopus "$@"
