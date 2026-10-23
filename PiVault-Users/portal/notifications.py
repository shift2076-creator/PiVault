"""Shared persistent notifications; filesystem outbox for local service producers."""
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Notification

logger = logging.getLogger(__name__)
IDENTIFIER = re.compile(r'^[a-f0-9]{64}$')
NAME = re.compile(r'^[a-zA-Z0-9_.-]{1,64}$')


def root():
    return Path(settings.PIVAULT_NOTIFICATION_ROOT)


def validate_details(details):
    if not isinstance(details, dict):
        raise ValidationError('Notification details must be an object.')
    if not re.fullmatch(r'[a-z0-9-]{1,150}', str(details.get('movie_key', ''))):
        raise ValidationError('Invalid movie key.')
    parsed = urlsplit(str(details.get('source_url', '')))
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
            parsed.port not in (None, 443) or
            not (host == 'archive.org' or host == 'loc.gov' or host.endswith('.loc.gov'))):
        raise ValidationError('Invalid movie source.')
    if not re.fullmatch(r'(?:[a-f0-9]{32}|-)', str(details.get('source_md5', ''))):
        raise ValidationError('Invalid source checksum.')
    for field in ('actual_bytes', 'estimated_bytes', 'maximum_bytes', 'height'):
        if type(details.get(field)) is not int or details[field] <= 0:
            raise ValidationError('Invalid filesize details.')
    if type(details.get('minimum_bytes')) is not int or details['minimum_bytes'] < 0:
        raise ValidationError('Invalid minimum filesize.')
    if type(details.get('tolerance_percent')) is not int or not 0 < details['tolerance_percent'] < 100:
        raise ValidationError('Invalid tolerance.')
    if not isinstance(details.get('reference_minutes'), (int, float)) or not 0 < details['reference_minutes'] <= 600:
        raise ValidationError('Invalid runtime.')


def ingest():
    """Called after an admin authenticates; retain the event in SQLite before unlinking its outbox file."""
    inbox = root() / 'inbox'
    if not inbox.is_dir():
        return
    for path in sorted(inbox.glob('*.json')):
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                continue
            data = json.loads(path.read_text())
            event_id = data['id']
            if not IDENTIFIER.fullmatch(event_id) or path.name != event_id + '.json' or data.get('version') != 1:
                raise ValidationError('Invalid notification identity.')
            if not NAME.fullmatch(data['source']) or not NAME.fullmatch(data['kind']):
                raise ValidationError('Invalid notification source.')
            if data['level'] not in ('info', 'warning', 'error'):
                raise ValidationError('Invalid notification severity.')
            if not isinstance(data['title'], str) or not 0 < len(data['title']) <= 200:
                raise ValidationError('Invalid notification title.')
            if not isinstance(data['message'], str) or len(data['message']) > 8000:
                raise ValidationError('Invalid notification message.')
            details = data.get('details', {})
            if not isinstance(details, dict):
                raise ValidationError('Invalid notification details.')
            if data['kind'] == 'cinema.filesize':
                if data['source'] != 'cinema':
                    raise ValidationError('Invalid cinema producer.')
                validate_details(details)
                if hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest() != event_id:
                    raise ValidationError('Movie event fingerprint does not match its source.')
            timestamp = parse_datetime(data.get('created_at', ''))
            if timestamp is None or timezone.is_naive(timestamp):
                raise ValidationError('Invalid event timestamp.')
            with transaction.atomic():
                Notification.objects.get_or_create(id=event_id, defaults={
                    'source': data['source'], 'kind': data['kind'], 'level': data['level'],
                    'title': data['title'], 'message': data['message'], 'details': details,
                    'created_at': timestamp,
                })
            # The SQLite transaction has committed; an unlink failure only causes a harmless duplicate import.
            path.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            logger.warning('Could not import notification file %s', path.name)


def notify(source, key, title, message, *, kind='service.notice', level='warning', details=None):
    """Common entry point for any PiVault service. Key identifies an incident, not an entire service."""
    if not NAME.fullmatch(source) or not NAME.fullmatch(kind) or level not in ('info', 'warning', 'error'):
        raise ValidationError('Invalid notification producer or severity.')
    if not 0 < len(title) <= 200 or len(message) > 8000 or not 0 < len(key) <= 256:
        raise ValidationError('Invalid notification content.')
    if kind == 'cinema.filesize':
        raise ValidationError('Cinema review events must include their verified filesystem payload.')
    event_id = hashlib.sha256((source + ':' + kind + ':' + key).encode()).hexdigest()
    value, _ = Notification.objects.get_or_create(id=event_id, defaults={
        'source': source, 'kind': kind, 'level': level, 'title': title, 'message': message,
        'details': details or {}, 'created_at': timezone.now(),
    })
    return value


def publish_decisions():
    """Retry persisted admin decisions if disk delivery was temporarily unavailable."""
    values = Notification.objects.filter(kind='cinema.filesize', status='open', resolution__in=['approve', 'skip'])
    for event in values:
        try:
            validate_details(event.details)
            destination = root() / 'decisions'
            destination.mkdir(mode=0o750, parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.decision-', dir=destination)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'version': 1, 'id': event.id, 'action': event.resolution,
                               'admin_id': event.resolved_by_id, 'at': timezone.now().isoformat()}, stream)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, destination / (event.id + '.json'))
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            Notification.objects.filter(id=event.id, status='open', resolution=event.resolution).update(
                status='resolved', resolved_at=timezone.now())
        except (OSError, ValueError, ValidationError):
            logger.warning('Notification decision is awaiting delivery: %s', event.id)
