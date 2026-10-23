#!/usr/bin/env bash
# Load PiVault container images from this folder back onto the Pi. No internet needed.
# Run on Greenie. By default only images missing on the Pi are loaded; --all reloads every image.
set -euo pipefail

HOST="${PIVAULT_HOST:-pivault}"
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

archive_name() { printf '%s.tar.gz' "$(printf '%s' "$1" | tr '/:@' '___')"; }

case "${1:-}" in
    '') mode=missing ;;
    --all) mode=all ;;
    *) echo "Usage: bash restore-images.sh [--all]" >&2; exit 2 ;;
esac
[[ -f images.txt ]] || { echo "Stopped: images.txt is missing from this folder." >&2; exit 1; }

failed=0
loaded=0
while read -r image; do
    file="$(archive_name "$image")"
    if [[ ! -f "$file" ]]; then
        echo "MISSING BACKUP: $image" >&2
        failed=1
        continue
    fi
    if [[ "$mode" == missing ]] && ssh -n "$HOST" docker image inspect "$image" > /dev/null 2>&1; then
        echo "Already on the Pi: $image"
        continue
    fi
    echo "Loading $image"
    if gunzip -c -- "$file" | ssh "$HOST" docker load > /dev/null; then
        loaded=$((loaded + 1))
    else
        echo "FAILED: $image" >&2
        failed=1
    fi
done < images.txt

echo
echo "Loaded $loaded image(s)."
if [[ "$failed" == 0 ]]; then
    echo "All images are on the Pi. Start or refresh the apps on pivault with:"
    echo "  cd /home/snx/pi-services && sudo docker-compose -f docker-compose.yml up -d"
else
    echo "Restore incomplete: see MISSING or FAILED lines above." >&2
fi
exit "$failed"
