#!/usr/bin/env python3
"""Bash cinema filesize preflight and durable PiVault notification outbox."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone


def atomic_json(path, value):
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def decision(root, event_id):
    try:
        result = json.loads((root / 'decisions' / (event_id + '.json')).read_text())
        if result.get('id') == event_id and result.get('action') in ('approve', 'skip'):
            return result['action']
    except (OSError, ValueError):
        pass
    return None


def reference_mbps(height):
    # Total video+audio estimates for archival viewing copies, not raw masters.
    if height <= 288:
        return 0.5
    if height <= 576:
        return 1.2
    if height <= 720:
        return 2.5
    return 5.0


def preflight(args):
    if not (args.bytes > 0 and args.minutes > 0 and args.height > 0 and 0 < args.tolerance < 100):
        raise ValueError('Invalid filesize, full-film runtime, resolution, or tolerance.')
    expected = int(args.minutes * 60 * reference_mbps(args.height) * 1_000_000 / 8)
    low = expected * (100 - args.tolerance) // 100
    high = expected * (100 + args.tolerance) // 100
    if low <= args.bytes <= high:
        return 0
    details = dict(movie_key=args.key, genre=args.genre, film_title=args.title, year=args.year,
                   source_url=args.url, source_md5=args.md5, actual_bytes=args.bytes,
                   reference_minutes=args.minutes, height=args.height,
                   reference_mbps=reference_mbps(args.height), estimated_bytes=expected,
                   minimum_bytes=low, maximum_bytes=high, tolerance_percent=args.tolerance)
    event_id = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
    root = Path(args.root)
    if decision(root, event_id) == 'approve':
        print('Admin approved this exact film/source/filesize; allowing it.')
        return 0
    flag_path = Path(args.flag)
    if decision(root, event_id) == 'skip':
        atomic_json(flag_path, {'id': event_id, 'details': details})
        print('Admin skipped this film; moving to the next candidate.')
        return 5
    message = (f'{args.title} ({args.year}): {args.minutes:g} minutes at {args.height}p. '
               f'Published size {args.bytes / 2**30:.2f} GiB; estimated {expected / 2**30:.2f} GiB; '
               f'allowed {low / 2**30:.2f}–{high / 2**30:.2f} GiB (±{args.tolerance}%).')
    event = dict(version=1, id=event_id, source='cinema', kind='cinema.filesize', level='warning',
                 title=f'Review movie download: {args.title} ({args.year})', message=message,
                 created_at=datetime.now(timezone.utc).isoformat(), details=details)
    # Queue the persistent event before advancing the movie list.
    atomic_json(root / 'inbox' / (event_id + '.json'), event)
    atomic_json(flag_path, {'id': event_id, 'details': details})
    print('FLAGGED: ' + message + ' Queued for admin review; trying the next movie in this genre.')
    return 4


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    pending = sub.add_parser('pending')
    pending.add_argument('--root', required=True)
    pending.add_argument('--flag', required=True)
    check = sub.add_parser('check')
    for name in ('root', 'flag', 'key', 'genre', 'title', 'url', 'md5'):
        check.add_argument('--' + name, required=True)
    for name in ('year', 'bytes', 'height', 'tolerance'):
        check.add_argument('--' + name, required=True, type=int)
    check.add_argument('--minutes', required=True, type=float)
    args = parser.parse_args()
    if args.command == 'pending':
        flag = Path(args.flag)
        if not flag.exists():
            return 0
        value = json.loads(flag.read_text())
        action = decision(Path(args.root), value['id'])
        return 0 if action == 'approve' else (5 if action == 'skip' else 4)
    return preflight(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print('Notification preflight failed: ' + str(error), file=__import__('sys').stderr)
        raise SystemExit(1)
