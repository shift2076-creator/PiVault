"""Every portal route. Browser scripts are served from portal/static/portal/."""
from django.urls import path
from portal import notification_views as notifications
from portal import device_views, service_control, vault_views, views
from portal.vault_repair import repair as repair_existing_vault

urlpatterns = [
    # Browser scripts (same-origin, so the strict CSP allows them).
    path('users/account-form.js', views.script('account-form.js')),
    path('users/vault.js', views.script('vault-crypto.js')),
    path('users/complete.js', views.script('complete.js')),
    path('users/vault-repair.js', views.script('vault-repair.js')),
    path('users/shared-copy.js', views.script('shared-copy.js')),
    path('users/add-wizard.js', views.script('add-wizard.js')),
    # Browser icons and the web app manifest. Public, so browsers can fetch them without a session.
    path('favicon.ico', views.static_file('favicon.ico', 'image/x-icon')),
    path('apple-touch-icon.png', views.static_file('apple-touch-icon.png', 'image/png')),
    path('manifest.webmanifest', views.static_file('manifest.webmanifest', 'application/manifest+json')),
    path('notifications/bell.js', notifications.admin_only(views.script('notifications-bell.js'))),
    # Notifications (Admin only).
    path('notifications/api', notifications.feed),
    path('notifications/seen', notifications.seen),
    path('notifications', notifications.overview),
    path('notifications/<str:event_id>/resolve', notifications.resolve),
    path('notifications/clear-failures', notifications.clear_failures),
    path('notifications/<str:event_id>', notifications.detail),
    # Joining and accounts.
    path('join', views.join),
    path('join/plan', vault_views.plan),
    path('users/sign-in', views.sign_in),
    path('users/sign-out', views.sign_out),
    path('users/session', views.session),
    path('users/add', views.add_user),
    path('users/add/shared', vault_views.attach_shared),
    path('users/vault-proxy/<str:action>', vault_views.vault_proxy),
    path('certificate', views.certificate),
    path('certificate/ca.crt', device_views.ca_certificate),
    path('users/offline-mode', notifications.admin_only(views.offline_mode)),
    path('users/complete', vault_views.complete),
    path('users/clients/<str:client>', vault_views.download),
    path('users/repair-vault', repair_existing_vault),
    # Launcher status dots; Admin start, stop and restart (Docker proxy, see service_control.py).
    path('services/status', service_control.status),
    path('services/<str:key>/<str:verb>', service_control.control),
    # Android devices. Admin pages use the session; /devices/api/* takes the helper's sync token instead.
    path('devices', device_views.overview),
    path('devices/page.js', views.script('devices-page.js')),
    path('devices/sync.py', device_views.helper_script),
    path('devices/ca.crt', device_views.ca_certificate),
    path('devices/<uuid:device_id>/rename', device_views.rename),
    path('devices/<uuid:device_id>/apps/<str:client>', device_views.set_app),
    path('devices/apps/<str:client>/version', device_views.set_version),
    path('devices/api/identify', device_views.identify),
    path('devices/api/manifest', device_views.manifest),
    path('devices/api/apk/<str:client>', device_views.apk),
    path('devices/api/<uuid:device_id>/report', device_views.report),
    # Private Caddy-only auth endpoint; Caddy answers 404 for it publicly.
    path('internal/gate', views.gate),
]
