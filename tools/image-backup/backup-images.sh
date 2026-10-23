#!/usr/bin/env bash
# Save every PiVault container image into this folder, one compressed file per image.
# Run on Greenie while the Pi still has everything installed. Requires the "pivault" SSH alias.
set -euo pipefail

HOST="${PIVAULT_HOST:-pivault}"
COMPOSE=/home/snx/pi-services/docker-compose.yml
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

archive_name() { printf '%s.tar.gz' "$(printf '%s' "$1" | tr '/:@' '___')"; }

scp -q "$HOST:$COMPOSE" docker-compose.yml
ssh "$HOST" "awk '\$1==\"image:\"{print \$2}' $COMPOSE | sort -u" > images.txt
if grep -q '@sha256' images.txt; then
    echo "Stopped: an image is pinned by digest. Pin it by version tag first, or it will not restore offline." >&2
    exit 1
fi

failed=0
while read -r image; do
    file="$(archive_name "$image")"
    echo "Saving $image"
    if ssh -n "$HOST" docker save "$image" | gzip > "$file.part"; then
        mv -f -- "$file.part" "$file"
    else
        rm -f -- "$file.part"
        echo "FAILED: $image" >&2
        failed=1
    fi
done < images.txt

echo
ls -lh -- *.tar.gz
if [[ "$failed" == 0 ]]; then
    echo "Backup complete: $(wc -l < images.txt) images."
else
    echo "Backup incomplete: see FAILED lines above." >&2
fi
exit "$failed"
