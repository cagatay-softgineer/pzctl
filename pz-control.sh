#!/usr/bin/env sh
# Launcher for Linux and macOS. The Windows equivalent is PZ-Control.bat.
#
# pzctl reads the game's files from its own location on disk, so this must live
# inside the Project Zomboid Dedicated Server directory, beside start-server.sh.
cd "$(dirname "$0")" || exit 1

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        exec "$candidate" -m pzctl --open "$@"
    fi
done

echo "Python 3.11 or newer is required but was not found on PATH." >&2
exit 1
