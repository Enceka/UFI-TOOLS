#!/bin/sh
# Install the UFI-TOOLS Linux backend on a Debian-style system (E5-LINUX).
#
#   ./install.sh                 install, generate a token, enable + start
#   ./install.sh --no-systemd    copy the files only
#   ./install.sh --token secret1 install with a known token
#
# Everything is configurable so the same script works from a source checkout and
# from a package build directory.
set -eu

LIB_DIR=/usr/lib/ufi-tools
BIN_DIR=/usr/bin
WWW_DIR=/usr/share/ufi-tools/www
SHIM_DIR=/usr/share/ufi-tools/www-linux
DOC_DIR=/usr/share/doc/ufi-tools
DATA_DIR=/var/lib/ufi-tools
UNIT_DIR=/etc/systemd/system

SYSTEMD=1
TOKEN=""
FRONTEND=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-systemd) SYSTEMD=0 ;;
        --token) shift; TOKEN="${1:-}" ;;
        --frontend) shift; FRONTEND="${1:-}" ;;
        --data-dir) shift; DATA_DIR="${1:-}" ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(dirname "$HERE")

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 1
fi

echo "==> installing package to $LIB_DIR"
install -d -m 755 "$LIB_DIR"
rm -rf "$LIB_DIR/ufitools"
cp -R "$HERE/ufitools" "$LIB_DIR/ufitools"
find "$LIB_DIR/ufitools" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# The web frontend is plain static files shipped in linux/www; there is no build
# step (the Android tree had one because it packed the result into an APK).
if [ -z "$FRONTEND" ]; then
    for candidate in "$HERE/www" "$REPO/linux/www"; do
        if [ -f "$candidate/index.html" ]; then FRONTEND="$candidate"; break; fi
    done
fi
if [ -z "$FRONTEND" ] || [ ! -f "$FRONTEND/index.html" ]; then
    echo "warning: no web frontend found; set --frontend <dir> later or the UI will 404" >&2
else
    echo "==> installing web frontend from $FRONTEND to $WWW_DIR"
    install -d -m 755 "$WWW_DIR"
    rm -rf "$WWW_DIR"/*
    cp -R "$FRONTEND"/. "$WWW_DIR"/
fi

echo "==> installing the frontend shim to $SHIM_DIR"
install -d -m 755 "$SHIM_DIR"
cp -R "$HERE/www-linux"/. "$SHIM_DIR"/

echo "==> installing launchers"
install -m 755 "$HERE/bin/ufi-tools" "$BIN_DIR/ufi-tools"
install -m 755 "$HERE/bin/ufi_req" "$BIN_DIR/ufi_req"
install -d -m 755 "$DOC_DIR"
[ -f "$HERE/README.md" ] && install -m 644 "$HERE/README.md" "$DOC_DIR/README.md"

echo "==> creating data directory $DATA_DIR"
install -d -m 700 "$DATA_DIR"

if [ "$SYSTEMD" = "1" ]; then
    echo "==> installing systemd unit"
    install -d -m 755 "$UNIT_DIR"
    install -m 644 "$HERE/systemd/ufi-tools.service" "$UNIT_DIR/ufi-tools.service"
fi

# Generate (or set) the access token.  A default 'admin' token on a device that
# may be reachable from the hotspot LAN is the one thing we should not ship.
if [ -n "$TOKEN" ]; then
    UFI_TOOLS_DATA="$DATA_DIR" PYTHONPATH="$LIB_DIR" \
        python3 -m ufitools --data-dir "$DATA_DIR" set-token "$TOKEN" >/dev/null
    echo "==> access token set as requested"
else
    TOKEN=$(python3 - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(16)))
PY
)
    UFI_TOOLS_DATA="$DATA_DIR" PYTHONPATH="$LIB_DIR" \
        python3 -m ufitools --data-dir "$DATA_DIR" set-token "$TOKEN" >/dev/null
    echo "==> generated access token: $TOKEN"
    echo "    (change it any time with: ufi-tools --data-dir $DATA_DIR set-token NEW)"
fi

if [ "$SYSTEMD" = "1" ]; then
    systemctl daemon-reload
    systemctl enable ufi-tools.service >/dev/null 2>&1 || true
    systemctl restart ufi-tools.service || true
    sleep 1
    systemctl --no-pager --lines=0 status ufi-tools.service || true
fi

cat <<EOF

Done.  Open http://<device-ip>:2333/ and log in with the token above.

Verify the bridges at any time with:
    ufi-tools --data-dir $DATA_DIR status
EOF
