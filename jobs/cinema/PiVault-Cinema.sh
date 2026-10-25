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
comedy	his-girl-friday-1940	His Girl Friday	1940	https://archive.org/download/his_girl_friday/his_girl_friday.mp4	575274912	8527a3c6c69520b655fbc6e10f0c6e3f	-	Rapid overlapping dialogue, newsroom satire and Rosalind Russell's formidable reporter. Archive transfer: 91.7 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	91.7	480
comedy	my-man-godfrey-1936	My Man Godfrey	1936	https://archive.org/download/MyManGodfrey1936/MyManGodfrey1936.mp4	590603785	489b89cf5199939fcd01318990a0323f	-	Screwball comedy turns Depression-era class inequality into social satire. Archive transfer: 94.8 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	94.8	480
comedy	the-general-1926	The General	1926	https://archive.org/download/The_General_Buster_Keaton/The_General.mp4	412400222	6767dec4e7725292556799367c735fed	-	Keaton combines precise physical comedy with large-scale railway action. Archive transfer: 78.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	78.7	480
comedy	the-gold-rush-1925	The Gold Rush	1925	-	-	-	-	Chaplin's best-known comedy; the original 1925 silent release, not his later narrated reissue.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	95	-
comedy	safety-last-1923	Safety Last!	1923	-	-	-	-	Harold Lloyd hanging from a clock face, one of the most famous images in American film.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	73	-
comedy	lambchops-1929	Lambchops	1929	-	-	-	-	George Burns and Gracie Allen's first film, preserving their vaudeville act.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	8	-
comedy	pups-is-pups-1930	Pups Is Pups	1930	-	-	-	-	An early sound Our Gang comedy, from the most popular children's series of its era.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	20	-
comedy	the-front-page-1931	The Front Page	1931	-	-	-	-	The template for the fast-talking newspaper comedy, later remade as His Girl Friday.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	101	-
comedy	disorder-in-the-court-1936	Disorder in the Court	1936	-	-	-	-	The Three Stooges at their peak in a courtroom slapstick short.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	17	-
comedy	nothing-sacred-1937	Nothing Sacred	1937	-	-	-	-	A Technicolor screwball satire of tabloid sensationalism starring Carole Lombard.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	77	-
comedy	the-flying-deuces-1939	The Flying Deuces	1939	-	-	-	-	Laurel and Hardy join the French Foreign Legion.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	69	-
comedy	topper-returns-1941	Topper Returns	1941	-	-	-	-	Ghost comedy sequel from Hal Roach with Joan Blondell and Roland Young.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	88	-
comedy	my-favorite-brunette-1947	My Favorite Brunette	1947	-	-	-	-	Bob Hope parodies hard-boiled detective films.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	87	-
comedy	life-with-father-1947	Life with Father	1947	-	-	-	-	Adaptation of Broadway's longest-running non-musical play.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	118	-
comedy	africa-screams-1949	Africa Screams	1949	-	-	-	-	Abbott and Costello's safari farce.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	79	-
comedy	the-inspector-general-1949	The Inspector General	1949	-	-	-	-	Danny Kaye in a musical comedy of mistaken identity after Gogol.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	102	-
comedy	at-war-with-the-army-1950	At War with the Army	1950	-	-	-	-	Dean Martin and Jerry Lewis in their first starring feature.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	93	-
comedy	father-s-little-dividend-1951	Father's Little Dividend	1951	-	-	-	-	Spencer Tracy and Elizabeth Taylor in the sequel to Father of the Bride.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	82	-
comedy	road-to-bali-1952	Road to Bali	1952	-	-	-	-	The only Technicolor Hope-Crosby Road picture.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	91	-
comedy	beat-the-devil-1953	Beat the Devil	1953	-	-	-	-	John Huston and Truman Capote's offbeat Bogart caper.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	89	-
crime-noir	detour-1945	Detour	1945	https://archive.org/download/Detour/Detour.mp4	423770341	e427ccfe54fed671e9932a66a5e319ef	-	A defining Poverty Row noir: fatalism and an American road trip stripped of romanticism. Archive transfer: 67.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	67.6	480
crime-noir	d-o-a-1950	D.O.A.	1950	https://archive.org/download/D.o.a.VideoQualityUpgrade/D.o.a.VideoQualityUpgrade.mp4	826667813	205d6605fcc97d01e4b481b184c13e47	-	A murdered man investigates his own poisoning; a singular noir premise. Archive transfer: 83.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/publicdomain/mark/1.0/	83.6	480
crime-noir	scarlet-street-1945	Scarlet Street	1945	https://archive.org/download/ScarletStreet/Scarlet_Street.mp4	633398173	467872e847b39d213afb3395cc26d799	-	Fritz Lang's bleak portrait of desire, deception and moral collapse. Archive transfer: 101.4 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	101.4	480
crime-noir	too-late-for-tears-1949	Too Late for Tears	1949	-	-	-	-	Lizabeth Scott as one of noir's most ruthless femmes fatales.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	99	-
crime-noir	woman-on-the-run-1950	Woman on the Run	1950	-	-	-	-	San Francisco location noir about a wife hunting her fugitive husband.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	77	-
crime-noir	quicksand-1950	Quicksand	1950	-	-	-	-	Mickey Rooney's turn as a mechanic sinking into crime.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	79	-
crime-noir	kansas-city-confidential-1952	Kansas City Confidential	1952	-	-	-	-	A heist noir whose structure influenced later crime cinema.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	99	-
crime-noir	the-amazing-mr-x-1948	The Amazing Mr. X	1948	-	-	-	-	A spiritualist-swindle noir with John Alton's celebrated photography.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	78	-
mystery	the-bat-whispers-1930	The Bat Whispers	1930	https://archive.org/download/THEBATWHISPERSspecial65mmWidescreenEdition/THE%20BAT%20WHISPERS%20%28special%2065mm%20widescreen%20edition%29.mp4	798268420	8df6b878e999a7a0dbc40df7e741e115	-	An early sound mystery notable for its experimental widescreen production. Archive transfer: 83.9 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	83.9	720
mystery	the-chase-1946	The Chase	1946	-	-	-	-	A dreamlike noir mystery from Cornell Woolrich's novel.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	86	-
mystery	the-red-house-1947	The Red House	1947	-	-	-	-	Edward G. Robinson guards a rural secret in a psychological mystery.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	100	-
suspense	the-hitch-hiker-1953	The Hitch-Hiker	1953	https://archive.org/download/Hitch_Hiker/Hitch-Hiker.mp4	442543106	2dcd87d4bce70930c83154a4fd6ec091	https://www.loc.gov/item/2023602025/	Ida Lupino's spare independent noir places ordinary men under sustained murderous threat. Archive transfer: 70.8 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	70.8	480
suspense	the-stranger-1946	The Stranger	1946	https://archive.org/download/TheStranger_0/The_Stranger.mp4	570456474	af17ebb566e667fd69272ebeb98715bf	-	Orson Welles brings a Nazi fugitive into an apparently ordinary American town. Archive transfer: 91.2 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	91.2	480
suspense	suddenly-1954	Suddenly	1954	https://archive.org/download/suddenly/suddenly.mp4	472229483	8ad2aa255ce1ca4cb77385fdba144639	-	Frank Sinatra's presidential-assassination thriller captures postwar political anxiety. Archive transfer: 75.6 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	75.6	480
suspense	the-strange-love-of-martha-ivers-1946	The Strange Love of Martha Ivers	1946	-	-	-	-	Barbara Stanwyck and Kirk Douglas's debut in a story of buried guilt.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	116	-
suspense	charade-1963	Charade	1963	-	-	-	-	Cary Grant and Audrey Hepburn in the definitive Hitchcock-style thriller not made by Hitchcock.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	113	-
suspense	the-great-flamarion-1945	The Great Flamarion	1945	-	-	-	-	Erich von Stroheim as a sharpshooter destroyed by a manipulative partner.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	78	-
horror	night-of-the-living-dead-1968	Night of the Living Dead	1968	https://archive.org/download/night_of_the_living_dead_dvd/Night.mp4	596399542	97128daa91e4c4d88e49ab5df3d330d3	-	Romero reshaped the zombie film and independent American horror. Archive transfer: 95.5 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	95.5	480
horror	carnival-of-souls-1962	Carnival of Souls	1962	https://archive.org/download/CarnivalofSouls/CarnivalOfSouls.mp4	518506661	45e9b57d1a6593a888534f964cb0e576	-	An independent regional horror film prized for its uncanny atmosphere and alienation. Archive transfer: 83.0 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	83.0	480
horror	house-on-haunted-hill-1959	House on Haunted Hill	1959	https://archive.org/download/The_House_On_Haunted_Hill/The_House_On_Haunted_Hill.mp4	467201749	da9da5bb4cdbfb81b24e05c310b823cd	-	Vincent Price and William Castle's showmanship represent the theatrical gimmick era. Archive transfer: 74.7 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	74.7	480
horror	white-zombie-1932	White Zombie	1932	-	-	-	-	Bela Lugosi in the first feature-length zombie film.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	69	-
horror	the-devil-bat-1940	The Devil Bat	1940	-	-	-	-	A defining Poverty Row horror vehicle for Lugosi.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	68	-
horror	bowery-at-midnight-1942	Bowery at Midnight	1942	-	-	-	-	Lugosi leads a double life in a Monogram horror-crime hybrid.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	61	-
horror	the-bat-1959	The Bat	1959	-	-	-	-	Vincent Price and Agnes Moorehead in the sound version of the classic mystery play.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	80	-
horror	a-bucket-of-blood-1959	A Bucket of Blood	1959	-	-	-	-	Roger Corman's beatnik horror satire.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	66	-
horror	the-little-shop-of-horrors-1960	The Little Shop of Horrors	1960	-	-	-	-	Corman's two-day cult comedy-horror, later a stage and screen musical.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	72	-
horror	dementia-13-1963	Dementia 13	1963	-	-	-	-	Francis Ford Coppola's first mainstream feature.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	75	-
horror	the-terror-1963	The Terror	1963	-	-	-	-	Boris Karloff and a young Jack Nicholson in Corman's gothic thriller.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	81	-
horror	the-last-man-on-earth-1964	The Last Man on Earth	1964	-	-	-	-	Vincent Price in the first screen adaptation of I Am Legend.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	86	-
horror	the-screaming-skull-1958	The Screaming Skull	1958	-	-	-	-	A low-budget haunted-house film with a famous mock-insurance gimmick.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	68	-
science-fiction	the-phantom-planet-1961	The Phantom Planet	1961	https://archive.org/download/Phantom_Planet/The_Phantom_Planet.mp4	512855084	8231495a7729cbbc0c86963315076582	-	A representative small-budget Space Age adventure about miniature alien civilization. Archive transfer: 82.1 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	82.1	480
science-fiction	plan-9-from-outer-space-1959	Plan 9 from Outer Space	1959	-	-	-	-	Ed Wood's film, notorious as the archetype of the so-bad-it's-good movie.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	79	-
science-fiction	teenagers-from-outer-space-1959	Teenagers from Outer Space	1959	-	-	-	-	A homemade Space Age invasion film emblematic of 1950s drive-in science fiction.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	86	-
science-fiction	the-brain-that-wouldn-t-die-1962	The Brain That Wouldn't Die	1962	-	-	-	-	Cult mad-scientist shocker of the early 1960s.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	82	-
science-fiction	indestructible-man-1956	Indestructible Man	1956	-	-	-	-	Lon Chaney Jr. as an executed killer revived by science.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	70	-
romance	sunrise-1927	Sunrise	1927	https://archive.org/download/sunrise-a-song-of-two-humans-f.-w.-murnau-1927/Sunrise%20A%20Song%20of%20Two%20Humans%20-%20F.W.%20Murnau%20%281927%29.mp4	714612001	26b000f062659bcfce98e28b5ee16dac	-	Murnau combines romance, expressive camerawork and a silent-to-sound transitional soundtrack. Archive transfer: 94.3 min, 720p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	94.3	720
romance	morocco-1930	Morocco	1930	-	-	-	-	Marlene Dietrich's American debut, with Gary Cooper and her famous kiss in a tuxedo.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	92	-
romance	bird-of-paradise-1932	Bird of Paradise	1932	-	-	-	-	King Vidor's South Seas romance with an early full orchestral score by Max Steiner.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	80	-
romance	love-affair-1939	Love Affair	1939	-	-	-	-	Irene Dunne and Charles Boyer in the romance later remade as An Affair to Remember.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	88	-
romance	penny-serenade-1941	Penny Serenade	1941	-	-	-	-	Cary Grant's Oscar-nominated performance in an adoption melodrama.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	119	-
romance	algiers-1938	Algiers	1938	-	-	-	-	Charles Boyer and Hedy Lamarr; its Casbah setting inspired Casablanca.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	95	-
romance	the-last-time-i-saw-paris-1954	The Last Time I Saw Paris	1954	-	-	-	-	Elizabeth Taylor in an adaptation of F. Scott Fitzgerald.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	116	-
romance	swing-high-swing-low-1937	Swing High, Swing Low	1937	-	-	-	-	Carole Lombard and Fred MacMurray in a Paramount musical romance.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	92	-
drama	salt-of-the-earth-1954	Salt of the Earth	1954	https://archive.org/download/SaltOfTheEarth_735/SALTOFTHEEARTH1954.mp4	261252886	24c75e4c0df577f2f3828d92e22c7f0c	-	A labor film centered on Mexican-American mine workers and women's participation in the strike. Archive transfer: 92.3 min, 240p.	U.S. public-domain film; source record: https://archive.org/details/SaltOfTheEarth_735	92.3	240
drama	the-birth-of-a-nation-1915	The Birth of a Nation	1915	-	-	-	-	The most consequential and most notorious American silent feature: it set Hollywood's epic grammar while glorifying the Klan and fueling its revival.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	190	-
drama	the-crowd-1928	The Crowd	1928	-	-	-	-	King Vidor's portrait of an anonymous office clerk turned the ordinary American into tragic subject matter.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	98	-
drama	the-emperor-jones-1933	The Emperor Jones	1933	-	-	-	-	Paul Robeson's first leading role in an American sound feature.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	72	-
drama	the-man-with-the-golden-arm-1955	The Man with the Golden Arm	1955	-	-	-	-	Otto Preminger defied the Production Code with a frank story of heroin addiction.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	119	-
drama	of-human-bondage-1934	Of Human Bondage	1934	-	-	-	-	Bette Davis's breakthrough performance from Maugham's novel.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	83	-
drama	a-farewell-to-arms-1932	A Farewell to Arms	1932	-	-	-	-	The first Hemingway screen adaptation, with Gary Cooper and Helen Hayes.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	89	-
drama	rain-1932	Rain	1932	-	-	-	-	Joan Crawford as Sadie Thompson in Maugham's pre-Code drama.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	94	-
drama	a-star-is-born-1937	A Star Is Born	1937	-	-	-	-	The original Technicolor Hollywood tragedy, remade three times.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	111	-
drama	our-town-1940	Our Town	1940	-	-	-	-	Thornton Wilder's Pulitzer play of small-town American life.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	90	-
drama	meet-john-doe-1941	Meet John Doe	1941	-	-	-	-	Frank Capra's warning about media manipulation and populist demagoguery.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	122	-
drama	the-southerner-1945	The Southerner	1945	-	-	-	-	Jean Renoir's Oscar-nominated portrait of Texas tenant farmers.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	92	-
drama	the-outlaw-1943	The Outlaw	1943	-	-	-	-	Howard Hughes's western, a landmark fight with the Production Code.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	116	-
drama	the-jackie-robinson-story-1950	The Jackie Robinson Story	1950	-	-	-	-	Jackie Robinson plays himself in the story of breaking baseball's color line.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	77	-
drama	the-joe-louis-story-1953	The Joe Louis Story	1953	-	-	-	-	A biography of the heavyweight champion and Black American hero.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	88	-
drama	cyrano-de-bergerac-1950	Cyrano de Bergerac	1950	-	-	-	-	José Ferrer's Oscar-winning performance.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	112	-
drama	the-snows-of-kilimanjaro-1952	The Snows of Kilimanjaro	1952	-	-	-	-	Gregory Peck in the Hemingway adaptation.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	114	-
drama	reefer-madness-1936	Reefer Madness	1936	-	-	-	-	An anti-drug scare film later revived as a counterculture cult classic.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	66	-
musicals	st-louis-blues-1929	St. Louis Blues	1929	https://archive.org/download/st-louis-blues-1929/st-louis-blues-1929.mp4	2185098565	05ff105697179c7cc7439d3c063f3e6d	https://www.loc.gov/item/mbrs00063365/	Bessie Smith's sole film appearance preserves an extraordinary blues performance. Archive transfer: 16.0 min, 1080p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	16.0	1080
musicals	black-and-tan-1929	Black and Tan	1929	https://archive.org/download/black-and-tan_1929/black-and-tan_1929.mp4	859046428	a8e82c8d87daeb5457203d08be707783	-	Early sound cinema preserves Duke Ellington and his orchestra in a dramatic jazz short. Archive transfer: 18.2 min, 572p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	18.2	572
musicals	dixiana-1930	Dixiana	1930	https://archive.org/download/Dixiana/Dixiana.mp4	531976151	6a4b33aadd96d2aeb482f2ccea534668	-	An early sound musical with a surviving original two-color Technicolor finale. Archive transfer: 84.9 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	84.9	480
musicals	the-jazz-singer-1927	The Jazz Singer	1927	-	-	-	-	The first feature with synchronized dialogue; it ended the silent era.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	89	-
musicals	hallelujah-1929	Hallelujah!	1929	-	-	-	-	King Vidor's all-Black cast musical drama, one of the first major-studio films of its kind.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	109	-
musicals	applause-1929	Applause	1929	-	-	-	-	Rouben Mamoulian freed the early talkie camera to move.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	80	-
musicals	king-of-jazz-1930	King of Jazz	1930	-	-	-	-	Universal's all-Technicolor revue with Paul Whiteman's orchestra and Bing Crosby's feature debut.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	98	-
musicals	second-chorus-1940	Second Chorus	1940	-	-	-	-	Fred Astaire with Artie Shaw's swing orchestra.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	84	-
musicals	till-the-clouds-roll-by-1946	Till the Clouds Roll By	1946	-	-	-	-	MGM's all-star Jerome Kern biography with Judy Garland and Lena Horne.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	137	-
musicals	royal-wedding-1951	Royal Wedding	1951	-	-	-	-	Fred Astaire's celebrated dance on the walls and ceiling.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	93	-
musicals	rock-rock-rock-1956	Rock, Rock, Rock!	1956	-	-	-	-	Early rock and roll film with Chuck Berry and Alan Freed.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	85	-
musicals	pot-o-gold-1941	Pot o' Gold	1941	-	-	-	-	James Stewart with Horace Heidt's radio band.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	86	-
musicals	the-little-princess-1939	The Little Princess	1939	-	-	-	-	Shirley Temple's first full Technicolor feature.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	93	-
westerns	under-western-stars-1938	Under Western Stars	1938	https://archive.org/download/UnderWesternStars/UnderWesternStars.mp4	404420238	a7dd3c0425d19ea459f9c934c1c7277b	https://www.loc.gov/item/mbrs02023537/	Roy Rogers's first starring feature connects the singing cowboy with Depression-era concerns. Archive transfer: 64.7 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	64.7	480
westerns	the-great-train-robbery-1903	The Great Train Robbery	1903	https://archive.org/download/TheGreatTrainRobbery1903/TheGreatTrainRobbery1903.mp4	62249887	2e2ad33259b2e50ae38e03cfa42ab577	https://www.loc.gov/item/00694220/	An early landmark of American narrative cinema and the screen western. Some surviving copies include original hand tinting. Archive transfer: 10.4 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	10.4	480
westerns	the-big-trail-1930	The Big Trail	1930	-	-	-	-	John Wayne's first starring role in an Oregon Trail epic shot in an early widescreen process.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	-	-
westerns	riders-of-destiny-1933	Riders of Destiny	1933	-	-	-	-	John Wayne as Singin' Sandy in the first Lone Star western.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	53	-
westerns	randy-rides-alone-1934	Randy Rides Alone	1934	-	-	-	-	Yakima Canutt's stunt work in a compact John Wayne B-western.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	53	-
westerns	santa-fe-trail-1940	Santa Fe Trail	1940	-	-	-	-	Errol Flynn and Olivia de Havilland in a frontier epic about John Brown.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	110	-
westerns	angel-and-the-badman-1947	Angel and the Badman	1947	-	-	-	-	John Wayne's first production, an unusually pacifist western.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	100	-
westerns	vengeance-valley-1951	Vengeance Valley	1951	-	-	-	-	Burt Lancaster in an MGM Technicolor ranch drama.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	83	-
westerns	rage-at-dawn-1955	Rage at Dawn	1955	-	-	-	-	Randolph Scott against the Reno brothers' gang.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	87	-
westerns	the-deadly-companions-1961	The Deadly Companions	1961	-	-	-	-	Sam Peckinpah's feature directing debut.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	93	-
westerns	mclintock-1963	McLintock!	1963	-	-	-	-	John Wayne's boisterous comic western.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	127	-
war	the-big-parade-1925	The Big Parade	1925	https://archive.org/download/silent-the-big-parade/The%20Big%20Parade.mp4	791022032	59551066d9f3a8156fbaa57e64eac878	-	A major Hollywood portrayal of ordinary soldiers and the cost of the First World War. Archive transfer: 151.3 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	151.3	480
war	the-battle-of-san-pietro-1945	The Battle of San Pietro	1945	https://archive.org/download/battle_of_san_pietro/battle_of_san_pietro_512kb.mp4	138157920	678ebbefacc453d3d4b432a895848e3d	-	John Huston and the U.S. Army confront the human cost of the Italian campaign in a landmark wartime documentary. Archive transfer: 32.2 min, 240p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	32.2	240
war	go-for-broke-1951	Go for Broke!	1951	https://archive.org/download/Go_for_Broke/Go_for_Broke.mp4	562799386	85c3ae0e5c8c5fa03c1839bfa5f0ebdc	-	A Hollywood depiction of Japanese-American soldiers of the 442nd Regimental Combat Team. Archive transfer: 90.3 min, 480p.	Archive public-domain declaration: http://creativecommons.org/licenses/publicdomain/	90.3	480
war	wings-1927	Wings	1927	-	-	-	-	The first winner of the Academy Award for Best Picture, with unmatched aerial combat.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	144	-
war	all-quiet-on-the-western-front-1930	All Quiet on the Western Front	1930	-	-	-	-	The Best Picture winner whose antiwar message is still taught a century later.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	-	-
war	prelude-to-war-1942	Prelude to War	1942	-	-	-	-	Frank Capra's first Why We Fight film for the US Army, which won the Academy Award for documentary.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	53	-
war	the-nazis-strike-1943	The Nazis Strike	1943	-	-	-	-	Why We Fight, part two: Germany's conquest of Central Europe.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	42	-
war	divide-and-conquer-1943	Divide and Conquer	1943	-	-	-	-	Why We Fight, part three: the fall of Western Europe.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	58	-
war	the-battle-of-britain-1943	The Battle of Britain	1943	-	-	-	-	Why We Fight, part four: Britain alone.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	54	-
war	the-battle-of-russia-1943	The Battle of Russia	1943	-	-	-	-	Why We Fight, part five: the Eastern Front.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	83	-
war	the-battle-of-china-1944	The Battle of China	1944	-	-	-	-	Why We Fight, part six: the war in China.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	65	-
war	war-comes-to-america-1945	War Comes to America	1945	-	-	-	-	Why We Fight, part seven: how America entered the war.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	70	-
war	the-negro-soldier-1944	The Negro Soldier	1944	-	-	-	-	The Army's film for Black soldiers, which changed how the government portrayed African Americans.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	40	-
war	the-memphis-belle-1944	The Memphis Belle	1944	-	-	-	-	William Wyler's color record of a B-17 crew's 25th mission.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	45	-
war	the-battle-of-midway-1942	The Battle of Midway	1942	-	-	-	-	John Ford filmed the battle under fire for the Navy; it won the Academy Award for documentary.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	18	-
war	let-there-be-light-1946	Let There Be Light	1946	-	-	-	-	John Huston's film on psychologically wounded veterans, suppressed by the Army for 35 years.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	58	-
war	blood-on-the-sun-1945	Blood on the Sun	1945	-	-	-	-	James Cagney exposes Japanese imperial plans in 1920s Tokyo.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	94	-
historical-biographical	abraham-lincoln-1930	Abraham Lincoln	1930	https://archive.org/download/abraham_lincoln/abraham_lincoln.mp4	528749539	31244e4d16ca79118eedb65932ac9865	-	An early sound-era presidential biography, useful alongside the historical record it dramatizes. Archive transfer: 84.7 min, 480p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	84.7	480
historical-biographical	becky-sharp-1935	Becky Sharp	1935	-	-	-	-	The first feature made in three-strip Technicolor.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	84	-
action-adventure	captain-kidd-1945	Captain Kidd	1945	-	-	-	-	Charles Laughton as the pirate in a period adventure.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	89	-
animation	steamboat-willie-1928	Steamboat Willie	1928	https://archive.org/download/steamboat-willie_1928/steamboat-willie_1928.mp4	596809088	88b66b3ffb6f11752c895aa5a573a879	-	An influential early synchronized-sound Mickey Mouse cartoon. Archive transfer: 7.7 min, 1080p.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	7.7	1080
animation	popeye-the-sailor-meets-sindbad-the-sailor-1936	Popeye the Sailor Meets Sindbad the Sailor	1936	-	-	-	-	The first two-reel Technicolor Popeye, with the Fleischers' three-dimensional set backgrounds.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	17	-
animation	gulliver-s-travels-1939	Gulliver's Travels	1939	-	-	-	-	Fleischer Studios' answer to Snow White, the second American animated feature.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	76	-
animation	superman-1941	Superman	1941	-	-	-	-	The first Fleischer Superman cartoon, Oscar-nominated and lavishly animated.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	10	-
animation	popeye-the-sailor-meets-ali-baba-s-forty-thieves-1937	Popeye the Sailor Meets Ali Baba's Forty Thieves	1937	-	-	-	-	A Fleischer two-reel Technicolor Popeye special.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	17	-
animation	aladdin-and-his-wonderful-lamp-1939	Aladdin and His Wonderful Lamp	1939	-	-	-	-	The last of the Fleischer Popeye Technicolor specials.	Listed as U.S. public domain: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	22	-
documentary	all-my-babies-1953	All My Babies	1953	https://archive.org/download/0891_All_My_Babies_A_Midwifes_Own_Story_15_00_56_00/0891_All_My_Babies_A_Midwifes_Own_Story_15_00_56_00.mp4	336444234	298bbe12ba751f2387cee74d115337a1	https://www.loc.gov/item/2017604960/	George Stoney documents midwife Mary Coley's work and Black maternal care in Georgia. Archive transfer: 54.6 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	54.6	480
documentary	duck-and-cover-1951	Duck and Cover	1951	https://archive.org/download/DuckandC1951/DuckandC1951.mp4	57591076	a5f2942d292783f366f23edd1e2a600d	https://www.loc.gov/item/2022604365/	A central artifact of Cold War civil-defense instruction for American schoolchildren. Archive transfer: 9.2 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	9.2	480
documentary	master-hands-1936	Master Hands	1936	https://archive.org/download/0555_Master_Hands_18_27_28_00/0555_Master_Hands_18_27_28_00.mp4	199125587	5eb0eaca77e38a3853aca6deb3a240cf	https://www.loc.gov/item/2022600183/	A Chevrolet assembly line becomes a mechanical ballet and a corporate vision of American industry. Archive transfer: 31.9 min, 480p.	LoC public-domain Registry collection: https://www.loc.gov/free-to-use/public-domain-films-from-the-national-film-registry/	31.9	480
documentary	nanook-of-the-north-1922	Nanook of the North	1922	-	-	-	-	Robert Flaherty's Inuit portrait, the founding work of feature documentary.	U.S. publication before 1931: https://ask.loc.gov/film-tv-video/faq/302791	78	-
documentary	the-plow-that-broke-the-plains-1936	The Plow That Broke the Plains	1936	-	-	-	-	Pare Lorentz's government film of the Dust Bowl.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	25	-
documentary	the-river-1938	The River	1938	-	-	-	-	Pare Lorentz's Mississippi River film for the Farm Security Administration.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	31	-
documentary	the-march-1964	The March	1964	-	-	-	-	The US Information Agency's record of the 1963 March on Washington.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	33	-
documentary	nine-from-little-rock-1964	Nine from Little Rock	1964	-	-	-	-	The US Information Agency's Oscar-winning film on the Little Rock Nine.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	19	-
documentary	power-and-the-land-1940	Power and the Land	1940	-	-	-	-	Joris Ivens's Rural Electrification film for the U.S. Film Service.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	38	-
documentary	the-autobiography-of-a-jeep-1943	The Autobiography of a Jeep	1943	-	-	-	-	Office of War Information short narrated by the jeep itself.	Work of the U.S. Government, not subject to copyright: 17 U.S.C. 105	10	-
fantasy	jack-and-the-beanstalk-1952	Jack and the Beanstalk	1952	-	-	-	-	Abbott and Costello's fairy-tale comedy, shifting from sepia to color.	U.S. copyright not renewed: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	78	-
fantasy	march-of-the-wooden-soldiers-1934	March of the Wooden Soldiers	1934	-	-	-	-	Laurel and Hardy's Babes in Toyland, a holiday television perennial.	U.S. copyright notice missing or defective: https://en.wikipedia.org/wiki/List_of_films_in_the_public_domain_in_the_United_States	77	-
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
