#!/usr/bin/env bash
# Installs or updates the dedicated server, then hands off to pzctl.
set -euo pipefail

SERVER_DIR="${PZ_SERVER_DIR:-/pz/steamapps/common/pzserver}"
ZOMBOID="${ZOMBOID_DIR:-/data/Zomboid}"
APP_ID="${PZ_APP_ID:-380870}"

mkdir -p "$SERVER_DIR" "$ZOMBOID"

# The server is installed into the volume rather than the image, so this runs on
# first start and on every start unless turned off. `validate` repairs a partial
# download, which is the state a container killed mid-install leaves behind.
if [ ! -x "$SERVER_DIR/jre64/bin/java" ] || [ "${UPDATE_ON_START:-1}" = "1" ]; then
    echo "pzctl: installing/updating Project Zomboid dedicated server (app $APP_ID)"
    steamcmd +force_install_dir "$SERVER_DIR" \
             +login anonymous \
             +app_update "$APP_ID" validate \
             +quit
fi

if [ ! -x "$SERVER_DIR/jre64/bin/java" ]; then
    echo "pzctl: the server install has no bundled java at $SERVER_DIR/jre64/bin/java" >&2
    echo "pzctl: SteamCMD may have failed - check the output above" >&2
    exit 1
fi

# Copied, not linked: pzctl works out where it is from its own file path, and a
# symlink would resolve to the staging directory instead of the server folder.
rm -rf "$SERVER_DIR/pzctl"
cp -r /opt/pzctl-src/pzctl "$SERVER_DIR/pzctl"

# The world lives outside the server directory, on its own volume.
export USERPROFILE="${ZOMBOID%/Zomboid}"
export HOME="${ZOMBOID%/Zomboid}"

cd "$SERVER_DIR"

# exec so pzctl replaces this shell as PID 1 and gets SIGTERM straight from
# `docker stop`. Without it the signal would stop at bash and the world would
# never be saved.
exec python3 -m pzctl --host "${PZCTL_HOST:-0.0.0.0}" --port "${PZCTL_PORT:-8077}" "$@"
