#!/usr/bin/env bash
# PiVault Cinema — one new film per genre per Pennsylvania calendar day.
# Downloads use curl from Bash. Python 3 only reads LoC metadata.
# No media is re-encoded, zipped, replaced, or automatically removed.

BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/home/snx/pi-services/cinema"
VIDEO_DISK="/srv/disk1"
VIDEO_ROOT="$VIDEO_DISK/videos"
DISK_LIMIT_PERCENT=90
NOTIFICATION_ROOT="/srv/pi-server/config/notifications"
export TZ=America/New_York

log() { printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"; }
die() { log "ERROR: $*" >&2; return 1; }

host_check() {
    (( EUID != 0 )) || { die 'Run as snx, not with sudo.'; return 1; }
    mountpoint -q -- "$VIDEO_DISK" || { die "$VIDEO_DISK is not mounted."; return 1; }
}

catalogue() {
    printf 'PiVault Americana — %s films in %s primary genre queues\n' "$(queue | wc -l)" "$(queue | cut -f1 | sort -u | wc -l)"
    printf 'Existing comparison: Shadow of a Doubt (1943); Spellbound (1945).\n'
    printf 'Each film is downloaded only once, under its primary genre.\n\n'
    local gib minutes height bitrate
    while IFS=$'\t' read -r genre key title year url size hash loc reason rights minutes height; do
        if [[ "$url" == - ]]; then
            printf '%s | %s (%s) | %s min | source found at download time\n  %s\n  Rights basis: %s\n' \
                "$genre" "$title" "$year" "$minutes" "$reason" "$rights"
            continue
        fi
        gib=$(awk -v n="$size" 'BEGIN {printf "%.2f",n/1073741824}')
        bitrate=$(awk -v n="$size" -v m="$minutes" 'BEGIN {printf "%.2f",n*8/(m*60*1000000)}')
        printf '%s | %s (%s) | %s min | %.2f GiB | %sp | %s Mbps\n  %s\n  Archive: %s\n  Rights basis: %s\n' \
            "$genre" "$title" "$year" "$minutes" "$gib" "$height" "$bitrate" "$reason" "$url" "$rights"
    done < <(queue)
}

preview() {
    local genre key title year url size hash loc reason rights minutes height gib bitrate total=0
    declare -A shown=()
    printf 'FIRST ROUND — archive fallback sizes (LoC files may differ)\n'
    while IFS=$'\t' read -r genre key title year url size hash loc reason rights minutes height; do
        [[ -z "${shown[$genre]:-}" && "$url" != - ]] || continue
        shown[$genre]=1
        gib=$(awk -v n="$size" 'BEGIN {printf "%.2f",n/1073741824}')
        bitrate=$(awk -v n="$size" -v m="$minutes" 'BEGIN {printf "%.2f",n*8/(m*60*1000000)}')
        printf '%-24s %-42s %6s min  %5s GiB  %5s Mbps\n' "$genre" "$title ($year)" "$minutes" "$gib" "$bitrate"
        total=$((total+size))
    done < <(queue)
    awk -v n="$total" 'BEGIN {printf "Archive fallback total: %.2f GiB\n",n/1073741824}'
}

valid_file() {
    local file=$1 expected=$2 hash=$3 got
    [[ -f "$file" ]] || return 1
    [[ "$(stat -c %s -- "$file")" == "$expected" ]] || return 1
    if [[ "$hash" != - ]]; then
        got=$(md5sum -- "$file") || return 1
        [[ "${got%% *}" == "$hash" ]] || return 1
    else
        # LoC does not consistently publish a checksum; require the MP4 signature.
        [[ "$(dd if="$file" bs=1 skip=4 count=4 status=none 2>/dev/null)" == ftyp ]] || return 1
    fi
}

loc_source() {
    local item=$1 metadata=$2 output headers
    [[ "$item" != - ]] || return 1
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 40 --max-filesize 10485760 \
        --output "$metadata" "${item}?fo=json" 2>/dev/null || return 1
    output=$(python3 - "$metadata" <<'PY'
import json, sys, urllib.parse
try:
    obj=json.load(open(sys.argv[1])); found={}
    def walk(value):
        if isinstance(value, dict):
            url=value.get('url', '')
            if isinstance(url, str):
                parsed=urllib.parse.urlsplit(url)
                if parsed.scheme=='https' and (parsed.hostname or '').endswith('.loc.gov') and parsed.path.lower().endswith('.mp4'):
                    try: score=int(value.get('width',0) or 0)*int(value.get('height',0) or 0)
                    except (TypeError,ValueError): score=0
                    try: height=int(value.get('height',0) or 0)
                    except (TypeError,ValueError): height=0
                    if height > 0: found[url]=max((score,height),found.get(url,(0,0)))
            for v in value.values(): walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    walk(obj.get('resources',[]))
    if not found: sys.exit(1)
    url=max(found,key=found.get)
    print(url+'\t'+str(found[url][1]))
except (OSError,ValueError): sys.exit(1)
PY
    ) || return 1
    IFS=$'\t' read -r output LOC_HEIGHT <<< "$output"
    headers=$(curl --fail --silent --show-error --head --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 40 "$output" 2>/dev/null) || return 1
    LOC_SIZE=$(printf '%s\n' "$headers" | awk 'tolower($1)=="content-length:" {gsub("\r", "", $2); n=$2} END {print n}')
    [[ "$LOC_SIZE" =~ ^[1-9][0-9]*$ ]] || return 1
    LOC_URL=$output
}

daily_count() {
    local file url size hash day genre count=0
    for file in "$BASE"/state/*.done; do
        [[ -f "$file" ]] || continue
        IFS=$'\t' read -r url size hash day genre < "$file"
        if [[ "$day" == "$RUN_DAY" && "$genre" == "$1" ]]; then count=$((count+1)); fi
    done
    printf '%s\n' "$count"
}

prepare_rights() {
    local found=()
    [[ ! -f "$BASE/state/$1.record.json" ]] || \
        found=(--record "$BASE/state/$1.record.json" --archive-metadata "$BASE/state/$1.archive.json")
    python3 "$BASE/cinema-rights.py" --evidence "$BASE/rights-evidence" \
        --key "$1" --provider "$2" --url "$3" --bytes "$4" --md5 "$5" \
        --loc-metadata "$BASE/state/$1.loc.json" --output "$6" "${found[@]}"
}

# Curated films without a pinned transfer are found on archive.org once, then kept fixed.
find_source() {
    local key=$1 title=$2 year=$3 reference=$4 rights=$5 line
    if [[ ! -s "$BASE/state/$key.source" ]]; then
        line=$(python3 "$BASE/cinema-resolve.py" --title "$title" --year "$year" --minutes "$reference" \
            --key "$key" --rights "$rights" --metadata-out "$BASE/state/$key.archive.json" \
            --record-out "$BASE/state/$key.record.json") || return 1
        printf '%s\n' "$line" > "$BASE/state/$key.source.tmp" && mv -- "$BASE/state/$key.source.tmp" "$BASE/state/$key.source" || return 1
    fi
    IFS=$'\t' read -r url size hash minutes height _ < "$BASE/state/$key.source"
}

transfer() {
    local key=$1 title=$2 year=$3 url=$4 size=$5 hash=$6 provider=$7 genre=$8 source_height=$9
    local part="$VIDEO_ROOT/.pivault-incoming/${key}.${provider}.part"
    local folder="$VIDEO_ROOT/$title ($year)" final="$VIDEO_ROOT/$title ($year)/$title ($year).mp4"
    local have=0 disk used remaining free summary rights_bytes
    local dossier="$BASE/state/$key.$provider.rights"
    if [[ -f "$final" ]]; then
        die "Existing target left untouched: $final"
        return 1
    fi
    python3 "$BASE/cinema-notifications.py" check --root "$NOTIFICATION_ROOT" \
        --flag "$BASE/state/$key.flag.json" --key "$key" --genre "$genre" \
        --title "$title" --year "$year" --url "$url" --md5 "$hash" --bytes "$size" \
        --minutes "$minutes" --height "$source_height" --tolerance "$TOLERANCE_PERCENT"
    local preflight_status=$?
    (( preflight_status == 0 )) || return "$preflight_status"
    prepare_rights "$key" "$provider" "$url" "$size" "$hash" "$dossier" || { die "Cannot preserve rights evidence for $title; download stopped."; return 1; }
    rights_bytes=$(du -sb -- "$dossier" | awk '{print $1}') || return 1
    [[ ! -f "$part" ]] || have=$(stat -c %s -- "$part")
    if (( have > size )); then
        die "Partial file is larger than the published source: $part. It was left untouched."
        return 1
    fi
    read -r disk used free < <(df -B1 --output=size,used,avail -- "$VIDEO_ROOT" | awk 'NR==2') || return 1
    remaining=$((size-have+rights_bytes+8192))
    summary=$(awk -v n="$size" -v m="$minutes" 'BEGIN {printf "%.2f GiB; reference runtime %s min; approx %.2f Mbps",n/1073741824,m,n*8/(m*60*1000000)}')
    log "Preflight $title ($year), $provider: $summary."
    if (( (used + remaining) * 100 > disk * DISK_LIMIT_PERCENT )); then
        log "CAP: $title ($year) would fill $(df --output=target -- "$VIDEO_ROOT" | awk 'NR==2') past $DISK_LIMIT_PERCENT%."
        return 3
    fi
    if (( free < remaining )); then
        log "SPACE: insufficient free disk space for $title ($year)."
        return 3
    fi
    if (( have < size )); then
        log "Downloading $title ($year) from $provider."
        curl --fail --location --show-error --proto '=https' --proto-redir '=https' \
            --connect-timeout 30 --speed-limit 1024 --speed-time 180 \
            --retry 3 --retry-delay 10 --max-filesize "$size" --continue-at - \
            --output "$part" "$url" || return 1
    fi
    if ! valid_file "$part" "$size" "$hash"; then
        die "Verification failed; partial retained for inspection: $part"
        return 1
    fi
    mkdir -p -- "$folder/rights" || return 1
    cp -a -- "$dossier/." "$folder/rights/" || return 1
    mv -n -- "$part" "$final" || return 1
    [[ ! -e "$part" ]] || { die "Target appeared during download; left untouched: $final"; return 1; }
    printf '%s\t%s\t%s\t%s\t%s\n' "$url" "$size" "$hash" "$RUN_DAY" "$genre" > "$BASE/state/$key.done.tmp" || return 1
    mv -- "$BASE/state/$key.done.tmp" "$BASE/state/$key.done" || return 1
    log "Verified: $title ($year)."
    return 0
}

run_round() {
    local mode=$1 genre key title year url size hash loc reason rights minutes height result status
    local failures=0 new=0 already=0 exhausted=0 flags=0
    [[ -f "$BASE/cinema.conf" ]] || { die 'Install this script first.'; return 1; }
    # This file is generated locally by install; it sets TOLERANCE_PERCENT.
    source "$BASE/cinema.conf" || return 1
    host_check || return 1
    mkdir -p -- "$BASE/state" "$VIDEO_ROOT/.pivault-incoming" || return 1
    exec 9>"$BASE/state/run.lock" || return 1
    flock -n 9 || { die 'A cinema round is already running.'; return 75; }
    touch "$BASE/state/completed.tsv" || return 1
    RUN_DAY=$(date +%F)
    declare -A chosen=() all_genres=() pending_genres=()
    log "Starting $mode: one new movie per genre for $RUN_DAY."
    while IFS=$'\t' read -r genre key title year url size hash loc reason rights minutes height; do
        all_genres[$genre]=1
        [[ -z "${chosen[$genre]:-}" ]] || continue
        if (( $(daily_count "$genre") >= 1 )); then
            chosen[$genre]=1
            already=$((already+1))
            log "$genre: today's quota is already complete."
            continue
        fi
        if [[ -f "$BASE/state/$key.done" && -s "$VIDEO_ROOT/$title ($year)/$title ($year).mp4" ]]; then
            continue
        fi
        python3 "$BASE/cinema-notifications.py" pending --root "$NOTIFICATION_ROOT" --flag "$BASE/state/$key.flag.json"
        status=$?
        if (( status == 5 )); then continue; fi
        if (( status == 4 )); then pending_genres[$genre]=1; continue; fi
        if (( status != 0 )); then
            chosen[$genre]=1
            failures=$((failures+1))
            log "$genre: its review state could not be read; left untouched."
            continue
        fi
        if [[ "$url" == - ]] && ! find_source "$key" "$title" "$year" "$minutes" "$rights"; then
            log "No safe archive.org copy found yet for $title ($year); trying the next film."
            continue
        fi
        # A previously downloaded copy at the exact final path is never replaced.
        if [[ -f "$VIDEO_ROOT/$title ($year)/$title ($year).mp4" ]]; then
            if valid_file "$VIDEO_ROOT/$title ($year)/$title ($year).mp4" "$size" "$hash"; then
                printf '%s\t%s\t%s\t%s\t%s\n' "$url" "$size" "$hash" existing "$genre" > "$BASE/state/$key.done.tmp" || return 1
                mv -- "$BASE/state/$key.done.tmp" "$BASE/state/$key.done" || return 1
                log "Already present: $title ($year); no download needed."
                continue
            fi
            chosen[$genre]=1
            failures=$((failures+1))
            log "Existing movie differs from queue transfer; left untouched: $title ($year)."
            continue
        fi
        chosen[$genre]=1
        result=1
        LOC_URL= LOC_SIZE=
        if loc_source "$loc" "$BASE/state/$key.loc.json"; then
            transfer "$key" "$title" "$year" "$LOC_URL" "$LOC_SIZE" - loc "$genre" "$LOC_HEIGHT"
            result=$?
        elif [[ "$loc" != - ]]; then
            log "LoC download unavailable for $title; using its pinned archive transfer."
        fi
        if (( result != 0 && result != 4 && result != 5 )); then
            transfer "$key" "$title" "$year" "$url" "$size" "$hash" archive "$genre" "$height"
            result=$?
        fi
        if (( result == 4 )); then
            flags=$((flags+1))
            pending_genres[$genre]=1
            unset 'chosen[$genre]'
            continue
        fi
        if (( result == 5 )); then unset 'chosen[$genre]'; continue; fi
        if (( result == 0 )); then
            printf '%s\t%s\t%s\n' "$RUN_DAY" "$genre" "$key" >> "$BASE/state/completed.tsv" || return 1
            new=$((new+1))
        else
            failures=$((failures+1))
            log "$genre: incomplete; its next attempt will resume the same film."
        fi
    done < <(queue)
    for genre in "${!all_genres[@]}"; do
        if [[ -z "${chosen[$genre]:-}" ]]; then
            if [[ -n "${pending_genres[$genre]:-}" ]]; then
                log "$genre: all remaining candidates require admin review."
                [[ "$mode" != test ]] || failures=$((failures+1))
            else
                log "$genre: queue complete."
                exhausted=$((exhausted+1))
            fi
        fi
    done
    log "Round result: $new new; $already genres already satisfied today; $flags new flags; $exhausted exhausted; $failures incomplete."
    (( failures == 0 ))
}

install_cinema() {
    local service timer
    host_check || return 1
    [[ -f "$BASE/cinema-rights.py" && -f "$BASE/rights-evidence/catalogue.json" ]] || { die 'Keep the rights helper and rights-evidence folder beside the installer.'; return 1; }
    mkdir -p -- "$INSTALL_DIR/state" "$INSTALL_DIR/rights-evidence" "$VIDEO_ROOT" || return 1
    if [[ "$BASE/PiVault-Cinema.sh" != "$INSTALL_DIR/PiVault-Cinema.sh" ]]; then
        cp -- "${BASH_SOURCE[0]}" "$INSTALL_DIR/PiVault-Cinema.sh" || return 1
        cp -- "$BASE/cinema-notifications.py" "$INSTALL_DIR/cinema-notifications.py" || return 1
        cp -- "$BASE/cinema-rights.py" "$INSTALL_DIR/cinema-rights.py" || return 1
        cp -- "$BASE/cinema-resolve.py" "$INSTALL_DIR/cinema-resolve.py" || return 1
        cp -a -- "$BASE/rights-evidence/." "$INSTALL_DIR/rights-evidence/" || return 1
    fi
    printf 'TOLERANCE_PERCENT=75\n' > "$INSTALL_DIR/cinema.conf" || return 1
    sudo install -d -o snx -g "$(id -gn)" -m 0750 "$NOTIFICATION_ROOT" "$NOTIFICATION_ROOT/inbox" "$NOTIFICATION_ROOT/decisions" || return 1
    chmod 700 "$INSTALL_DIR/PiVault-Cinema.sh" || return 1
    service=$(cat <<'UNIT'
[Unit]
Description=PiVault Americana cinema downloads
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=snx
WorkingDirectory=/home/snx/pi-services/cinema
Environment=HOME=/home/snx
ExecStart=/usr/bin/bash /home/snx/pi-services/cinema/PiVault-Cinema.sh run
TimeoutStartSec=infinity
UNIT
    )
    timer=$(cat <<'UNIT'
[Unit]
Description=PiVault cinema daily at 02:00 America/New_York

[Timer]
OnCalendar=*-*-* 02:00:00 America/New_York
AccuracySec=1s
Persistent=false
Unit=pivault-cinema.service

[Install]
WantedBy=timers.target
UNIT
    )
    printf '%s\n' "$service" | sudo tee /etc/systemd/system/pivault-cinema.service >/dev/null || return 1
    printf '%s\n' "$timer" | sudo tee /etc/systemd/system/pivault-cinema.timer >/dev/null || return 1
    sudo systemctl daemon-reload || return 1
    sudo systemctl disable --now pivault-cinema.timer || return 1
    preview
    log "Installed; downloads stop at $DISK_LIMIT_PERCENT% of the video drive. Starting the real first-round test (one film per genre)."
    if ! bash "$INSTALL_DIR/PiVault-Cinema.sh" test; then
        die 'First-round test is incomplete. Nightly scheduling remains disabled. Fix the reported problem, then rerun install to resume.'
        return 1
    fi
    sudo systemctl enable --now pivault-cinema.timer || return 1
    log 'First-round test passed. Daily downloads are enabled for 02:00 America/New_York.'
    systemctl list-timers --all pivault-cinema.timer --no-pager
}

# The curated title queue is appended below. Pinned rows name an exact transfer; rows whose
# URL is "-" are looked up once on archive.org by cinema-resolve.py, then kept fixed.

queue() {
cat <<'PIVAULT_QUEUE'
action-adventure	the-mark-of-zorro-1920	The Mark of Zorro	1920	https://archive.org/download/vidzo/The-Mark-of-Zorro-v2.mp4	964602910	0440853e177e1fb815e0b61d7cb13575	-	Douglas Fairbanks helped establish the masked swashbuckler as a Hollywood archetype. Archive transfer: 106.8 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	106.8	480
action-adventure	robin-hood-1922	Robin Hood	1922	https://archive.org/download/FairbanksRobinHood1922/FairbanksRobinHood1922/FairbanksRobinHood1922_512kb.mp4	578762091	22ddba6f0ce77e5831d81d6def41b146	-	A lavish Fairbanks spectacle representing the scale and ambition of silent Hollywood. Archive transfer: 132.8 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	132.8	240
action-adventure	the-three-musketeers-1921	The Three Musketeers	1921	https://archive.org/download/The_Three_Musketeers/TheThreeMusketeers.mp4	668958619	f017b812382b62111f804c19381310b5	-	Fairbanks athleticism and elaborate swordplay helped define the American swashbuckler. Archive transfer: 127.9 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791; transfer additionally marked http://creativecommons.org/licenses/by/3.0/	127.9	480
comedy	his-girl-friday-1940	His Girl Friday	1940	https://archive.org/download/his_girl_friday/his_girl_friday.mp4	575274912	8527a3c6c69520b655fbc6e10f0c6e3f	-	Rapid overlapping dialogue, newsroom satire and Rosalind Russell's formidable reporter. Archive transfer: 91.7 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	91.7	480
comedy	my-man-godfrey-1936	My Man Godfrey	1936	https://archive.org/download/MyManGodfrey1936/MyManGodfrey1936.mp4	590603785	489b89cf5199939fcd01318990a0323f	-	Screwball comedy turns Depression-era class inequality into social satire. Archive transfer: 94.8 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	94.8	480
comedy	the-general-1926	The General	1926	https://archive.org/download/The_General_Buster_Keaton/The_General.mp4	412400222	6767dec4e7725292556799367c735fed	-	Keaton combines precise physical comedy with large-scale railway action. Archive transfer: 78.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	78.7	480
crime-noir	detour-1945	Detour	1945	https://archive.org/download/Detour/Detour.mp4	423770341	e427ccfe54fed671e9932a66a5e319ef	-	A defining Poverty Row noir: fatalism and an American road trip stripped of romanticism. Archive transfer: 67.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	67.6	480
crime-noir	d-o-a-1950	D.O.A.	1950	https://archive.org/download/D.o.a.VideoQualityUpgrade/D.o.a.VideoQualityUpgrade.mp4	826667813	205d6605fcc97d01e4b481b184c13e47	-	A murdered man investigates his own poisoning; a singular noir premise. Archive transfer: 83.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/publicdomain/mark/1.0/	83.6	480
crime-noir	scarlet-street-1945	Scarlet Street	1945	https://archive.org/download/ScarletStreet/Scarlet_Street.mp4	633398173	467872e847b39d213afb3395cc26d799	-	Fritz Lang's bleak portrait of desire, deception and moral collapse. Archive transfer: 101.4 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	101.4	480
mystery	sherlock-jr-1924	Sherlock Jr.	1924	https://archive.org/download/sherlockjr1924_201909/Sherlock%20Jr.%20-%20Buster%20Keaton%20%281924%29%20HD%20%5B720p%5D.mp4	354283591	777ff524f1597753b7b96839672ff44b	-	Keaton turns detective fiction and the act of watching movies into ingenious visual comedy. Archive transfer: 44.6 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	44.6	720
mystery	the-bat-1926	The Bat	1926	https://archive.org/download/TheBat1926/TheBat1926.mp4	329334657	1069a191f58114741944c6241e2ddbcf	-	A masked criminal and a spooky mansion join the mystery and haunted-house traditions. Archive transfer: 85.6 min, 360p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	85.6	360
mystery	the-bat-whispers-1930	The Bat Whispers	1930	https://archive.org/download/THEBATWHISPERSspecial65mmWidescreenEdition/THE%20BAT%20WHISPERS%20%28special%2065mm%20widescreen%20edition%29.mp4	798268420	8df6b878e999a7a0dbc40df7e741e115	-	An early sound mystery notable for its experimental widescreen production. Archive transfer: 83.9 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	83.9	720
suspense	the-hitch-hiker-1953	The Hitch-Hiker	1953	https://archive.org/download/Hitch_Hiker/Hitch-Hiker.mp4	442543106	2dcd87d4bce70930c83154a4fd6ec091	https://www.loc.gov/item/2023602025/	Ida Lupino's spare independent noir places ordinary men under sustained murderous threat. Archive transfer: 70.8 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	70.8	480
suspense	the-stranger-1946	The Stranger	1946	https://archive.org/download/TheStranger_0/The_Stranger.mp4	570456474	af17ebb566e667fd69272ebeb98715bf	-	Orson Welles brings a Nazi fugitive into an apparently ordinary American town. Archive transfer: 91.2 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	91.2	480
suspense	suddenly-1954	Suddenly	1954	https://archive.org/download/suddenly/suddenly.mp4	472229483	8ad2aa255ce1ca4cb77385fdba144639	-	Frank Sinatra's presidential-assassination thriller captures postwar political anxiety. Archive transfer: 75.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	75.6	480
horror	night-of-the-living-dead-1968	Night of the Living Dead	1968	https://archive.org/download/night_of_the_living_dead_dvd/Night.mp4	596399542	97128daa91e4c4d88e49ab5df3d330d3	-	Romero reshaped the zombie film and independent American horror. Archive transfer: 95.5 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	95.5	480
horror	carnival-of-souls-1962	Carnival of Souls	1962	https://archive.org/download/CarnivalofSouls/CarnivalOfSouls.mp4	518506661	45e9b57d1a6593a888534f964cb0e576	-	An independent regional horror film prized for its uncanny atmosphere and alienation. Archive transfer: 83.0 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	83.0	480
horror	house-on-haunted-hill-1959	House on Haunted Hill	1959	https://archive.org/download/The_House_On_Haunted_Hill/The_House_On_Haunted_Hill.mp4	467201749	da9da5bb4cdbfb81b24e05c310b823cd	-	Vincent Price and William Castle's showmanship represent the theatrical gimmick era. Archive transfer: 74.7 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	74.7	480
science-fiction	20-000-leagues-under-the-sea-1916	20,000 Leagues Under the Sea	1916	https://archive.org/download/20000Leagues_1916/20000_Leagues_Under_the_Sea.mp4	616298162	bf12780e948f5d7b982b55139bc69e58	-	Pioneering underwater photography in an early American science-fiction spectacle. Archive transfer: 98.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	98.7	480
science-fiction	the-lost-world-1925	The Lost World	1925	https://archive.org/download/lost_world/lost_world.mp4	424986300	057ae0ddbeb6f6087db324124efe2d20	-	Willis O'Brien's stop-motion dinosaurs helped establish the screen creature spectacle. Archive transfer: 68.1 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	68.1	480
science-fiction	the-phantom-planet-1961	The Phantom Planet	1961	https://archive.org/download/Phantom_Planet/The_Phantom_Planet.mp4	512855084	8231495a7729cbbc0c86963315076582	-	A representative small-budget Space Age adventure about miniature alien civilization. Archive transfer: 82.1 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	82.1	480
fantasy	the-thief-of-bagdad-1924	The Thief of Bagdad	1924	https://archive.org/download/TheThiefOfBagdad1924/TOB1924-512.mp4	659317752	38ca18c78ed6350dc4b8e489b8297ac5	-	An elaborate Fairbanks fantasy with monumental sets and imaginative visual effects. Archive transfer: 140.4 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	140.4	240
fantasy	the-wizard-of-oz-1925	The Wizard of Oz	1925	https://archive.org/download/TheWizardOfOz1925/LarrySemonsWizardOfOzsilent1925.mp4	294557357	4097381a00ec91c5a60380314fb54067	-	A strange early screen reinvention of Oz, distinct from the later MGM musical. Archive transfer: 85.0 min, 360p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	85.0	360
fantasy	alice-in-wonderland-1915	Alice in Wonderland	1915	https://archive.org/download/AliceinWonderland1915/AliceinWonderland_512kb.mp4	200801038	77f752233da4d2b5888c84af934c8906	-	An early American feature adaptation of literary fantasy using theatrical visual effects. Archive transfer: 52.3 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	52.3	240
romance	sunrise-1927	Sunrise	1927	https://archive.org/download/sunrise-a-song-of-two-humans-f.-w.-murnau-1927/Sunrise%20A%20Song%20of%20Two%20Humans%20-%20F.W.%20Murnau%20%281927%29.mp4	714612001	26b000f062659bcfce98e28b5ee16dac	-	Murnau combines romance, expressive camerawork and a silent-to-sound transitional soundtrack. Archive transfer: 94.3 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	94.3	720
romance	seventh-heaven-1927	Seventh Heaven	1927	https://archive.org/download/BillSpragueCollectionSeventhHeaven/7th%20Heaven%20%281927%29.mp4	602760272	e424c97e0279dedb6a0003f3a34da372	-	A major silent romantic melodrama associated with the first Academy Awards. Archive transfer: 114.0 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	114.0	240
romance	way-down-east-1920	Way Down East	1920	https://archive.org/download/WayDownEast_201312/Way-Down-East-v2.mp4	1343297065	c45648b80f6f4ae156824f716386e2f3	-	Melodrama and a celebrated ice-floe rescue demonstrate silent Hollywood spectacle. Archive transfer: 147.9 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	147.9	480
drama	within-our-gates-1920	Within Our Gates	1920	https://archive.org/download/WithinOurGates/WithinOurGates.mp4	1055405382	e64e15f62299d8799b6f63157bd2c8ef	https://www.loc.gov/item/2024600507/	Oscar Micheaux confronts racism, education and racial violence in an early surviving Black-directed feature. Archive transfer: 77.1 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	77.1	480
drama	salt-of-the-earth-1954	Salt of the Earth	1954	https://archive.org/download/SaltOfTheEarth_735/SALTOFTHEEARTH1954.mp4	261252886	24c75e4c0df577f2f3828d92e22c7f0c	-	A labor film centered on Mexican-American mine workers and women's participation in the strike. Archive transfer: 92.3 min, 240p.	U.S. public-domain film; source record: https://archive.org/details/SaltOfTheEarth_735	92.3	240
drama	broken-blossoms-1919	Broken Blossoms	1919	https://archive.org/download/Broken.Blossoms/Broken_Blossoms.mp4	426837856	3c7d826c7d3c89df7940ce6373ed1897	-	A major silent melodrama whose interracial sympathy coexists with period racial stereotyping. Archive transfer: 88.8 min, 384p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	88.8	384
musicals	st-louis-blues-1929	St. Louis Blues	1929	https://archive.org/download/st-louis-blues-1929/st-louis-blues-1929.mp4	2185098565	05ff105697179c7cc7439d3c063f3e6d	https://www.loc.gov/item/mbrs00063365/	Bessie Smith's sole film appearance preserves an extraordinary blues performance. Archive transfer: 16.0 min, 1080p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	16.0	1080
musicals	black-and-tan-1929	Black and Tan	1929	https://archive.org/download/black-and-tan_1929/black-and-tan_1929.mp4	859046428	a8e82c8d87daeb5457203d08be707783	-	Early sound cinema preserves Duke Ellington and his orchestra in a dramatic jazz short. Archive transfer: 18.2 min, 572p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	18.2	572
musicals	dixiana-1930	Dixiana	1930	https://archive.org/download/Dixiana/Dixiana.mp4	531976151	6a4b33aadd96d2aeb482f2ccea534668	-	An early sound musical with a surviving original two-color Technicolor finale. Archive transfer: 84.9 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	84.9	480
westerns	under-western-stars-1938	Under Western Stars	1938	https://archive.org/download/UnderWesternStars/UnderWesternStars.mp4	404420238	a7dd3c0425d19ea459f9c934c1c7277b	https://www.loc.gov/item/mbrs02023537/	Roy Rogers's first starring feature connects the singing cowboy with Depression-era concerns. Archive transfer: 64.7 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	64.7	480
westerns	the-great-train-robbery-1903	The Great Train Robbery	1903	https://archive.org/download/TheGreatTrainRobbery1903/TheGreatTrainRobbery1903.mp4	62249887	2e2ad33259b2e50ae38e03cfa42ab577	https://www.loc.gov/item/00694220/	An early landmark of American narrative cinema and the screen western. Some surviving copies include original hand tinting. Archive transfer: 10.4 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	10.4	480
westerns	the-great-k-a-train-robbery-1926	The Great K & A Train Robbery	1926	https://archive.org/download/1926-the-great-k-a-train-robbery/1926%20-%20The%20Great%20K%20%26%20A%20Train%20Robbery.mp4	690901890	3396ecc52205bee38c9d03a385f91897	-	Tom Mix's energetic screen cowboy represents the western as popular stunt spectacle. Archive transfer: 63.7 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	63.7	720
war	the-big-parade-1925	The Big Parade	1925	https://archive.org/download/silent-the-big-parade/The%20Big%20Parade.mp4	791022032	59551066d9f3a8156fbaa57e64eac878	-	A major Hollywood portrayal of ordinary soldiers and the cost of the First World War. Archive transfer: 151.3 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	151.3	480
war	the-battle-of-san-pietro-1945	The Battle of San Pietro	1945	https://archive.org/download/battle_of_san_pietro/battle_of_san_pietro_512kb.mp4	138157920	678ebbefacc453d3d4b432a895848e3d	-	John Huston and the U.S. Army confront the human cost of the Italian campaign in a landmark wartime documentary. Archive transfer: 32.2 min, 240p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	32.2	240
war	go-for-broke-1951	Go for Broke!	1951	https://archive.org/download/Go_for_Broke/Go_for_Broke.mp4	562799386	85c3ae0e5c8c5fa03c1839bfa5f0ebdc	-	A Hollywood depiction of Japanese-American soldiers of the 442nd Regimental Combat Team. Archive transfer: 90.3 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	90.3	480
historical-biographical	abraham-lincoln-1930	Abraham Lincoln	1930	https://archive.org/download/abraham_lincoln/abraham_lincoln.mp4	528749539	31244e4d16ca79118eedb65932ac9865	-	An early sound-era presidential biography, useful alongside the historical record it dramatizes. Archive transfer: 84.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	84.7	480
historical-biographical	the-iron-horse-1924	The Iron Horse	1924	https://archive.org/download/the-iron-horse-1924-john-ford/The%20Iron%20Horse%201924%20John%20Ford.mp4	1031196551	6d9cdc9c0bcf18a896a4f71799a24d77	-	John Ford's transcontinental-railroad epic captures Hollywood's mythmaking about national expansion. Archive transfer: 149.7 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	149.7	720
historical-biographical	the-last-of-the-mohicans-1920	The Last of the Mohicans	1920	https://archive.org/download/PublicdomainTheLastoftheMohicans/Last_of_the_Mohicans.mp4	443632281	dbbbb4f3010d7ef75ce1bac6ddc66dd3	-	An influential American literary adaptation and example of frontier historical romance. Archive transfer: 71.1 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	71.1	480
animation	gertie-the-dinosaur-1914	Gertie the Dinosaur	1914	https://archive.org/download/Gertie/GertietheDinosaur_512kb.mp4	53611973	8ff2eb59133cc9dd9c33d8f78dc3f51a	-	Winsor McCay gives a drawn dinosaur personality in a landmark of character animation. Archive transfer: 12.3 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	12.3	240
animation	felix-in-hollywood-1923	Felix in Hollywood	1923	https://archive.org/download/Felix_inHollywood_NoAudio/Felix_inHollywood_NoAudio_512kb.mp4	44435229	a20ce9ef05c60ce3424c4480d8394d05	-	Felix visits the movie industry in an early animated satire of celebrity culture. Archive transfer: 11.5 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	11.5	240
animation	steamboat-willie-1928	Steamboat Willie	1928	https://archive.org/download/steamboat-willie_1928/steamboat-willie_1928.mp4	596809088	88b66b3ffb6f11752c895aa5a573a879	-	An influential early synchronized-sound Mickey Mouse cartoon. Archive transfer: 7.7 min, 1080p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	7.7	1080
documentary	all-my-babies-1953	All My Babies	1953	https://archive.org/download/0891_All_My_Babies_A_Midwifes_Own_Story_15_00_56_00/0891_All_My_Babies_A_Midwifes_Own_Story_15_00_56_00.mp4	336444234	298bbe12ba751f2387cee74d115337a1	https://www.loc.gov/item/2017604960/	George Stoney documents midwife Mary Coley's work and Black maternal care in Georgia. Archive transfer: 54.6 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	54.6	480
documentary	duck-and-cover-1951	Duck and Cover	1951	https://archive.org/download/DuckandC1951/DuckandC1951.mp4	57591076	a5f2942d292783f366f23edd1e2a600d	https://www.loc.gov/item/2022604365/	A central artifact of Cold War civil-defense instruction for American schoolchildren. Archive transfer: 9.2 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	9.2	480
documentary	master-hands-1936	Master Hands	1936	https://archive.org/download/0555_Master_Hands_18_27_28_00/0555_Master_Hands_18_27_28_00.mp4	199125587	5eb0eaca77e38a3853aca6deb3a240cf	https://www.loc.gov/item/2022600183/	A Chevrolet assembly line becomes a mechanical ballet and a corporate vision of American industry. Archive transfer: 31.9 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	31.9	480
experimental	manhatta-1921	Manhatta	1921	https://archive.org/download/Manhatta/manhatta.mp4	132001782	aa93a6d6bec8fed8f1052a9e1a6c697a	-	Charles Sheeler and Paul Strand turn New York into an early American avant-garde city portrait. Archive transfer: 9.7 min, 240p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	9.7	240
experimental	the-life-and-death-of-9413-a-hollywood-extra-1928	The Life and Death of 9413, a Hollywood Extra	1928	https://archive.org/download/the-life-and-death-of-9413-a-hollywood-extra_1928/the-life-and-death-of-9413-a-hollywood-extra_1928.mp4	335732831	57068e83c811395e737ce7a5d57259f1	-	A low-budget expressionist satire of Hollywood's treatment of aspiring performers. Archive transfer: 13.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	13.7	480
experimental	the-fall-of-the-house-of-usher-1928	The Fall of the House of Usher	1928	https://archive.org/download/Fall_of_the_House_of_Usher_1928_Watson/Fall_of_the_House_of_Usher.mp4	79725368	cc5f91c13a1a461601f2b169031107ca	-	James Sibley Watson and Melville Webber adapt Poe through an American avant-garde visual language. Archive transfer: 12.8 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	12.8	480
PIVAULT_QUEUE
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        install) install_cinema ;;
        run) run_round nightly ;;
        test) run_round test ;;
        catalogue|list) catalogue ;;
        preview) preview ;;
        *)
            printf '%s\n' 'Usage:' \
                '  bash PiVault-Cinema.sh catalogue       # Read every selection and its reason.' \
                '  bash PiVault-Cinema.sh preview         # Show the pinned first round, sizes and runtimes.' \
                '  bash PiVault-Cinema.sh install         # On pivault: install, test one per genre, then schedule.' \
                '  bash ~/pi-services/cinema/PiVault-Cinema.sh test  # Resume an incomplete test.'
            ;;
    esac
fi
