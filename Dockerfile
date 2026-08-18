# pzctl + a Project Zomboid dedicated server.
#
# The game is NOT baked into this image. SteamCMD installs it on first run into
# a volume, so nothing here redistributes content that is not ours, and the
# image stays small.
#
# Ubuntu 24.04 for Python 3.12 - pzctl needs 3.11 or newer, and 22.04 ships 3.10.
FROM ubuntu:24.04

# steamcmd lives in multiverse and its licence prompt has to be answered
# up front, or the build hangs waiting for a keypress nobody can give it.
RUN set -eux; \
    dpkg --add-architecture i386; \
    echo steam steam/question select "I AGREE" | debconf-set-selections; \
    echo steam steam/license note '' | debconf-set-selections; \
    sed -i 's/^Components: main$/Components: main restricted universe multiverse/' \
        /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates locales python3 steamcmd; \
    rm -rf /var/lib/apt/lists/*; \
    ln -sf /usr/games/steamcmd /usr/local/bin/steamcmd

# The game writes non-ASCII into its logs and configs.
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1

# pzctl derives every path from where its package sits, and mods.py walks two
# levels up to find Workshop content. Mirroring Steam's own layout is what makes
# that resolve: <root>/steamapps/common/<server> beside <root>/steamapps/workshop.
ENV PZ_ROOT=/pz \
    PZ_SERVER_DIR=/pz/steamapps/common/pzserver \
    ZOMBOID_DIR=/data/Zomboid \
    PZ_APP_ID=380870 \
    UPDATE_ON_START=1

# Staged here and copied into the server directory at runtime. It cannot be
# symlinked: config.py resolves __file__, which follows links, and SERVER_DIR
# would come out as /opt instead of the server folder.
COPY pzctl /opt/pzctl-src/pzctl
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/pz", "/data"]

# 8077 panel, 16261/16262 the game itself.
EXPOSE 8077/tcp 16261/udp 16262/udp

# exec form so pzctl is PID 1 and receives SIGTERM from `docker stop` directly.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
