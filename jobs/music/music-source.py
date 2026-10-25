#!/usr/bin/env python3
"""Read one archive.org metadata response for PiVault Music; never download or invent a grant."""
import argparse
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import sys
import urllib.parse

MP3_FORMAT = 'VBR MP3'


def mp3_files(metadata):
    """Every published VBR MP3 in the item with a usable size and MD5."""
    found = []
    for entry in metadata.get('files', []):
        name, size, md5 = entry.get('name', ''), str(entry.get('size', '')), entry.get('md5', '')
        if entry.get('format') != MP3_FORMAT or not name.lower().endswith('.mp3'):
            continue
        if not size.isdigit() or int(size) <= 0 or not re.fullmatch(r'[0-9a-f]{32}', md5):
            continue
        found.append({'name': name, 'size': int(size), 'md5': md5})
    return found


def select(metadata, pick):
    """'album' takes every MP3 track; 'track' takes the largest, ties broken by name."""
    files = sorted(mp3_files(metadata), key=lambda f: f['name'])
    if pick == 'track' and files:
        files = [max(files, key=lambda f: f['size'])]
    return files


def write_rights(metadata_path, destination, evidence, record, files):
    metadata = json.loads(pathlib.Path(metadata_path).read_text())
    destination = pathlib.Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(metadata_path, destination/'source-metadata.json')
    info = metadata.get('metadata', {})
    license_url = info.get('licenseurl') or None
    if license_url and '/zero/' in license_url:
        shutil.copyfile(pathlib.Path(evidence)/'CC0-1.0.txt', destination/'CC0-1.0.txt')
    creator = info.get('creator') or 'Not supplied; see source-metadata.json.'
    if isinstance(creator, list):
        creator = '; '.join(map(str, creator))
    page = 'https://archive.org/details/'+record['archive_id']
    transfer = {'title': record['title'], 'artist': record['artist'], 'year': record['year'],
                'key': record['key'], 'source_page': page, 'files': files,
                'saved_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'declared_license_url': license_url, 'catalogue_rights_basis': record['rights'],
                'source_metadata_sha256': hashlib.sha256((destination/'source-metadata.json').read_bytes()).hexdigest()}
    (destination/'transfer.json').write_text(json.dumps(transfer, indent=2)+'\n')
    listing = ''.join(f"  {f['name']}  {f['size']} bytes  MD5 {f['md5']}\n" for f in files)
    year = '' if record['year'] == '-' else f" ({record['year']})"
    (destination/'RIGHTS.txt').write_text(
        f"{record['artist']} — {record['title']}{year} — rights and provenance\n\n"
        f"Source page: {page}\nFiles, as published:\n{listing}"
        f"Catalogue rights basis: {record['rights']}\n"
        f"Source license/declaration: {license_url or 'No machine-readable license supplied; see source metadata and catalogue rights basis.'}\n"
        f"Creator(s) as supplied by source: {creator}\n"
        "PiVault changes: none; original downloaded bytes retained.\n\n"
        "Source declarations are preserved as evidence, not a new license granted by PiVault.\n"
        "A public-domain recording may contain compositions or arrangements with separate rights.\n"
        "Retain this complete folder when copying or sharing the recordings.\n")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('select')
    s.add_argument('--metadata', required=True)
    s.add_argument('--pick', choices=('track', 'album'), required=True)
    r = sub.add_parser('rights')
    for name in ('metadata', 'pick', 'output', 'evidence', 'key', 'artist', 'title', 'year', 'archive-id', 'rights'):
        r.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        metadata = json.loads(pathlib.Path(args.metadata).read_text())
    except (OSError, ValueError):
        return 1
    files = select(metadata, args.pick)
    if not files:
        return 1
    if args.command == 'select':
        # name, URL-encoded download path, bytes, MD5
        for f in files:
            print('\t'.join((f['name'], urllib.parse.quote(f['name']), str(f['size']), f['md5'])))
        return 0
    record = {'key': args.key, 'artist': args.artist, 'title': args.title, 'year': args.year,
              'archive_id': args.archive_id, 'rights': args.rights}
    write_rights(args.metadata, args.output, args.evidence, record, files)
    return 0


if __name__ == '__main__':
    sys.exit(main())
