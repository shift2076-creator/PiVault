from functools import wraps
from pathlib import Path
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from .models import Notification, NotificationReceipt
from .notifications import ingest, publish_decisions, IDENTIFIER


def admin_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            return JsonResponse({'error': 'Sign in required.'}, status=401)
        if request.user.role != 'admin':
            return JsonResponse({'error': 'Admin access required.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


@require_GET
@admin_only
def feed(request):
    ingest()
    publish_decisions()
    values = Notification.objects.filter(status='open')
    return JsonResponse({
        'open_count': values.count(),
        'unread_count': values.exclude(receipts__user=request.user).count(),
        'csrf_token': get_token(request),
        'items': [{'id': item.id, 'source': item.source, 'level': item.level,
                   'title': item.title, 'message': item.message,
                   'created_at': item.created_at.isoformat(),
                   'pending_delivery': bool(item.resolution),
                   'url': '/notifications/' + item.id}
                  for item in values.order_by('-created_at')[:50]],
    })


@require_POST
@admin_only
def seen(request):
    ids = request.POST.getlist('ids')
    if len(ids) > 100 or any(not IDENTIFIER.fullmatch(value) for value in ids):
        return JsonResponse({'error': 'Invalid notification list.'}, status=400)
    for item in Notification.objects.filter(id__in=ids):
        NotificationReceipt.objects.get_or_create(notification=item, user=request.user)
    return JsonResponse({'ok': True})


@require_GET
@admin_only
def overview(request):
    ingest()
    publish_decisions()
    return render(request, 'notifications.html', {
        'open_notifications': Notification.objects.filter(status='open').order_by('-created_at')[:100],
        'resolved_notifications': Notification.objects.filter(status='resolved').order_by('-resolved_at')[:50],
    })


@require_GET
@admin_only
def detail(request, event_id):
    publish_decisions()
    item = get_object_or_404(Notification, id=event_id)
    data = item.details
    from .models import ClientRelease
    client_update = item.kind == 'clients.update' and ClientRelease.objects.filter(
        notification=item, id=data.get('release_id')).exists()
    context = {'item': item, 'cinema': item.kind == 'cinema.filesize', 'client_update': client_update}
    if item.kind == 'cinema.filesize':
        context['sizes'] = [(label, f'{data.get(key, 0) / 2**30:.2f} GiB') for label, key in [
            ('Published filesize', 'actual_bytes'), ('Estimated filesize', 'estimated_bytes'),
            ('Allowed minimum', 'minimum_bytes'), ('Allowed maximum', 'maximum_bytes')]]
    return render(request, 'notifications.html', context)


@require_POST
@admin_only
def resolve(request, event_id):
    action = request.POST.get('action', '')
    with transaction.atomic():
        item = get_object_or_404(Notification.objects.select_for_update(), id=event_id)
        allowed = ('approve', 'skip') if item.kind in ('cinema.filesize', 'clients.update') else ('resolve',)
        if action not in allowed:
            return JsonResponse({'error': 'Invalid resolution.'}, status=400)
        if item.status == 'resolved' or item.resolution:
            return JsonResponse({'error': 'Another admin has already handled this notification.'}, status=409)
        item.resolution = action
        item.resolved_by = request.user
        if item.kind == 'clients.update':
            from .models import ClientRelease
            if not ClientRelease.objects.filter(notification=item, id=item.details.get('release_id')).exists():
                return JsonResponse({'error': 'This release is no longer available for review.'}, status=409)
        if item.kind not in ('cinema.filesize', 'clients.update') or (item.kind == 'clients.update' and action == 'skip'):
            item.status = 'resolved'
            item.resolved_at = timezone.now()
        item.save(update_fields=['resolution', 'resolved_by', 'status', 'resolved_at'])
        NotificationReceipt.objects.get_or_create(notification=item, user=request.user)
    publish_decisions()
    item.refresh_from_db()
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'status': item.status, 'pending_delivery': item.status == 'open'},
                            status=202 if item.status == 'open' else 200)
    return redirect('/notifications/' + item.id)


@require_GET
@admin_only
def bell_script(request):
    return HttpResponse((Path(__file__).parent / 'notifications-bell.js').read_text(),
                        content_type='application/javascript; charset=utf-8')
