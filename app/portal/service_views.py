from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET, require_POST

from . import services_control
from .notification_views import admin_only


@require_GET
def status(request):
    """Launcher status for any signed-in user. Only Admins are told they can control services."""
    if not request.user.is_authenticated or not request.user.is_active:
        return JsonResponse({'error': 'Sign in required.'}, status=401)
    return JsonResponse({
        'services': services_control.statuses(),
        'can_control': request.user.role == 'admin',
        'csrf_token': get_token(request),
    })


@require_POST
@admin_only
def action(request, key, verb):
    try:
        services_control.act(key, verb)
    except ValueError:
        return JsonResponse({'error': 'Unknown service or action.'}, status=404)
    except services_control.ProxyError as error:
        return JsonResponse({'error': str(error)}, status=502)
    return JsonResponse({'ok': True})
