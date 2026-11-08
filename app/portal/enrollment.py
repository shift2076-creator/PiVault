"""One server-owned enrollment policy for the browser plan and final submission."""
import uuid
from django.core.exceptions import ValidationError
from .models import User
from .services import ADAPTERS, SERVICES, default_mode, normalize_access


class SetupConfigurationError(ValidationError):
    pass


def first_admin_access():
    vault = ADAPTERS.get('passwords')
    if not vault or 'isolated' not in vault.modes or not getattr(vault, 'requires_browser_bundle', False):
        raise SetupConfigurationError('Password Manager setup is unavailable. The server configuration needs attention.')
    values = {'passwords': 'isolated'}
    for key, _ in SERVICES:
        if key == 'passwords':
            continue
        adapter = ADAPTERS.get(key)
        modes = set(adapter.modes) if adapter else set()
        values[key] = 'shared' if 'shared' in modes else 'isolated' if 'isolated' in modes else 'none'
    return normalize_access(values)


def enrollment_user(invitation, email):
    return User(username=uuid.uuid4().hex, email=email,
                display_name=invitation.name if invitation else 'Admin',
                role=invitation.role if invitation else 'admin',
                service_access=normalize_access(invitation.services) if invitation else first_admin_access())


def invitation_default(key):
    adapter = ADAPTERS.get(key)
    if key == 'passwords' and adapter and 'isolated' in adapter.modes:
        return 'isolated'
    return default_mode(key)


def plan_identity(invitation, user, manifest):
    return {'kind': 'invitation' if invitation else 'first-admin',
            'invitation': invitation.pk if invitation else None,
            'email': user.email, 'name': user.display_name, 'role': user.role,
            'access': user.service_access, 'items': manifest}
