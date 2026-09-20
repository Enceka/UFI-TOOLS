#!/bin/sh
# Remove the UFI-TOOLS Linux backend from this device.
#
#   ./uninstall.sh            remove the service and its files, keep the data dir
#   ./uninstall.sh --purge    also delete /var/lib/ufi-tools (token, tasks, theme)
#   ./uninstall.sh --dry-run  print what would be removed
#
# The data directory holds the access token and the scheduled tasks, so it is the
# one thing that is kept by default: reinstalling then keeps working as before.
#
# This also reverses the two changes UFI-TOOLS makes outside its own directories:
# the hotspot systemd drop-in and the managed hostapd/dnsmasq drop-ins pointing
# at the managed config.  Without that, the device would keep serving the access
# point settings that were last set from the web UI.
set -eu

LIB_DIR=/usr/lib/ufi-tools
WWW_DIR=/usr/share/ufi-tools
DOC_DIR=/usr/share/doc/ufi-tools
DATA_DIR=/var/lib/ufi-tools
UNIT_DIR=/etc/systemd/system
UNIT=ufi-tools.service
#: The drop-in configure_hotspot() writes to point e5-hotspot at our config.
HOTSPOT_DROPIN=/etc/systemd/system/e5-hotspot.service.d/10-ufi-tools.conf

PURGE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --data-dir) shift; DATA_DIR="${1:-}" ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

run() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  would run: $*"
    else
        "$@"
    fi
}

remove() {
    if [ ! -e "$1" ]; then
        return 0
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  would remove: $1"
    else
        rm -rf "$1"
        echo "  removed: $1"
    fi
}

echo "==> stopping the service"
if command -v systemctl >/dev/null 2>&1 && [ -f "$UNIT_DIR/$UNIT" ]; then
    run systemctl stop "$UNIT" || true
    run systemctl disable "$UNIT" || true
else
    echo "  (no systemd unit installed)"
fi

echo "==> removing the unit and drop-ins"
remove "$UNIT_DIR/$UNIT"
remove "$HOTSPOT_DROPIN"
if [ -d "$UNIT_DIR/e5-hotspot.service.d" ] && [ -z "$(ls -A "$UNIT_DIR/e5-hotspot.service.d" 2>/dev/null)" ]; then
    run rmdir "$UNIT_DIR/e5-hotspot.service.d" || true
fi

echo "==> removing program files"
remove "$LIB_DIR"
remove "$WWW_DIR"
remove "$DOC_DIR"
remove /usr/bin/ufi-tools
remove /usr/bin/ufi_req

if [ "$PURGE" = "1" ]; then
    echo "==> removing data (token, tasks, theme, plugins)"
    remove "$DATA_DIR"
else
    echo "==> keeping $DATA_DIR (use --purge to delete it)"
fi

if command -v systemctl >/dev/null 2>&1; then
    echo "==> reloading systemd"
    run systemctl daemon-reload || true
fi

echo
echo "Done.  The hotspot, if it was running, keeps whatever configuration it had;"
echo "restart it to fall back to the device's own /etc/hostapd/e5.conf:"
echo "    systemctl restart e5-hotspot.service"
