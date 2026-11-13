import hashlib
import hmac
import re
import secrets
import string
import unicodedata
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import Attempt, Invitation
from .services import normalize_access


def code_digest(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()


def issue(name, role, services, issuer=None):
    name = name.strip()
    # Control (Cc) and format (Cf) characters, such as DEL or right-to-left overrides, can hide or disguise a name on screen.
    if not name or len(name) > 80 or any(unicodedata.category(c) in ('Cc', 'Cf') for c in name):
        raise ValidationError('Enter a name of 1–80 characters.')
    if role not in ('admin', 'user'):
        raise ValidationError('Choose Admin or User.')
    services = normalize_access(services)
    if services.get('passwords') == 'isolated' and len(name) > 50:
        raise ValidationError('Password Manager names must be 50 characters or fewer.')
    for _ in range(100):
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        try:
            with transaction.atomic():
                invitation = Invitation.objects.create(
                    digest=code_digest(code), name=name, role=role, services=services,
                    created_by=issuer, expires_at=timezone.now() + timedelta(minutes=30),
                )
            return invitation, code
        except IntegrityError:
            continue
    raise ValidationError('Could not issue a code. Try again.')


def find_valid(code):
    code = code.strip()
    if not re.fullmatch('[A-Za-z0-9]{4}', code, flags=re.ASCII):
        raise ValidationError('Invalid or expired invitation code.')
    invitation = Invitation.objects.filter(
        digest=code_digest(code), used_at=None, expires_at__gt=timezone.now(),
    ).first()
    if not invitation:
        raise ValidationError('Invalid or expired invitation code.')
    return invitation


def rate_limit(request, action):
    # Bound IP is supplied by trusted Caddy, never a client-controlled XFF chain.
    ip = request.META.get('HTTP_X_PIVAULT_CLIENT_IP', request.META.get('REMOTE_ADDR', ''))
    scope = hmac.new(settings.SECRET_KEY.encode(), f'{action}:{ip}'.encode(), hashlib.sha256).hexdigest()
    now = timezone.now()
    cutoff = now - timedelta(minutes=30)
    # Per device only: a shared cap would let one misbehaving device lock everyone out.
    with transaction.atomic():
        Attempt.objects.filter(at__lt=cutoff).delete()
        if Attempt.objects.filter(scope=scope, at__gte=cutoff).count() >= 12:
            return False
        Attempt.objects.create(scope=scope, at=now)
    return True


def attach_shared(code, issuer, sealed):
    """Store the admin's sealed shared logins on an open invitation. The server never receives their key."""
    from .integrations.vaultwarden import enc_string
    from .services import SHARED_LOGIN
    invitation = Invitation.objects.filter(
        digest=code_digest(code), used_at=None, expires_at__gt=timezone.now(), created_by=issuer,
    ).first()
    if not invitation:
        raise ValidationError('That invitation is no longer open. Create a new one.')
    services = invitation.services or {}
    if not any(services.get(key) == 'shared' for key in SHARED_LOGIN):
        raise ValidationError('This invitation has no shared logins to copy.')
    if services.get('passwords') != 'isolated':
        raise ValidationError('Shared logins are copied into the Password Manager, which this invitation does not use.')
    if not isinstance(sealed, str):
        raise ValidationError('The sealed shared logins are invalid.')
    enc_string(sealed, 8192)
    invitation.shared_sealed = sealed
    invitation.save(update_fields=['shared_sealed'])
    return invitation
