#!/bin/sh
# Make the mounted data and model folders writable by the unprivileged "ppe" user, then
# drop root. Bind mounts that Docker creates on the host start out owned by root.
set -e
if [ "$(id -u)" = "0" ]; then
  for d in /data /models; do
    mkdir -p "$d"
    find "$d" -xdev \( ! -user ppe -o ! -group ppe \) -exec chown ppe:ppe {} + 2>/dev/null || true
  done
  exec setpriv --reuid=ppe --regid=ppe --init-groups "$@"
fi
exec "$@"
