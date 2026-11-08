"""Provision only configured native integrations, with compensating cleanup."""
import logging
from django.core.exceptions import ValidationError
from .setup_logging import event

SERVICES = (
    ('books', 'Books'), ('games', 'Games'), ('music', 'Music'), ('videos', 'Videos'),
    ('documents', 'Documents'), ('wiki', 'Offline Wiki'),
    ('passwords', 'Password Manager'), ('photos', 'Photos'), ('contacts', 'Contacts'),
    ('radicale', 'Radicale'),
)
ADAPTERS = {}
# Tools with no per-user account. Access is either shared or none; there is no account to create.
OPEN_SERVICES = ('books', 'games', 'documents', 'wiki', 'photos', 'contacts', 'radicale')
# Mirrors the launcher's services.yaml on pivault: group and tile description for each row on Add user.
CATEGORY_ORDER = ('Entertainment', 'Knowledge', 'Home Tools', 'Tools')
SERVICE_DETAILS = {
    'books': ('Entertainment', 'Calibre-Web'), 'games': ('Entertainment', 'RetroAssembly'),
    'music': ('Entertainment', 'Navidrome'), 'videos': ('Entertainment', 'Jellyfin'),
    'documents': ('Knowledge', 'Paperless-ngx'), 'wiki': ('Knowledge', 'Kiwix'),
    'photos': ('Home Tools', 'Lychee'), 'contacts': ('Home Tools', 'Contacts'),
    'radicale': ('Home Tools', 'CalDAV and CardDAV'),
    'passwords': ('Tools', 'Vaultwarden'),
}


def is_open(key):
    return key in OPEN_SERVICES and key not in ADAPTERS


def normalize_access(values):
    if not isinstance(values, dict):
        raise ValidationError('Invalid service access.')
    if set(values) - {key for key, _ in SERVICES}:
        raise ValidationError('Unknown service.')
    result = {}
    for key, label in SERVICES:
        mode = values.get(key, 'shared' if is_open(key) else 'none')
        if mode not in ('shared', 'isolated', 'none'):
            raise ValidationError('Invalid service access.')
        if is_open(key):
            if mode not in ('shared', 'none'):
                raise ValidationError(f'{label}: {mode} is not available.')
            result[key] = mode
            continue
        adapter = ADAPTERS.get(key)
        if mode != 'none' and (not adapter or mode not in adapter.modes):
            raise ValidationError(f'{label}: {mode} integration is not configured.')
        result[key] = mode
    return result


def provision(user, password, vault_bundle=None):
    # Native credentials must exist before their encrypted vault entries are saved.
    created = []
    service = None
    try:
        ordered = [(key, mode) for key, mode in user.service_access.items() if key != 'passwords']
        ordered.append(('passwords', user.service_access.get('passwords', 'none')))
        for key, mode in ordered:
            service = key
            if mode == 'none':
                event('service_skipped', service=key, mode=mode)
                continue
            if is_open(key):
                continue  # Open tools have no account to create.
            event('service_started', service=key, mode=mode)
            adapter = ADAPTERS.get(key)
            if not adapter or mode not in adapter.modes:
                raise ValidationError('A required service integration is unavailable.')
            kwargs = {'user': user, 'password': password, 'mode': mode}
            if getattr(adapter, 'requires_browser_bundle', False):
                kwargs['bundle'] = vault_bundle
            receipt = adapter.provision(**kwargs)
            if receipt is not None:
                created.append(receipt)
            event('service_succeeded', service=key, mode=mode)
    except Exception as error:
        event('service_failed', service=service, error=error, level=logging.ERROR)
        failures = list(getattr(error, 'cleanup_failures', []))
        failures.extend(rollback_accounts(created))
        if failures:
            failure = ValidationError('Account setup failed; an admin must review incomplete service cleanup.')
            failure.cleanup_failures = failures
            raise failure from None
        raise
    return created


def rollback_accounts(created):
    """Compensate when provisioning or the identity transaction fails."""
    failures = []
    for receipt in reversed(created):
        event('cleanup_started', service=receipt.service)
        try:
            receipt.rollback()
            event('cleanup_succeeded', service=receipt.service)
        except Exception as error:
            event('cleanup_failed', service=receipt.service, error=error, level=logging.ERROR)
            failures.append({'service': receipt.service, 'native_id': receipt.native_id})
    return failures
