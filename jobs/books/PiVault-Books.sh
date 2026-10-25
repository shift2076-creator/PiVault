#!/usr/bin/env bash
# PiVault Books — public-domain books added to the Calibre-Web library a few per night.
# Source order per title: Project Gutenberg EPUB, Library of Congress PDF, Internet Archive PDF.
# Books are added with calibredb; nothing in the library is replaced or removed.

BASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/home/snx/pi-services/books"
BOOK_DISK="/media/snx/C29C87F99C87E66F"
LIBRARY="$BOOK_DISK/Server/Books"
INCOMING="$BOOK_DISK/Server/.pivault-books-incoming"
MAINTENANCE_RUNTIME="/srv/pi-server/users/maintenance.py"
MAINTENANCE_CONFIG="/srv/pi-server/config/users/maintenance.json"
TAG="PiVault Americana"
export TZ=America/New_York

log() { printf '[%s] %s\n' "$(date '+%F %T %Z')" "$*"; }
die() { log "ERROR: $*" >&2; return 1; }

host_check() {
    (( EUID != 0 )) || { die 'Run as snx, not with sudo.'; return 1; }
    mountpoint -q -- "$BOOK_DISK" || { die "$BOOK_DISK is not mounted."; return 1; }
    [[ -f "$LIBRARY/metadata.db" ]] || { die "No Calibre library at $LIBRARY."; return 1; }
    command -v calibredb >/dev/null || { die 'calibredb is not installed on this host.'; return 1; }
}

catalogue() {
    local key title author year source url size md5 fb_url fb_size fb_md5 rights total=0
    printf 'PiVault Americana — %s public-domain books\n\n' "$(queue | wc -l)"
    while IFS=$'\t' read -r key title author year source url size md5 fb_url fb_size fb_md5 rights; do
        printf '%-9s %s (%s) — %s\n' "$source" "$title" "$year" "$author"
        [[ "$size" == - ]] || total=$((total+size))
    done < <(queue)
    awk -v n="$total" 'BEGIN {printf "\nPinned PDF total: %.2f GiB (Gutenberg EPUBs are about 1 MB each)\n",n/1073741824}'
}

valid_file() {
    local file=$1 size=$2 hash=$3 got
    [[ -s "$file" ]] || return 1
    if [[ "$size" != - ]]; then [[ "$(stat -c %s -- "$file")" == "$size" ]] || return 1; fi
    if [[ "$hash" != - ]]; then
        got=$(md5sum -- "$file") || return 1
        [[ "${got%% *}" == "$hash" ]] || return 1
    fi
    case "$file" in
        *.pdf.part) [[ "$(head -c 5 -- "$file")" == %PDF- ]] ;;
        *.epub.part) [[ "$(dd if="$file" bs=1 skip=30 count=28 status=none 2>/dev/null)" == mimetypeapplication/epub+zip ]] ;;
        *) return 1 ;;
    esac
}

fetch() {
    local url=$1 size=$2 hash=$3 part=$4 have=0 progress=(--silent --show-error)
    [[ -t 2 ]] && progress=(--progress-bar)
    if [[ "$size" == - ]]; then
        rm -f -- "$part"
        curl --fail --location "${progress[@]}" --proto '=https' --proto-redir '=https' \
            --connect-timeout 30 --speed-limit 1024 --speed-time 120 --retry 3 --retry-delay 10 \
            --max-filesize 104857600 --output "$part" "$url" || return 1
    else
        [[ ! -f "$part" ]] || have=$(stat -c %s -- "$part")
        (( have <= size )) || rm -f -- "$part"
        if (( have < size )); then
            curl --fail --location "${progress[@]}" --proto '=https' --proto-redir '=https' \
                --connect-timeout 30 --speed-limit 1024 --speed-time 180 --retry 3 --retry-delay 10 \
                --max-filesize "$size" --continue-at - --output "$part" "$url" || return 1
        fi
    fi
    valid_file "$part" "$size" "$hash" || { die "Verification failed; partial retained for inspection: $part"; return 1; }
}

in_library() {
    calibredb search --library-path "$LIBRARY" "identifiers:\"=pivault:$1\"" >/dev/null 2>&1
}

add_book() {
    local file=$1 key=$2 title=$3 author=$4 year=$5 url=$6 rights=$7 output id
    output=$(calibredb add --library-path "$LIBRARY" --title "$title" --authors "$author" \
        --identifier "pivault:$key" --tags "$TAG" --languages eng -- "$file" 2>&1) || { die "$output"; return 1; }
    id=$(printf '%s\n' "$output" | sed -n 's/^Added book ids: \([0-9]*\).*/\1/p')
    [[ "$id" =~ ^[0-9]+$ ]] || { die "Calibre did not add $title; a book with the same title and author may already exist."; return 1; }
    calibredb set_metadata --library-path "$LIBRARY" --field "pubdate:$year-01-01" \
        --field "comments:Source: $url<br>Rights basis: $rights" "$id" >/dev/null || { die "Added $title as book $id, but its date and rights note were not saved."; return 1; }
}

mark_done() {
    printf '%s\t%s\t%s\n' "$2" "$3" "$4" > "$INSTALL_DIR/state/$1.done.tmp" && mv -- "$INSTALL_DIR/state/$1.done.tmp" "$INSTALL_DIR/state/$1.done"
}

run_round() {
    local mode=$1 key title author year source url size md5 fb_url fb_size fb_md5 rights
    local today=0 new=0 failures=0 remaining=0 file day ext part used_url free
    [[ -f "$INSTALL_DIR/books.conf" ]] || { die 'Install this script first.'; return 1; }
    source "$INSTALL_DIR/books.conf" || return 1
    host_check || return 1
    mkdir -p -- "$INSTALL_DIR/state" "$INCOMING" || return 1
    exec 9>"$INSTALL_DIR/state/run.lock" || return 1
    flock -n 9 || { die 'A books round is already running.'; return 75; }
    RUN_DAY=$(date +%F)
    for file in "$INSTALL_DIR"/state/*.done; do
        [[ -f "$file" ]] || continue
        IFS=$'\t' read -r day _ _ < "$file"
        [[ "$day" != "$RUN_DAY" ]] || today=$((today+1))
    done
    log "Starting $mode: up to $DAILY_COUNT new books for $RUN_DAY ($today already added today)."
    while IFS=$'\t' read -r key title author year source url size md5 fb_url fb_size fb_md5 rights; do
        [[ ! -f "$INSTALL_DIR/state/$key.done" ]] || continue
        if (( today >= DAILY_COUNT )); then remaining=$((remaining+1)); continue; fi
        if in_library "$key"; then
            mark_done "$key" existing "$source" "$url" || return 1
            log "Already in the library: $title ($year)."
            continue
        fi
        free=$(df -B1 --output=avail -- "$LIBRARY" | awk 'NR==2 {print $1}') || return 1
        if (( free < 1073741824 + ${size/#-/0} * 2 )); then
            log "SPACE: not enough free space on $BOOK_DISK for $title ($year); round stopped."
            failures=$((failures+1)); break
        fi
        ext=pdf; [[ "$source" != gutenberg ]] || ext=epub
        part="$INCOMING/$key.$source.$ext.part"
        log "Book $((today+1)) of $DAILY_COUNT: $title ($year), $author — from $source."
        used_url=$url
        if ! fetch "$url" "$size" "$md5" "$part"; then
            used_url=
            if [[ "$fb_url" != - ]]; then
                log "$source copy unavailable; using the pinned Internet Archive copy."
                part="$INCOMING/$key.archive.pdf.part"
                fetch "$fb_url" "$fb_size" "$fb_md5" "$part" && used_url=$fb_url
            fi
        fi
        if [[ -z "$used_url" ]]; then
            failures=$((failures+1)); log "Incomplete: $title; the next round will retry it."; continue
        fi
        if ! add_book "$part" "$key" "$title" "$author" "$year" "$used_url" "$rights"; then
            failures=$((failures+1)); continue
        fi
        rm -f -- "$part"
        mark_done "$key" "$RUN_DAY" "$source" "$used_url" || return 1
        today=$((today+1)); new=$((new+1))
        log "Added: $title ($year)."
    done < <(queue)
    log "Round result: $new new; $remaining waiting for later rounds; $failures incomplete."
    (( failures == 0 ))
}

install_books() {
    local daily=${1:-10} service
    [[ "$daily" =~ ^[1-9][0-9]{0,2}$ ]] || { die 'Usage: bash PiVault-Books.sh install [BOOKS_PER_NIGHT]'; return 1; }
    host_check || return 1
    [[ -f "$BASE/maintenance.py" ]] || { die 'Keep the updated maintenance.py beside this installer.'; return 1; }
    mkdir -p -- "$INSTALL_DIR/state" || return 1
    [[ "$BASE/PiVault-Books.sh" == "$INSTALL_DIR/PiVault-Books.sh" ]] || cp -- "${BASH_SOURCE[0]}" "$INSTALL_DIR/PiVault-Books.sh" || return 1
    chmod 700 "$INSTALL_DIR/PiVault-Books.sh" || return 1
    printf 'DAILY_COUNT=%s\n' "$daily" > "$INSTALL_DIR/books.conf" || return 1
    service=$(cat <<'UNIT'
[Unit]
Description=PiVault public-domain book downloads
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=snx
WorkingDirectory=/home/snx/pi-services/books
Environment=HOME=/home/snx
ExecStart=/usr/bin/bash /home/snx/pi-services/books/PiVault-Books.sh run
TimeoutStartSec=infinity
UNIT
    )
    printf '%s\n' "$service" | sudo tee /etc/systemd/system/pivault-books.service >/dev/null || return 1
    sudo systemctl daemon-reload || return 1
    log "Installed with $daily books per night. Starting the first round."
    if ! bash "$INSTALL_DIR/PiVault-Books.sh" test; then
        die 'First round is incomplete. Nightly books stay disabled. Fix the reported problem, then rerun install to resume.'
        return 1
    fi
    sudo install -o root -g root -m 0700 -- "$BASE/maintenance.py" "$MAINTENANCE_RUNTIME" || return 1
    sudo python3 -c 'import json, pathlib, sys
p = pathlib.Path(sys.argv[1]); c = json.loads(p.read_text()) if p.is_file() else {}
c["books_enabled"] = True; p.write_text(json.dumps(c, indent=2) + "\n")' "$MAINTENANCE_CONFIG" || return 1
    log 'First round passed. Remaining books now download in the 02:00 nightly maintenance run.'
}

# The fixed, reviewed title queue is appended below. No runtime book searches.
# key	title	author	year	source	url	bytes	md5	fallback_url	fallback_bytes	fallback_md5	rights

queue() {
cat <<'PIVAULT_QUEUE'
the-grizzly-our-greatest-wild-animal-1909	The grizzly: our greatest wild animal	Enos A. Mills	1909	gutenberg	https://www.gutenberg.org/ebooks/42277.epub3.images	-	-	-	-	-	Project Gutenberg eBook 42277 (https://www.gutenberg.org/ebooks/42277); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
american-big-game-hunting-1893	American big-game hunting	Boone and Crockett Club	1893	gutenberg	https://www.gutenberg.org/ebooks/39965.epub3.images	-	-	-	-	-	Project Gutenberg eBook 39965 (https://www.gutenberg.org/ebooks/39965); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
quaint-and-historic-forts-of-north-america-1915	Quaint and historic forts of North America	John Martin Hammond	1915	gutenberg	https://www.gutenberg.org/ebooks/54746.epub3.images	-	-	-	-	-	Project Gutenberg eBook 54746 (https://www.gutenberg.org/ebooks/54746); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
journal-of-a-trip-to-california-1920	Journal of a trip to California	Charles W. Smith	1920	gutenberg	https://www.gutenberg.org/ebooks/33450.epub3.images	-	-	-	-	-	Project Gutenberg eBook 33450 (https://www.gutenberg.org/ebooks/33450); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
what-i-saw-in-california-1848	What I saw in California	Edwin Bryant	1848	gutenberg	https://www.gutenberg.org/ebooks/13002.epub3.images	-	-	-	-	-	Project Gutenberg eBook 13002 (https://www.gutenberg.org/ebooks/13002); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
in-the-early-days-along-the-overland-trail-in-nebraska-terri-1905	In the early days along the overland trail in Nebraska territory	Gilbert L. Cole	1905	gutenberg	https://www.gutenberg.org/ebooks/31384.epub3.images	-	-	-	-	-	Project Gutenberg eBook 31384 (https://www.gutenberg.org/ebooks/31384); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-santa-fe-trail-and-other-poems-1921	The Santa Fe Trail and other poems	Joseph Robert Wilson	1921	gutenberg	https://www.gutenberg.org/ebooks/42439.epub3.images	-	-	-	-	-	Project Gutenberg eBook 42439 (https://www.gutenberg.org/ebooks/42439); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-emigrant-trail-1910	The emigrant trail	Geraldine Bonner	1910	gutenberg	https://www.gutenberg.org/ebooks/19113.epub3.images	-	-	-	-	-	Project Gutenberg eBook 19113 (https://www.gutenberg.org/ebooks/19113); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
cattle-brands-1906	Cattle brands	Andy Adams	1906	gutenberg	https://www.gutenberg.org/ebooks/12281.epub3.images	-	-	-	-	-	Project Gutenberg eBook 12281 (https://www.gutenberg.org/ebooks/12281); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-outlaws-of-cave-in-rock-1924	The outlaws of Cave-in-Rock	Otto A. Rothert	1924	gutenberg	https://www.gutenberg.org/ebooks/61265.epub3.images	-	-	-	-	-	Project Gutenberg eBook 61265 (https://www.gutenberg.org/ebooks/61265); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
travels-through-the-interior-parts-of-north-america-1778	Travels through the interior parts of North America	Jonathan Carver	1778	gutenberg	https://www.gutenberg.org/ebooks/49753.epub3.images	-	-	-	-	-	Project Gutenberg eBook 49753 (https://www.gutenberg.org/ebooks/49753); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
reminiscences-of-a-canadian-pioneer-for-the-last-fifty-years-1884	Reminiscences of a Canadian pioneer for the last fifty years	Samuel Thompson	1884	gutenberg	https://www.gutenberg.org/ebooks/35586.epub3.images	-	-	-	-	-	Project Gutenberg eBook 35586 (https://www.gutenberg.org/ebooks/35586); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
revolutionary-reader-reminiscences-and-indian-legends-1913	Revolutionary reader; reminiscences and Indian legends	Sophie Lee Foster	1913	gutenberg	https://www.gutenberg.org/ebooks/46400.epub3.images	-	-	-	-	-	Project Gutenberg eBook 46400 (https://www.gutenberg.org/ebooks/46400); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
a-history-of-the-nineteenth-century-year-by-year-1900	A history of the nineteenth century, year by year	Edwin Emerson	1900	gutenberg	https://www.gutenberg.org/ebooks/26901.epub3.images	-	-	-	-	-	Project Gutenberg eBook 26901 (https://www.gutenberg.org/ebooks/26901); U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
camp-fires-in-the-canadian-rockies-1906	Camp-fires in the Canadian Rockies	William T. Hornaday	1906	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/campfiresincanad01horn/campfiresincanad01horn.pdf	131926993	-	-	-	-	Library of Congress https://www.loc.gov/item/06035980/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
narrative-journal-of-travels-through-the-northwestern-region-1821	Narrative journal of travels through the northwestern regions of the United States	Henry Rowe Schoolcraft	1821	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/narrativejournal01scho/narrativejournal01scho_bw.pdf	20156709	-	https://archive.org/download/narrativejournal00scho/narrativejournal00scho.pdf	26040212	6020c11310cb464443f053d5f56b1766	Library of Congress https://www.loc.gov/item/rc01001453/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
french-and-indians-of-illinois-river-1874	French and Indians of Illinois river	N. Matson	1874	loc	https://tile.loc.gov/storage-services/public/gdc/rc01002011/rc01002011.pdf	75458265	-	https://archive.org/download/frenchindiansofi00mats/frenchindiansofi00mats.pdf	9930457	db8d36bd6570ded2e9e59f5c464b7ff2	Library of Congress https://www.loc.gov/item/rc01002011/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
recollections-of-a-long-life-1829-1915-1915	Recollections of a long life, 1829-1915	Isaac Stephenson	1915	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/recollectionsofl01step/recollectionsofl01step_bw.pdf	11886191	-	-	-	-	Library of Congress https://www.loc.gov/item/15026800/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
three-thousand-miles-through-the-rocky-mountains-1869	Three thousand miles through the Rocky Mountains	Alexander K. McClure	1869	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/threethousandmil00mccl/threethousandmil00mccl.pdf	111641074	-	https://archive.org/download/threethousandmil00mcclrich/threethousandmil00mcclrich.pdf	25173558	360bd6605ea0689d6aeed07f252c59d9	Library of Congress https://www.loc.gov/item/rc01001525/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
incidents-of-a-trip-through-the-great-platte-valley-1867	Incidents of a trip through the great Platte Valley	Silas Seymour	1867	loc	https://tile.loc.gov/storage-services/public/gdc/rc01001538/rc01001538.pdf	34279650	-	https://archive.org/download/incidentsoftript00seymrich/incidentsoftript00seymrich.pdf	9726674	608eefc5fbedb45597f4aaf32730216a	Library of Congress https://www.loc.gov/item/rc01001538/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
klondike-a-manual-for-goldseekers-1897	Klondike: a manual for goldseekers	Chicago Record Co.	1897	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/klondikechicagor00phil/klondikechicagor00phil.pdf	158613221	-	https://archive.org/download/klondikechicagor00chic/klondikechicagor00chic.pdf	24091411	59482a1cf8dd3cdd1ea0c1304f01a611	Library of Congress https://www.loc.gov/item/84172033/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
wanderings-in-the-western-land-1879	Wanderings in the western land	A. Pendarves Vivian	1879	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/wanderingsinwest00vivi/wanderingsinwest00vivi_bw.pdf	26786722	-	https://archive.org/download/cihm_25447/cihm_25447.pdf	42736018	81ba5ccc9a5ee873d969ad6f92d527a0	Library of Congress https://www.loc.gov/item/rc01001590/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-opening-of-the-mississippi-1904	The opening of the Mississippi	Frederic Austin Ogg	1904	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/openingofmississ00ogg/openingofmississ00ogg_bw.pdf	32121436	-	https://archive.org/download/openingofmississ00oggfuoft/openingofmississ00oggfuoft.pdf	43356753	fd082bb7b70b11fe1964af00ef235b31	Library of Congress https://www.loc.gov/item/04005423/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
an-impartial-and-correct-history-of-the-war-between-the-unit-1815	An impartial and correct history of the war between the United States of America, and Great Britain	Thomas O'Connor	1815	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/impartialcorrect00oconor/impartialcorrect00oconor.pdf	110623365	-	https://archive.org/download/cihm_29102/cihm_29102.pdf	33283587	96298922c6df48cc9a19388eac59a723	Library of Congress https://www.loc.gov/item/02017599/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
pioneer-hunters-of-the-kankakee-1920	Pioneer hunters of the Kankakee	Jacob Lorenzo Werich	1920	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/pioneerhuntersof01weri/pioneerhuntersof01weri_bw.pdf	11976272	-	https://archive.org/download/pioneerhuntersof00weri/pioneerhuntersof00weri.pdf	7710716	43b6c524a4ae6118f80f1692d925d458	Library of Congress https://www.loc.gov/item/20017670/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
history-of-the-late-war-between-the-united-states-and-great-1816	History of the late war between the United States and Great Britain	H. M. Brackenridge	1816	loc	https://tile.loc.gov/storage-services/public/gdcmassbookdig/historyofwar00brac/historyofwar00brac.pdf	122349732	-	https://archive.org/download/cihm_34609/cihm_34609.pdf	39217981	49b82cbcb1bd14de63f29855c6e2b829	Library of Congress https://www.loc.gov/item/16012284/; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
three-years-in-canada-1829	Three years in Canada	John MacTaggart	1829	archive	https://archive.org/download/cihm_36879/cihm_36879.pdf	31089399	fdc5df9175a7c8f5aef25b85a5f180f3	-	-	-	Internet Archive https://archive.org/details/cihm_36879; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
legends-of-the-ohio-valley-1887	Legends of the Ohio valley	James H. McMechen	1887	archive	https://archive.org/download/legendsofohioval00mcmeuoft/legendsofohioval00mcmeuoft.pdf	4516542	d1af544d9e9e1a7236e9328e5e1f2e90	-	-	-	Internet Archive https://archive.org/details/legendsofohioval00mcmeuoft; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
romance-of-natural-history-1853	Romance of natural history	Philip Henry Gosse	1853	archive	https://archive.org/download/romanceofnatural00gossrich/romanceofnatural00gossrich.pdf	25636361	2a85205ecd60b6cd49f71ef43c6c502b	-	-	-	Internet Archive https://archive.org/details/romanceofnatural00gossrich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
pioneer-life-and-frontier-adventures-1880	Pioneer life and frontier adventures	De Witt C. Peters	1880	archive	https://archive.org/download/pioneerlifefront00pete/pioneerlifefront00pete.pdf	42179549	44dfa94000be6628c73dd12ed97bf417	-	-	-	Internet Archive https://archive.org/details/pioneerlifefront00pete; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
a-summer-in-alaska-1891	A summer in Alaska	Frederick Schwatka	1891	archive	https://archive.org/download/summerinalaskapo00schwuoft/summerinalaskapo00schwuoft.pdf	25875838	c55890ab5cde001bd269263e7a5f1ae1	-	-	-	Internet Archive https://archive.org/details/summerinalaskapo00schwuoft; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
stagecoach-and-tavern-tales-of-the-old-northwest-1930	Stagecoach and tavern tales of the Old Northwest	Harry Ellsworth Cole	1930	archive	https://archive.org/download/stagecoachtavern00cole/stagecoachtavern00cole.pdf	22080192	dd60fd0263f68ac4f32e03c52562d85b	-	-	-	Internet Archive https://archive.org/details/stagecoachtavern00cole; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
sketches-of-eighteenth-century-america-1925	Sketches of eighteenth century America	J. Hector St. John de Crèvecoeur	1925	archive	https://archive.org/download/sketchesofeighte00stjo/sketchesofeighte00stjo.pdf	16335559	6431c3bc2bb7c88cd4860880593126f8	-	-	-	Internet Archive https://archive.org/details/sketchesofeighte00stjo; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-indian-wars-of-pennsylvania-1929	The Indian wars of Pennsylvania	C. Hale Sipe	1929	archive	https://archive.org/download/indianwarsofpenn00sipe/indianwarsofpenn00sipe.pdf	42758193	ab78f6391dac308174d405957ac8c3ef	-	-	-	Internet Archive https://archive.org/details/indianwarsofpenn00sipe; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
a-history-of-the-french-war-ending-in-the-conquest-of-canada-1882	A history of the French war, ending in the conquest of Canada	Rossiter Johnson	1882	archive	https://archive.org/download/historyfrenchwar00johnrich/historyfrenchwar00johnrich.pdf	18486315	bb754f658f212a71c64286dcc9ac0c47	-	-	-	Internet Archive https://archive.org/details/historyfrenchwar00johnrich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-scout-of-pea-ridge-1911	The scout of Pea Ridge	Byron A. Dunn	1911	archive	https://archive.org/download/scoutofpearidge00dunnrich/scoutofpearidge00dunnrich.pdf	14331483	ec854c5b06213efaeb8ef87155ecf623	-	-	-	Internet Archive https://archive.org/details/scoutofpearidge00dunnrich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
california-46-to-88-1888	California '46 to '88	Jacob Wright Harlan	1888	archive	https://archive.org/download/california46to8800harl/california46to8800harl.pdf	9159916	c71311c111d525287d67940eff521d1a	-	-	-	Internet Archive https://archive.org/details/california46to8800harl; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-border-outlaws-1881	The border outlaws	James W. Buel	1881	archive	https://archive.org/download/borderoutlawsaut00buel/borderoutlawsaut00buel.pdf	15353691	058a73887a0b3c92682919520a204c67	-	-	-	Internet Archive https://archive.org/details/borderoutlawsaut00buel; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
the-klondike-a-souvenir-1900	The Klondike: a souvenir	Tappan Adney	1900	archive	https://archive.org/download/klondikestampede00adnerich/klondikestampede00adnerich.pdf	35011524	8870347ede800f5708ecf10b6eef7e2f	-	-	-	Internet Archive https://archive.org/details/klondikestampede00adnerich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
a-sporting-paradise-1904	A sporting paradise	Percy St. Michael Podmore	1904	archive	https://archive.org/download/sportingparadise00podmrich/sportingparadise00podmrich.pdf	15026329	224c58e51ec96aa7d047ec55515ca2d0	-	-	-	Internet Archive https://archive.org/details/sportingparadise00podmrich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
a-history-of-the-missouri-river-1911	A history of the Missouri River	Phil E. Chappell	1911	archive	https://archive.org/download/historyofmissour00chaprich/historyofmissour00chaprich.pdf	7489063	b40500bcbe7cb4c34c989cf6cffa472f	-	-	-	Internet Archive https://archive.org/details/historyofmissour00chaprich; U.S. publication before 1931: https://guides.library.cornell.edu/copyright/publicdomain
PIVAULT_QUEUE
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        install) install_books "${2:-}" ;;
        run) run_round nightly ;;
        test) run_round test ;;
        catalogue|list) catalogue ;;
        *)
            printf '%s\n' 'Usage:' \
                '  bash PiVault-Books.sh catalogue    # List every queued book and its source.' \
                '  bash PiVault-Books.sh install 10   # On pivault: install, add the first 10, then schedule the rest nightly.' \
                '  bash ~/pi-services/books/PiVault-Books.sh test  # Resume an incomplete first round.'
            ;;
    esac
fi
