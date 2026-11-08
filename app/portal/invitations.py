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
from .models import Attempt, Invitation, User
from .services import normalize_access


# Per-user private storage an admin account cannot keep private: admins see everything in these apps.
ADMIN_REFUSED = {
    'music': 'Per-user Music folders are not available for admin accounts.',
    'documents': 'Per-user Documents are not available for admin accounts.',
}


def code_digest(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()


def videos_name_taken(name):
    # Jellyfin uses the display name as the login and refuses a duplicate at join time,
    # so a clash is settled here, while the admin can still pick another name.
    wanted = name.casefold()
    for display_name, access in User.objects.values_list('display_name', 'service_access'):
        if display_name.casefold() == wanted and (access or {}).get('videos', 'none') != 'none':
            return True
    pending = Invitation.objects.filter(used_at=None, expires_at__gt=timezone.now()).values_list('name', 'services')
    return any(pending_name.casefold() == wanted and (granted or {}).get('videos', 'none') != 'none'
               for pending_name, granted in pending)


def issue(name, role, services, issuer=None):
    name = name.strip()
    # Control (Cc) and format (Cf) characters, such as DEL or right-to-left overrides, can hide or disguise a name on screen.
    if not name or len(name) > 80 or any(unicodedata.category(c) in ('Cc', 'Cf') for c in name):
        raise ValidationError('Enter a name of 1–80 characters.')
    if role not in ('admin', 'user'):
        raise ValidationError('Choose Admin or User.')
    services = normalize_access(services)
    # Refused now, not at join: the joiner cannot change the admin's role or these choices.
    if role == 'admin':
        for key, message in ADMIN_REFUSED.items():
            if services.get(key) == 'isolated':
                raise ValidationError(message)
    if services.get('passwords') == 'isolated' and len(name) > 50:
        raise ValidationError('Password Manager names must be 50 characters or fewer.')
    if services['videos'] != 'none' and videos_name_taken(name):
        raise ValidationError('A Videos account or invitation already uses that name. Choose a different name.')
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
