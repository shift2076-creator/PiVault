#!/usr/bin/env python3
"""Find the Internet Archive copy of one curated film; never guess past the checks below.

The curated list names the film (title, year, reference runtime). This helper searches
archive.org for that exact film, rejects dubs, colorized or re-edited versions, trailers,
reissues and remakes, and picks the best MP4 whose runtime matches. It prints
URL, bytes, MD5, minutes, height and identifier as one tab-separated line, and saves the
item's metadata and a catalogue record for the rights dossier. Exit 1 means no safe copy.
"""
import argparse
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

SEARCH = 'https://archive.org/advancedsearch.php'
METADATA = 'https://archive.org/metadata/'
DOWNLOAD = 'https://archive.org/download/'
MAX_BYTES = 6 * 1024 ** 3
REJECT = re.compile(
    r'\b(trailers?|clips?|excerpts?|scenes?|samples?|previews?|teasers?|dub|dubbed|doblad[ao]|'
    r'colou?ri[sz]ed|subtitulad[ao]|legendado|sottotitol\w*|espa[nñ]ol|castellano|fran[cç]ais|'
    r'deutsch|italiano|portugu[eê]s|remake|reviews?|reaction|commentary|parody|abridged|'
    r'condensed|highlights|recut|re-?edit\w*|edited|trimmed|shortened|cut\s+version|tribute|mashup|gif|'
    r'reels?|part\s*\d+|featurettes?|blu-?\s?ray|brrip|bdrip|webrip|web-dl|yify|yts|rarbg)\b', re.I)
ENGLISH = {'english', 'eng', 'en', 'en-us', 'silent', 'no linguistic content', 'zxx'}
LENGTH_RANGE = (0.6, 1.6)  # silent projection speeds vary widely between transfers


def words(text):
    text = str(text).lower().replace('&', ' and ')
    return [w for w in re.split(r'[^0-9a-z]+', text) if w]


def core(title):
    tokens = words(title)
    while tokens and tokens[0] in ('the', 'a', 'an'):
        tokens = tokens[1:]
    return tokens


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def years_in(text):
    return {int(y) for y in re.findall(r'\b(18[89]\d|19\d\d|20\d\d)\b', str(text))}


def acceptable(doc, title, year, query_words):
    """Reject anything that is not plainly this film in its original English release."""
    name = ' '.join(str(v) for v in as_list(doc.get('title')))
    ident = str(doc.get('identifier', ''))
    wanted = core(title)
    have = core(name)
    # The film's title must appear whole (uploads often prefix the star or director).
    span = len(wanted)
    if not wanted or len(have) > span + 8 or not any(
            have[i:i + span] == wanted for i in range(len(have) - span + 1)):
        return False
    label = name + ' ' + ident.replace('_', ' ').replace('-', ' ')
    if REJECT.search(label) and not REJECT.search(' '.join(query_words)):
        return False
    for language in as_list(doc.get('language')):
        if str(language).strip().lower() not in ENGLISH:
            return False
    stated = years_in(label)
    if any(abs(y - year) > 1 for y in stated):
        return False  # a different year in the label means a reissue, remake or another film
    dated = years_in(' '.join(str(v) for v in as_list(doc.get('year')) + as_list(doc.get('date'))))
    if dated and all(abs(y - year) > 1 for y in dated):
        return False
    return bool(stated or dated)


def seconds(value):
    try:
        if ':' in str(value):
            total = 0.0
            for part in str(value).split(':'):
                total = total * 60 + float(part)
            return total
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def best_file(identifier, files, minutes):
    """Largest-resolution complete MP4 whose runtime matches the reference."""
    choices = []
    for item in files:
        name = str(item.get('name', ''))
        if not name.lower().endswith('.mp4') or REJECT.search(name.replace('_', ' ')):
            continue
        try:
            size = int(item.get('size', 0))
        except (TypeError, ValueError):
            continue
        md5 = str(item.get('md5', ''))
        if not (0 < size <= MAX_BYTES and re.fullmatch(r'[0-9a-f]{32}', md5)):
            continue
        length = seconds(item.get('length')) / 60
        if minutes and length and not (LENGTH_RANGE[0] * minutes <= length <= LENGTH_RANGE[1] * minutes):
            continue
        if minutes and not length:
            continue  # cannot confirm this is the whole film
        try:
            height = int(float(item.get('height', 0) or 0))
        except (TypeError, ValueError):
            height = 0
        choices.append(((min(height, 1080), size), name, size, md5, length or minutes, height or 480))
    if not choices:
        return None
    _, name, size, md5, length, height = max(choices)
    url = DOWNLOAD + urllib.parse.quote(identifier) + '/' + urllib.parse.quote(name)
    return url, size, md5, round(length, 1), height


def fetch_json(url, timeout=40):
    request = urllib.request.Request(url, headers={'User-Agent': 'PiVault-Cinema/1 (offline archive)'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(16 * 1024 * 1024))


QUERIES = (
    # Exact title within a year of release, then the title next to its year anywhere in the record,
    # then the title alone; short common titles ("The Kid", "It") need the second form.
    'title:("{full}") AND mediatype:movies AND (year:[{lo} TO {hi}] OR date:[{lo}-01-01 TO {hi}-12-31])',
    '"{full}" AND "{year}" AND mediatype:movies',
    'title:("{core}") AND mediatype:movies',
)


def search(title, year, form):
    full = ' '.join(words(title)) or title
    query = QUERIES[form].format(full=full, core=' '.join(core(title)) or full, year=year, lo=year - 1, hi=year + 1)
    params = [('q', query), ('rows', '60'), ('output', 'json'), ('sort[]', 'downloads desc')]
    params += [('fl[]', f) for f in ('identifier', 'title', 'year', 'date', 'language', 'downloads')]
    return fetch_json(SEARCH + '?' + urllib.parse.urlencode(params))['response']['docs']


def resolve(title, year, minutes, fetch=None, searcher=None):
    fetch = fetch or fetch_json
    searcher = searcher or search
    seen = []
    for form in range(len(QUERIES)):
        for doc in searcher(title, year, form):
            if doc.get('identifier') not in [d['identifier'] for d in seen] and acceptable(doc, title, year, words(title)):
                seen.append(doc)
    for doc in seen[:20]:
        identifier = doc['identifier']
        metadata = fetch(METADATA + urllib.parse.quote(identifier))
        meta = metadata.get('metadata', {})
        if not acceptable({**meta, 'identifier': identifier}, title, year, words(title)):
            continue
        chosen = best_file(identifier, metadata.get('files', []), minutes)
        if chosen:
            return identifier, metadata, chosen
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', required=True)
    parser.add_argument('--year', required=True, type=int)
    parser.add_argument('--minutes', default='-')
    parser.add_argument('--key', required=True)
    parser.add_argument('--rights', required=True)
    parser.add_argument('--metadata-out', required=True)
    parser.add_argument('--record-out', required=True)
    args = parser.parse_args()
    minutes = 0.0 if args.minutes == '-' else float(args.minutes)
    found = resolve(args.title, args.year, minutes)
    if not found:
        return 1
    identifier, metadata, (url, size, md5, length, height) = found
    meta = metadata.get('metadata', {})
    record = {'key': args.key, 'title': args.title, 'year': args.year, 'loc': '-',
              'rights': args.rights, 'item': 'https://archive.org/details/' + identifier,
              'license': meta.get('licenseurl') or '-', 'creator': meta.get('creator') or [],
              'archive_id': identifier}
    pathlib.Path(args.metadata_out).write_text(json.dumps(metadata) + '\n')
    pathlib.Path(args.record_out).write_text(json.dumps(record, indent=2) + '\n')
    print('\t'.join(map(str, (url, size, md5, length, height, identifier))))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('Source lookup failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
