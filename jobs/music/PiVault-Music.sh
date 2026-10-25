#!/usr/bin/env bash
# PiVault Music — one new curated recording per genre per Pennsylvania calendar day.
# Downloads use curl from Bash. Python 3 only reads archive.org metadata.
# MP3s are verified against the published size and MD5, never re-encoded or removed.

BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/home/snx/pi-services/music"
MUSIC_DISK="/srv/disk1"
MUSIC_ROOT="$MUSIC_DISK/music"
export TZ=America/New_York

log() { printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"; }
die() { log "ERROR: $*" >&2; return 1; }

host_check() {
    (( EUID != 0 )) || { die 'Run as snx, not with sudo.'; return 1; }
    mountpoint -q -- "$MUSIC_DISK" || { die "$MUSIC_DISK is not mounted."; return 1; }
}

catalogue() {
    printf 'PiVault Music — curated public-domain MP3 recordings\n\n'
    while IFS=$'\t' read -r genre key artist title year id pick rights reason; do
        printf '%s | %s — %s (%s) | %s\n  %s\n  Archive: https://archive.org/details/%s\n  Rights basis: %s\n' \
            "$genre" "$artist" "$title" "$year" "$pick" "$reason" "$id" "$rights"
    done < <(queue)
}

daily_count() {
    local file day genre count=0
    for file in "$BASE"/state/*.done; do
        [[ -f "$file" ]] || continue
        IFS=$'\t' read -r day genre < "$file"
        if [[ "$day" == "$RUN_DAY" && "$genre" == "$1" ]]; then count=$((count+1)); fi
    done
    printf '%s\n' "$count"
}

valid_file() {
    local file=$1 expected=$2 hash=$3 got
    [[ -f "$file" ]] || return 1
    [[ "$(stat -c %s -- "$file")" == "$expected" ]] || return 1
    got=$(md5sum -- "$file") || return 1
    [[ "${got%% *}" == "$hash" ]]
}

transfer() {
    local genre=$1 key=$2 artist=$3 title=$4 year=$5 id=$6 pick=$7 rights=$8
    local metadata="$BASE/state/$key.json" incoming="$MUSIC_ROOT/.pivault-incoming/$key"
    local label folder name path size hash part have remaining=0 used free
    local -a files=()
    if [[ "$year" == - ]]; then label=$title; else label="$title ($year)"; fi
    folder="$MUSIC_ROOT/$artist/$label"
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 60 --max-filesize 10485760 \
        --output "$metadata" "https://archive.org/metadata/$id" || { die "Metadata unavailable for $title."; return 1; }
    mapfile -t files < <(python3 "$BASE/music-source.py" select --metadata "$metadata" --pick "$pick") || true
    (( ${#files[@]} > 0 )) || { die "No verifiable MP3 published for $title."; return 1; }
    mkdir -p -- "$incoming" || return 1
    for line in "${files[@]}"; do
        IFS=$'\t' read -r name path size hash <<< "$line"
        part="$incoming/${name##*/}.part"
        have=0
        [[ ! -f "$part" ]] || have=$(stat -c %s -- "$part")
        [[ -f "$folder/${name##*/}" ]] || remaining=$((remaining+size-have))
    done
    used=$(du -sb -- "$MUSIC_ROOT" | awk '{print $1}') || return 1
    free=$(df -B1 --output=avail -- "$MUSIC_ROOT" | awk 'NR==2 {print $1}') || return 1
    log "Preflight $artist — $label: ${#files[@]} file(s), $(awk -v n="$remaining" 'BEGIN {printf "%.1f MiB",n/1048576}') to fetch."
    if (( used + remaining + 65536 > CAP_BYTES )); then
        log "CAP: $label will not fit under the configured Music limit."
        return 3
    fi
    if (( free < remaining + 65536 )); then
        log "SPACE: insufficient free disk space for $label."
        return 3
    fi
    for line in "${files[@]}"; do
        IFS=$'\t' read -r name path size hash <<< "$line"
        part="$incoming/${name##*/}.part"
        if [[ -f "$folder/${name##*/}" ]]; then
            valid_file "$folder/${name##*/}" "$size" "$hash" || { die "Existing file differs; left untouched: $folder/${name##*/}"; return 1; }
            continue
        fi
        have=0
        [[ ! -f "$part" ]] || have=$(stat -c %s -- "$part")
        if (( have > size )); then
            die "Partial file is larger than the published source: $part. It was left untouched."
            return 1
        fi
        if (( have < size )); then
            log "Downloading ${name##*/}."
            curl --fail --location --show-error --proto '=https' --proto-redir '=https' \
                --connect-timeout 30 --speed-limit 1024 --speed-time 180 \
                --retry 3 --retry-delay 10 --max-filesize "$size" --continue-at - \
                --output "$part" "https://archive.org/download/$id/$path" || return 1
        fi
        valid_file "$part" "$size" "$hash" || { die "Verification failed; partial retained for inspection: $part"; return 1; }
    done
    python3 "$BASE/music-source.py" rights --metadata "$metadata" --pick "$pick" --output "$folder/rights" \
        --evidence "$BASE/rights-evidence" --key "$key" --artist "$artist" --title "$title" \
        --year "$year" --archive-id "$id" --rights "$rights" || { die "Cannot preserve rights evidence for $title."; return 1; }
    for line in "${files[@]}"; do
        IFS=$'\t' read -r name path size hash <<< "$line"
        part="$incoming/${name##*/}.part"
        [[ -f "$part" ]] || continue
        mv -n -- "$part" "$folder/${name##*/}" || return 1
        [[ ! -e "$part" ]] || { die "Target appeared during download; left untouched: $folder/${name##*/}"; return 1; }
    done
    rmdir -- "$incoming" 2>/dev/null
    printf '%s\t%s\n' "$RUN_DAY" "$genre" > "$BASE/state/$key.done.tmp" || return 1
    mv -- "$BASE/state/$key.done.tmp" "$BASE/state/$key.done" || return 1
    log "Verified: $artist — $label."
}

run_round() {
    local mode=$1 genre key artist title year id pick rights reason result
    local failures=0 new=0 already=0 exhausted=0
    [[ -f "$BASE/music.conf" ]] || { die 'Install this script first.'; return 1; }
    # This file is generated locally by install, containing only CAP_BYTES.
    source "$BASE/music.conf" || return 1
    host_check || return 1
    mkdir -p -- "$BASE/state" "$MUSIC_ROOT/.pivault-incoming" || return 1
    exec 9>"$BASE/state/run.lock" || return 1
    flock -n 9 || { die 'A music round is already running.'; return 75; }
    RUN_DAY=$(date +%F)
    declare -A chosen=() all_genres=()
    log "Starting $mode: one new recording per genre for $RUN_DAY."
    while IFS=$'\t' read -r genre key artist title year id pick rights reason; do
        all_genres[$genre]=1
        [[ -z "${chosen[$genre]:-}" ]] || continue
        if (( $(daily_count "$genre") >= 1 )); then
            chosen[$genre]=1
            already=$((already+1))
            log "$genre: today's quota is already complete."
            continue
        fi
        [[ ! -f "$BASE/state/$key.done" ]] || continue
        chosen[$genre]=1
        transfer "$genre" "$key" "$artist" "$title" "$year" "$id" "$pick" "$rights"
        result=$?
        if (( result == 0 )); then
            new=$((new+1))
        else
            failures=$((failures+1))
            log "$genre: incomplete; its next attempt will resume the same recording."
        fi
    done < <(queue)
    for genre in "${!all_genres[@]}"; do
        if [[ -z "${chosen[$genre]:-}" ]]; then
            log "$genre: queue complete."
            exhausted=$((exhausted+1))
        fi
    done
    log "Round result: $new new; $already genres already satisfied today; $exhausted exhausted; $failures incomplete."
    (( failures == 0 ))
}

install_music() {
    local cap=${1:-} service timer
    [[ "$cap" =~ ^[1-9][0-9]{0,5}$ ]] || { die 'Usage: bash PiVault-Music.sh install CAP_IN_GiB'; return 1; }
    host_check || return 1
    mkdir -p -- "$INSTALL_DIR/state" "$INSTALL_DIR/rights-evidence" "$MUSIC_ROOT" || return 1
    if [[ "$BASE/PiVault-Music.sh" != "$INSTALL_DIR/PiVault-Music.sh" ]]; then
        cp -- "${BASH_SOURCE[0]}" "$INSTALL_DIR/PiVault-Music.sh" || return 1
        cp -- "$BASE/music-source.py" "$INSTALL_DIR/music-source.py" || return 1
        cp -a -- "$BASE/rights-evidence/." "$INSTALL_DIR/rights-evidence/" || return 1
    fi
    printf 'CAP_BYTES=%s\n' "$((cap*1024*1024*1024))" > "$INSTALL_DIR/music.conf" || return 1
    chmod 700 "$INSTALL_DIR/PiVault-Music.sh" || return 1
    service=$(cat <<'UNIT'
[Unit]
Description=PiVault music downloads
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=snx
WorkingDirectory=/home/snx/pi-services/music
Environment=HOME=/home/snx
ExecStart=/usr/bin/bash /home/snx/pi-services/music/PiVault-Music.sh run
TimeoutStartSec=infinity
UNIT
    )
    timer=$(cat <<'UNIT'
[Unit]
Description=PiVault music daily at 03:00 America/New_York

[Timer]
OnCalendar=*-*-* 03:00:00 America/New_York
AccuracySec=1s
Persistent=false
Unit=pivault-music.service

[Install]
WantedBy=timers.target
UNIT
    )
    printf '%s\n' "$service" | sudo tee /etc/systemd/system/pivault-music.service >/dev/null || return 1
    printf '%s\n' "$timer" | sudo tee /etc/systemd/system/pivault-music.timer >/dev/null || return 1
    sudo systemctl daemon-reload || return 1
    sudo systemctl disable --now pivault-music.timer || return 1
    log "Installed with a $cap GiB Music cap. Starting the real first-round test (one recording per genre)."
    if ! bash "$INSTALL_DIR/PiVault-Music.sh" test; then
        die 'First-round test is incomplete. Nightly scheduling remains disabled. Fix the reported problem, then rerun install with the same cap to resume.'
        return 1
    fi
    sudo systemctl enable --now pivault-music.timer || return 1
    log 'First-round test passed. Daily downloads are enabled for 03:00 America/New_York.'
    systemctl list-timers --all pivault-music.timer --no-pager
}

# The fixed, reviewed queue is appended below. No runtime music searches.
# Fields: genre, key, artist, title, year, archive.org identifier, track|album, rights basis, reason.

queue() {
cat <<'PIVAULT_QUEUE'
classical	open-goldberg-variations-2012	Kimiko Ishizaka	The Open Goldberg Variations	2012	The_Open_Goldberg_Variations-11823	album	Performer's CC0 1.0 dedication: http://creativecommons.org/publicdomain/zero/1.0/	Bach's complete Goldberg Variations in a crowd-funded studio recording released to the public domain.
classical	caprice-viennois-1910	Fritz Kreisler	Caprice Viennois	1910	78_caprice-viennois_fritz-kreisler-george-falkenstein-kreisler_gbia0458946a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Kreisler plays his own Viennese miniature on Victrola 74197.
classical	humoresque-1919	Fritz Kreisler	Humoresque	1919	78_humoresque_fritz-kreisler-dvok-josef-pasternack_gbia0478156a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Dvorak's best-known piano piece recast as a violin showpiece on Victrola 74180.
opera	vesti-la-giubba-1907	Enrico Caruso	Vesti la giubba	1907	78_pagliacci-vesti-la-giubba-on-with-the-play_enrico-caruso-ruggiero-leoncavallo_gbia7040707a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Caruso's signature aria from Pagliacci on Victrola 88061.
opera	celeste-aida-1911	Enrico Caruso	Celeste Aida	1911	78_celeste-aida-heavenly-aida_enrico-caruso-giuseppi-verdi_gbia0105063a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Verdi's opening tenor aria from Aida on Victrola 88127.
opera	una-furtiva-lagrima-1911	Enrico Caruso	Una furtiva lagrima	1911	78_elisir-damore-una-furtiva-lagrima_enrico-caruso-victor-orchestra-donizetti_gbia0105026a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Donizetti's L'elisir d'amore romanza with the Victor Orchestra on Victrola 88339.
jazz	livery-stable-blues-1917	Original Dixieland Jass Band	Livery Stable Blues	1917	original-dixieland-jass-band-livery-stable-blues-victor-18255-b	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	A side of Victor 18255, the first commercially issued jazz record.
jazz	mabels-dream-1923	King Oliver's Creole Jazz Band	Mabel's Dream	1923	78_mabels-dream_king-olivers-creole-jazz-band-ike-smith-joe-oliver-louis-armstrong-j_gbia0039203a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	New Orleans ensemble jazz with the young Louis Armstrong on second cornet; Paramount 20292.
jazz	working-man-blues-1923	King Oliver's Creole Jazz Band	Working Man Blues	1923	78_working-man-blues_king-olivers-creole-jazz-band_gbia0366412b	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	A further 1923 Creole Jazz Band side from the band's landmark recording year.
blues	crazy-blues-1920	Mamie Smith & Her Jazz Hounds	Crazy Blues	1920	MamieSmithHerJazzHounds	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	The 1920 hit that proved the market for blues records by Black artists; OKeh 4169.
blues	you-cant-keep-a-good-man-down-1921	Mamie Smith's Jazz Hounds	You Can't Keep a Good Man Down	1921	78_you-cant-keep-a-good-man-down_mamie-smiths-jazz-hounds-perry-bradford_gbia0396025a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Perry Bradford's song from the first wave of recorded blues; OKeh 4305.
blues	down-home-blues-1921	Mamie Smith and Her Jazz Band	Down Home Blues	1921	78_down-home-blues_mamie-smith-and-her-jazz-band-tom-delaney_gbia0295930a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Tom Delaney's blues standard as recorded in 1921; OKeh 4446.
old-time	arkansaw-traveler-1922	Henry C. Gilliland & A. C. (Eck) Robertson	Arkansaw Traveler	1922	Gilliland_and_Robertson-Arkansaw_Traveler	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Twin fiddles from one of the earliest commercial country recordings; Victor 18956.
old-time	keep-my-skillet-good-and-greasy-1924	Uncle Dave Macon	Keep My Skillet Good and Greasy	1924	78_keep-my-skillet-good-and-greasy_uncle-dave-macon-macon_gbia0237943a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	Banjo and vaudeville showmanship from the first Grand Ole Opry star; Vocalion 14848.
old-time	wreck-of-the-southern-old-97-1925	Vernon Dalhart	The Wreck of the Southern Old '97	1925	78_the-wreck-of-the-southern-old-97_vernon-dalhart_gbia0371194a	track	U.S. sound recording published before 1926: https://www.copyright.gov/music-modernization/pre1972-soundrecordings/	The railroad ballad that became country music's first million-selling song; Herwin 75503.
band	semper-fidelis	United States Marine Band	Semper Fidelis	-	SemperFidelisInaugurationMusic	track	U.S. federal government work (Marine Band); archive public-domain declaration: https://archive.org/details/SemperFidelisInaugurationMusic	Sousa's official march of the U.S. Marine Corps, played by the band he once led.
band	stars-and-stripes-forever	United States Marine Band	The Stars and Stripes Forever	-	StarsAndStripesForever	track	U.S. federal government work (Marine Band); archive public-domain declaration: https://archive.org/details/StarsAndStripesForever	Sousa's 1896 march, the national march of the United States.
band	king-cotton	United States Marine Band	King Cotton	-	KingCotton_450	track	U.S. federal government work (Marine Band); archive public-domain declaration: https://archive.org/details/KingCotton_450	Sousa's 1895 march written for the Cotton States and International Exposition.
PIVAULT_QUEUE
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        install) install_music "${2:-}" ;;
        run) run_round nightly ;;
        test) run_round test ;;
        catalogue|list) catalogue ;;
        *)
            printf '%s\n' 'Usage:' \
                '  bash PiVault-Music.sh catalogue      # Read every selection and its rights basis.' \
                '  bash PiVault-Music.sh install 46     # On pivault: install, test one per genre, then schedule.' \
                '  bash ~/pi-services/music/PiVault-Music.sh test  # Resume an incomplete test.'
            ;;
    esac
fi
