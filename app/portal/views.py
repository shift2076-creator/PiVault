import base64
import io
import logging
import uuid
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
from .models import User
from .services import SERVICES, ADAPTERS, provision, rollback_accounts
from .enrollment import enrollment_user, invitation_default, SetupConfigurationError
from .native_http import NativeError
from .setup_logging import begin, end, event


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
    return render(request, 'users.html', {'mode': mode, 'services': services,
                  'client_rows': client_rows() if mode == 'admin' else [],
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
        # SQLite IMMEDIATE serializes the first-admin decision and invite use.
        with transaction.atomic():
            stage = 'policy'
            invitation = find_valid(request.POST.get('code', ''))
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError('An account already uses that email. Sign in instead.')
            user = enrollment_user(invitation, email)
            event('policy_selected', stage='invitation' if invitation else 'first_admin')
            stage = 'password_validation'
            validate_password(password, user)
            stage = 'vault_bundle'
            from .vault_views import join_bundle
            bundle = join_bundle(request, invitation, user, password)
            event('bundle_validated', stage=stage)
            if not User.objects.exists() and user.role != 'admin':
                raise ValidationError('The first account must be Admin.')
            stage = 'portal_account'
            user.set_password(password)
            user.save()
            stage = 'services'
            created = provision(user, password, vault_bundle=bundle) or []
            user.save(update_fields=['vault_user_id', 'vault_items'])
            if invitation is not None:
                invitation.used_at = timezone.now()
                invitation.save(update_fields=['used_at'])
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


@require_GET
def account_form_script(request):
    from pathlib import Path
    return HttpResponse((Path(__file__).parent / 'account-form.js').read_text(),
                        content_type='application/javascript; charset=utf-8')
