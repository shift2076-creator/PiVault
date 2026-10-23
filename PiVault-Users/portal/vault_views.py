import base64
import io
import json
import logging
from pathlib import Path
import qrcode
import qrcode.image.svg
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from .client_packages import CLIENTS, client_rows, release_path
from .invitations import find_valid, rate_limit
from .models import ClientPackage, User
from .runtime import PUBLIC_URLS, credential_plan
from .enrollment import enrollment_user, plan_identity, SetupConfigurationError
from .setup_logging import begin, end, event
from .vaultwarden import validate_bundle


def draft(invitation, email):
    return enrollment_user(invitation, email)


def manifest_for(user):
    try:
        return credential_plan(user)
    except ValidationError as error:
        raise SetupConfigurationError(error.messages) from None


@require_POST
def plan(request):
    reference, context_token = begin('vault_plan')
    try:
        if not rate_limit(request, 'vault-plan'):
            event('request_rejected', stage='rate_limit', status=429, level=logging.WARNING)
            return JsonResponse({'error': 'Too many attempts. Try again later.'}, status=429)
        email = request.POST.get('email', '').strip().lower()
        validate_email(email)
        if len(email) > 254:
            raise ValidationError('Email is too long.')
        invitation = find_valid(request.POST.get('code', '')) if User.objects.exists() else None
        user = draft(invitation, email)
        required = user.service_access.get('passwords', 'none') != 'none'
        if not required:
            event('plan_ready', stage='without_vault', status=200)
            return JsonResponse({'vault_required': False})
        if len(user.display_name) > 50:
            raise ValidationError('Password Manager names must be 50 characters or fewer.')
        manifest = manifest_for(user)
        token = signing.dumps(plan_identity(invitation, user, manifest), salt='vault-plan')
        event('plan_ready', stage='with_vault', status=200)
        return JsonResponse({'vault_required': True, 'items': manifest, 'token': token})
    except Exception as error:
        status = 503 if isinstance(error, (SetupConfigurationError, DatabaseError)) else 400 if isinstance(error, ValidationError) else 500
        message = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Could not prepare account setup.'
        event('request_failed', stage='vault_plan', status=status, error=error,
              level=logging.ERROR if status >= 500 else logging.WARNING)
        response = JsonResponse({'error': message + ' Setup reference: ' + reference + '.', 'setup_reference': reference}, status=status)
        response['X-PiVault-Setup-ID'] = reference
        return response
    finally:
        end(context_token)


def join_bundle(request, invitation, user, password):
    from .services import ADAPTERS
    adapter = ADAPTERS.get('passwords')
    if user.service_access.get('passwords', 'none') == 'none' or not getattr(adapter, 'requires_browser_bundle', False):
        return None
    try:
        encoded = request.POST.get('vault_bundle', '')
        if not encoded or len(encoded) > 65536:
            raise ValueError()
        value = json.loads(encoded)
        payload = signing.loads(value['token'], salt='vault-plan', max_age=300)
        manifest = manifest_for(user)
        if payload != plan_identity(invitation, user, manifest):
            raise ValueError()
        return validate_bundle(value['bundle'], password, user.email, manifest)
    except (ValueError, TypeError, KeyError, signing.BadSignature):
        raise ValidationError('Could not validate the encrypted vault setup. Enable JavaScript and try joining again.') from None


@require_GET
def crypto_script(request):
    return HttpResponse((Path(__file__).parent / 'vault-crypto.js').read_text(),
                        content_type='application/javascript; charset=utf-8')


@require_GET
def complete_script(request):
    return HttpResponse((Path(__file__).parent / 'complete.js').read_text(),
                        content_type='application/javascript; charset=utf-8')


@require_GET
def complete(request):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in')
    if not request.user.vault_user_id or request.user.service_access.get('passwords') != 'isolated':
        return HttpResponseForbidden()
    output = io.BytesIO()
    url = PUBLIC_URLS.get('passwords', '')
    if not url:
        return HttpResponse('The vault client address needs Admin attention.', status=503)
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=5, border=4).save(output)
    return render(request, 'complete.html', {'vault_url': url,
        'qr': base64.b64encode(output.getvalue()).decode(), 'clients': client_rows(),
        'saved_count': len(request.user.vault_items)})


@require_GET
def download(request, client):
    if not request.user.is_authenticated:
        return redirect('/users/sign-in')
    if request.user.role != 'admin' and (not request.user.vault_user_id or request.user.service_access.get('passwords') != 'isolated'):
        return HttpResponseForbidden()
    if client not in CLIENTS:
        raise Http404()
    package = ClientPackage.objects.select_related('current').filter(client=client).first()
    if not package or not package.current:
        raise Http404('No approved local download is available.')
    path = release_path(package.current)
    if not path.is_file() or path.is_symlink():
        raise Http404('The approved local download is unavailable.')
    return FileResponse(path.open('rb'), as_attachment=client != 'firefox',
        filename='Bitwarden-' + package.current.version + CLIENTS[client][1], content_type=CLIENTS[client][2])
