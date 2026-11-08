import base64
import io
import logging
import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.debug import sensitive_post_parameters
from .invitations import find_valid, issue, rate_limit
from .models import Invitation, SiteSettings, User
from .services import SERVICES, ADAPTERS, provision, rollback_accounts
from .enrollment import enrollment_user, invitation_default, SetupConfigurationError
from .integrations.native_http import NativeError
from .setup_logging import begin, end, event
from .device_views import root_certificate, root_fingerprint
from .tools import tool_links


def page(request, mode, **context):
    status = context.pop('status_code', 200)
    context['has_accounts'] = User.objects.exists()
    services = []
    for key, label in SERVICES:
        adapter = ADAPTERS.get(key)
        supported = set(adapter.modes) if adapter else set()
        options = [{'value': value, 'label': title} for value, title in (
            ('shared', 'Shared'), ('isolated', 'Per user' if key == 'passwords' else 'Isolated'), ('none', 'No access'),
        ) if value == 'none' or value in supported]
        services.append({'key': key, 'label': label, 'selected': request.POST.get(f'service_{key}', 'none') if request.method == 'POST' else invitation_default(key),
                         'options': options})
    from .client_packages import client_rows
    root = root_certificate() if mode == 'system' else None
    return render(request, 'users.html', {'mode': mode, 'services': services,
                  'client_rows': client_rows() if mode == 'admin' else [],
                  'offline_mode': SiteSettings.is_offline() if mode in ('admin', 'system') else False,
                  'ca_ready': root is not None,
                  'ca_fingerprint': root_fingerprint(root) if root is not None else '',
                  'tools': tool_links(request.get_host().split(':')[0]) if mode == 'system' else [],
                  'name': request.POST.get('name', ''),
                  'selected_role': request.POST.get('role', 'user'), **context}, status=status)


def error_text(error):
    return ' '.join(error.messages)


@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('password', 'password_confirm', 'vault_bundle')
def join(request):
    if request.method == 'GET':
        return page(request, 'join')
    reference, context_token = begin('join')
    created = []
    claimed = None
    committed = False
    stage = 'validation'
    try:
        if not rate_limit(request, 'join'):
            event('request_rejected', stage='rate_limit', status=429, level=logging.WARNING)
            return page(request, 'join', error='Too many attempts. Try again later.', status_code=429)
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        if password != request.POST.get('password_confirm', ''):
            raise ValidationError('Passwords do not match.')
        validate_email(email)
        if len(email) > 254 or len(password) > 1024:
            raise ValidationError('Email or password is too long.')
        # A short IMMEDIATE transaction checks the request and claims the single-use
        # invitation. Slow work (vault key checks, other apps' APIs) then runs without
        # holding SQLite's write lock, so the rest of the portal stays responsive.
        with transaction.atomic():
            stage = 'policy'
            invitation = find_valid(request.POST.get('code', ''))
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError('An account already uses that email. Sign in instead.')
            user = enrollment_user(invitation, email)
            event('policy_selected', stage='invitation')
            stage = 'password_validation'
            validate_password(password, user)
            if not User.objects.exists() and user.role != 'admin':
                raise ValidationError('The first account must be Admin.')
            stage = 'claim'
            if not Invitation.objects.filter(pk=invitation.pk, used_at=None).update(used_at=timezone.now()):
                raise ValidationError('Invalid or expired invitation code.')
            claimed = invitation
        stage = 'vault_bundle'
        from .vault_views import join_bundle
        bundle = join_bundle(request, invitation, user, password)
        event('bundle_validated', stage=stage)
        stage = 'services'
        user.set_password(password)
        created = provision(user, password, vault_bundle=bundle) or []
        stage = 'portal_account'
        with transaction.atomic():
            user.save()
        stage = 'commit'
        committed = True
        event('account_committed', stage=stage)
        stage = 'session'
        login(request, user)
        event('request_succeeded', stage=stage, status=302)
        return redirect('/users/complete' if user.vault_user_id else '/')
    except Exception as error:
        failures = list(getattr(error, 'cleanup_failures', []))
        if not committed:
            failures.extend(rollback_accounts(created))
            if claimed is not None:
                # Return the invitation so the person can simply try again.
                try:
                    Invitation.objects.filter(pk=claimed.pk).update(used_at=None)
                except Exception as release_error:
                    event('invitation_release_failed', stage=stage, error=release_error, level=logging.ERROR)
        if failures:
            # This is outside the rolled-back identity transaction.
            try:
                from .notifications import notify
                notify('users', reference, 'User setup needs review',
                       'Enrollment failed and a newly created service account needs cleanup.',
                       level='error', details={'accounts': failures})
            except Exception as notice_error:
                event('notification_failed', stage='cleanup_notice', error=notice_error, level=logging.ERROR)
        if committed:
            message = 'Your account was created, but sign-in could not finish. Open Sign in to continue.'
            status = 500
        elif failures or isinstance(error, (SetupConfigurationError, DatabaseError)):
            message = error_text(error) if isinstance(error, ValidationError) else 'Account setup is temporarily unavailable. Try again.'
            status = 503
        elif isinstance(error, NativeError):
            message = error_text(error)
            status = 502
        elif isinstance(error, ValidationError):
            message = error_text(error)
            status = 400
        else:
            message = 'Account setup failed. The setup reference below identifies the server error.'
            status = 500
        event('request_failed', stage=stage, status=status, error=error,
              level=logging.ERROR if status >= 500 else logging.WARNING)
        response = page(request, 'join', error=message + ' Setup reference: ' + reference + '.',
                        email=request.POST.get('email', ''), status_code=status)
        response['X-PiVault-Setup-ID'] = reference
        return response
    finally:
        end(context_token)


@require_http_methods(['GET', 'POST'])
def sign_in(request):
    if request.method == 'GET' and request.user.is_authenticated:
        return redirect('/')
    if not User.objects.exists():
        return page(request, 'join')
    if request.method == 'GET':
        return page(request, 'sign-in')
    if not rate_limit(request, 'login'):
        return page(request, 'sign-in', error='Too many attempts. Try again later.', status_code=429)
    email = request.POST.get('email', '').strip().lower()
    found = User.objects.filter(email__iexact=email).first()
    # Always call the backend, including unknown emails, to run its dummy hash.
    user = authenticate(request, username=found.username if found else '__missing__',
                        password=request.POST.get('password', ''))
    if not user:
        return page(request, 'sign-in', error='Email or password is incorrect.', email=email, status_code=401)
    login(request, user)
    return redirect('/')


@require_http_methods(['GET', 'POST'])
def add_user(request):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in')
    if request.user.role != 'admin':
        return HttpResponseForbidden()
    if request.method == 'GET':
        return page(request, 'admin')
    try:
        invitation, code = issue(
            request.POST.get('name', ''), request.POST.get('role', ''),
            {key: request.POST.get(f'service_{key}', 'none') for key, _ in SERVICES},
            issuer=request.user,
        )
        output = io.BytesIO()
        qrcode.make(settings.JOIN_URL, image_factory=qrcode.image.svg.SvgPathImage,
                    box_size=5, border=4).save(output)
        qr = base64.b64encode(output.getvalue()).decode()
        return page(request, 'admin', invitation=invitation, code=code,
                    join_url=settings.JOIN_URL, qr=qr)
    except ValidationError as error:
        event('invitation_rejected', stage='invitation', status=400, error=error, level=logging.WARNING)
        return page(request, 'admin', error=error_text(error), status_code=400)
    except DatabaseError as error:
        # SQLite can be busy during setup; show the admin page again instead of a server error.
        event('invitation_failed', stage='invitation', status=503, error=error, level=logging.ERROR)
        return page(request, 'admin', error='The invitation could not be saved. Try again.', status_code=503)


@require_http_methods(['GET'])
def system(request):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in')
    if request.user.role != 'admin':
        return HttpResponseForbidden()
    return page(request, 'system')


@require_POST
def offline_mode(request):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in')
    if request.user.role != 'admin':
        return HttpResponseForbidden()
    SiteSettings.set_offline(request.POST.get('offline') == 'on')
    return redirect('/system')


@require_POST
def sign_out(request):
    logout(request)
    return redirect('/users/sign-in')


@require_GET
def gate(request):
    # Private Caddy-only auth endpoint. Never expose it as a public URL.
    if not request.user.is_authenticated or not request.user.is_active:
        return HttpResponse(status=401)
    service = request.GET.get('service', '')
    if service:
        adapter = ADAPTERS.get(service)
        mode = request.user.service_access.get(service, 'none')
        if mode == 'none' or not adapter or mode not in adapter.modes:
            return HttpResponse(status=403)
    response = HttpResponse(status=204)
    response['X-PiVault-User'] = str(request.user.pk)
    response['X-PiVault-Role'] = request.user.role
    return response


@require_GET
def session(request):
    from django.http import JsonResponse
    if not request.user.is_authenticated:
        return JsonResponse({'authenticated': False}, status=401)
    return JsonResponse({'authenticated': True, 'admin': request.user.role == 'admin'})


from functools import lru_cache


@lru_cache(maxsize=None)
def _asset(name):
    from pathlib import Path
    return (Path(__file__).parent / 'static' / 'portal' / name).read_text()


def script(name):
    @require_GET
    def view(request):
        return HttpResponse(_asset(name), content_type='application/javascript; charset=utf-8')
    return view


def static_file(name, content_type):
    """A public file from portal/static/portal, served as bytes (icons and the web app manifest)."""
    from pathlib import Path
    data = (Path(__file__).parent / 'static' / 'portal' / name).read_bytes()
    @require_GET
    def view(request):
        return HttpResponse(data, content_type=content_type)
    return view
