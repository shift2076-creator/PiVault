"""Fetch the newest Kiwix ZIM for a name prefix into the Kiwix library folder.

Run on pivault while it has internet. Resumes interrupted downloads, verifies the
published SHA-256, and only then moves the file beside the other ZIMs.

    python3 tools/kiwix_fetch.py gutenberg gutenberg_en_all
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

MIRROR = 'https://download.kiwix.org/zim/'
LIBRARY = Path('/media/snx/C29C87F99C87E66F/Server/Kiwix')
CHUNK = 1 << 20


def newest(section, prefix):
    with urlopen(MIRROR + section + '/', timeout=60) as response:
        listing = response.read().decode('utf-8', 'replace')
    names = set(re.findall(re.escape(prefix) + r'_\d{4}-\d{2}\.zim(?=")', listing))
    if not names:
        raise SystemExit('No ' + prefix + ' ZIM found under ' + section + '/')
    return max(names)


def remote_size(url):
    with urlopen(Request(url, method='HEAD'), timeout=60) as response:
        return int(response.headers['Content-Length'])


def published_sha256(url):
    with urlopen(url + '.sha256', timeout=60) as response:
        return response.read().decode().split()[0].lower()


def download(url, part, size):
    offset = part.stat().st_size if part.exists() else 0
    while offset < size:
        request = Request(url, headers={'Range': 'bytes=%d-' % offset})
        with urlopen(request, timeout=120) as response, open(part, 'ab') as stream:
            if offset and response.status != 206:
                raise SystemExit('Mirror refused to resume; delete ' + str(part) + ' and rerun.')
            while chunk := response.read(CHUNK):
                stream.write(chunk)
                offset += len(chunk)
                if offset % 1_000_000_000 < len(chunk):
                    print('%.0f / %.0f GB' % (offset / 1e9, size / 1e9), flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('section', help='mirror folder, e.g. gutenberg')
    parser.add_argument('prefix', help='file prefix without date, e.g. gutenberg_en_all')
    parser.add_argument('--size-only', action='store_true', help='report size and free space, then stop')
    args = parser.parse_args()

    name = newest(args.section, args.prefix)
    url = MIRROR + args.section + '/' + name
    size = remote_size(url)
    target, part = LIBRARY / name, LIBRARY / (name + '.part')
    have = part.stat().st_size if part.exists() else 0
    free = shutil.disk_usage(LIBRARY).free
    print('%s: %.1f GB; %.1f GB free on the Kiwix drive' % (name, size / 1e9, free / 1e9), flush=True)
    if args.size_only or target.exists():
        print('Already in the library.' if target.exists() else 'Size check only; nothing downloaded.')
        return
    if size - have > free:
        raise SystemExit('Not enough space: need %.1f GB more.' % ((size - have - free) / 1e9))

    download(url, part, size)
    print('Verifying SHA-256...', flush=True)
    if sha256(part) != published_sha256(url):
        raise SystemExit('Checksum mismatch; ' + str(part) + ' kept for inspection.')
    os.replace(part, target)
    subprocess.run(['docker', 'restart', 'pivault-kiwix'], check=True)
    print('Done: ' + str(target))


if __name__ == '__main__':
    main()
