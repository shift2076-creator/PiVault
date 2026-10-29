"""Every portal route. Browser scripts are served from portal/static/portal/."""
from django.urls import path
from portal import notification_views as notifications
from portal import search_views, vault_views, views
from portal.vault_repair import repair as repair_existing_vault

urlpatterns = [
    # Browser scripts (same-origin, so the strict CSP allows them).
    path('users/account-form.js', views.script('account-form.js')),
    path('users/vault.js', views.script('vault-crypto.js')),
    path('users/complete.js', views.script('complete.js')),
    path('users/vault-repair.js', views.script('vault-repair.js')),
    path('notifications/bell.js', notifications.admin_only(views.script('notifications-bell.js'))),
    # Notifications (Admin only).
    path('notifications/api', notifications.feed),
    path('notifications/seen', notifications.seen),
    path('notifications', notifications.overview),
    path('notifications/<str:event_id>/resolve', notifications.resolve),
    path('notifications/<str:event_id>', notifications.detail),
    # Joining and accounts.
    path('join', views.join),
    path('join/plan', vault_views.plan),
    path('users/sign-in', views.sign_in),
    path('users/sign-out', views.sign_out),
    path('users/session', views.session),
    path('users/add', views.add_user),
    path('system', views.system),
    path('users/offline-mode', views.offline_mode),
    path('users/complete', vault_views.complete),
    path('users/clients/<str:client>', vault_views.download),
    path('users/repair-vault', repair_existing_vault),
    # Unified launcher search (signed-in users; only apps they can use).
    path('search', search_views.results),
    path('search/apps', search_views.apps),
    # Private Caddy-only auth endpoint; Caddy answers 404 for it publicly.
    path('internal/gate', views.gate),
]
