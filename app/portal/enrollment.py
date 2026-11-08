"""One server-owned enrollment policy for the browser plan and final submission."""
import uuid
from django.core.exceptions import ValidationError
from .models import User
from .services import ADAPTERS, default_mode, normalize_access


class SetupConfigurationError(ValidationError):
    pass


def enrollment_user(invitation, email):
    return User(username=uuid.uuid4().hex, email=email, display_name=invitation.name,
                role=invitation.role, service_access=normalize_access(invitation.services))


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
