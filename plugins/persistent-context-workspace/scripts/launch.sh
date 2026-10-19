#!/bin/sh
# POSIX bootstrap: no Python, package, environment, or model installation.

if [ "${PCW_PYTHON+x}" = x ]; then
    pcw_python=$PCW_PYTHON
    case "$pcw_python" in
        /*) ;;
        *) printf '%s\n' 'PCW_PYTHON must be a nonempty absolute executable path; it is not a shell command.' >&2; exit 2 ;;
    esac
else
    pcw_python=$(command -v python3 2>/dev/null) || {
        printf '%s\n' 'PCW requires Python >=3.11. No python3 was found on PATH; install Python explicitly or set PCW_PYTHON to an existing absolute executable path.' >&2
        exit 2
    }
    case "$pcw_python" in
        /*) ;;
        *) pcw_python="$(pwd -P)/$pcw_python" ;;
    esac
fi
if [ ! -f "$pcw_python" ] || [ ! -x "$pcw_python" ]; then
    printf 'PCW runtime interpreter is unavailable or not executable: %s\n' "$pcw_python" >&2
    exit 2
fi

# -S avoids site startup before version validation; -E/-s also work on old
# Python releases. Only the built-in sys module is needed for this probe.
pcw_probe=$("$pcw_python" -E -s -S -c 'import sys
if sys.version_info < (3, 11):
    sys.stderr.write("PCW requires Python >=3.11; selected " + sys.executable + " reports " + sys.version.split()[0] + ".\n")
    sys.exit(2)
sys.stdout.write("PCW_PYTHON_READY")')
pcw_probe_status=$?
if [ "$pcw_probe_status" -ne 0 ] || [ "$pcw_probe" != PCW_PYTHON_READY ]; then
    printf 'PCW could not validate the selected Python interpreter: %s\n' "$pcw_python" >&2
    exit 2
fi

case "$0" in
    */*) pcw_script_directory=${0%/*} ;;
    *) pcw_script_directory=. ;;
esac
pcw_plugin_root=$(CDPATH= cd -P "$pcw_script_directory/.." && pwd -P) || exit 2
if [ ! -r "$pcw_plugin_root/scripts/launch.py" ]; then
    printf 'PCW bundled launcher is unavailable under: %s\n' "$pcw_plugin_root" >&2
    exit 2
fi
# Ignore PYTHONPATH and exclude unsafe CWD/script path insertion, while keeping
# the selected interpreter's normal installed packages and virtualenv policy.
# -E would ignore PYTHONNOUSERSITE; honor that explicit restriction with -s.
# -B avoids bytecode writes in an installed plugin cache.
if [ -n "${PYTHONNOUSERSITE:-}" ]; then
    exec "$pcw_python" -E -P -B -s "$pcw_plugin_root/scripts/launch.py" "$@"
fi
exec "$pcw_python" -E -P -B "$pcw_plugin_root/scripts/launch.py" "$@"
