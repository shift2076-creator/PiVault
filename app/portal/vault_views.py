import base64
import io
import json
import logging
import qrcode
import qrcode.image.svg
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from .client_packages import CLIENTS, client_rows, download_filename, release_path
from .invitations import find_valid, rate_limit
from .models import ClientPackage
from .runtime import PUBLIC_URLS, credential_plan
from .enrollment import enrollment_user, plan_identity, SetupConfigurationError
from .setup_logging import begin, end, event
from .integrations.vaultwarden import validate_bundle
from .invitations import attach_shared as store_shared
from .services import ADAPTERS
from django.http import HttpResponseNotAllowed
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
        invitation = find_valid(request.POST.get('code', ''))
        user = draft(invitation, email)
        required = user.service_access.get('passwords', 'none') != 'none'
        if not required:
            event('plan_ready', stage='without_vault', status=200)
            return JsonResponse({'vault_required': False})
        if len(user.display_name) > 50:
            raise ValidationError('Password Manager names must be 50 characters or fewer.')
        manifest = manifest_for(user)
        shared = any(value.get('shared') for value in manifest.values())
        if shared and not invitation.shared_sealed:
            raise ValidationError('The shared logins have not been copied for this invitation yet. Ask the admin to finish step 4.')
        token = signing.dumps(plan_identity(invitation, user, manifest), salt='vault-plan')
        event('plan_ready', stage='with_vault', status=200)
        return JsonResponse({'vault_required': True, 'items': manifest, 'token': token,
                             'shared': invitation.shared_sealed if shared else ''})
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
        filename=download_filename(client, package.current.version), content_type=CLIENTS[client][2])


@require_POST
def attach_shared(request):
    """Admin only: attach the sealed shared logins to the open invitation they were made for."""
    if not request.user.is_authenticated or request.user.role != 'admin':
        return HttpResponseForbidden()
    try:
        store_shared(request.POST.get('code', ''), request.user, request.POST.get('sealed', ''))
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    event('shared_logins_attached', stage='invitation', status=200)
    return JsonResponse({'ok': True})


# The admin's browser reads its own Vaultwarden entries through these three calls only.
PROXY_ROUTES = {
    'prelogin': ('POST', '/identity/accounts/prelogin'),
    'token': ('POST', '/identity/connect/token'),
    'sync': ('GET', '/api/sync?excludeDomains=true'),
}
TOKEN_FIELDS = {'grant_type', 'username', 'password', 'scope', 'client_id', 'deviceType', 'deviceIdentifier', 'deviceName'}


@require_http_methods(['GET', 'POST'])
def vault_proxy(request, action):
    """Admin only. Forwards three Vaultwarden calls so the admin's browser can read its own shared entries.

    The browser needs these calls on the portal origin; Vaultwarden does not serve CORS to it. Only these
    paths are relayed, only the named fields are sent, and nothing from the response's cookies is passed on.
    """
    if not request.user.is_authenticated or request.user.role != 'admin':
        return HttpResponseForbidden()
    route = PROXY_ROUTES.get(action)
    if not route:
        raise Http404()
    method, path = route
    if request.method != method:
        return HttpResponseNotAllowed([method])
    adapter = ADAPTERS.get('passwords')
    base = getattr(getattr(adapter, 'client', None), 'base_url', '')
    if not base:
        return JsonResponse({'error': 'The Password Manager is not configured.'}, status=503)
    headers = {'Accept': 'application/json'}
    body = None
    if action == 'prelogin':
        try:
            value = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'error': 'Invalid request.'}, status=400)
        email = value.get('email') if isinstance(value, dict) and set(value) == {'email'} else None
        if not isinstance(email, str) or len(email) > 254 or '@' not in email:
            return JsonResponse({'error': 'Invalid request.'}, status=400)
        body = json.dumps({'email': email}).encode()
        headers['Content-Type'] = 'application/json'
    elif action == 'token':
        if set(request.POST) - TOKEN_FIELDS or request.POST.get('grant_type') != 'password':
            return JsonResponse({'error': 'Invalid request.'}, status=400)
        body = urlencode({key: request.POST.get(key, '') for key in TOKEN_FIELDS if key in request.POST}).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    else:
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or any(c in authorization for c in '\r\n'):
            return JsonResponse({'error': 'Sign in again.'}, status=401)
        headers['Authorization'] = authorization
    relay = Request(base + path, data=body, method=method, headers=headers)
    status = 200
    try:
        with urlopen(relay, timeout=20) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except HTTPError as error:
        status = error.code
        raw = error.read(64 * 1024)
    except (URLError, OSError):
        return JsonResponse({'error': 'The Password Manager did not answer.'}, status=503)
    if len(raw) > 4 * 1024 * 1024:
        return JsonResponse({'error': 'The Password Manager sent too much data.'}, status=502)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return JsonResponse({'error': 'The Password Manager sent an unreadable reply.'}, status=502)
    if status >= 400:
        return JsonResponse({'error': 'The Password Manager rejected the request.', 'status': status}, status=status)
    return JsonResponse(value, safe=False)
