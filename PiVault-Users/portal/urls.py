from django.urls import path
from . import views
from . import notification_views as notifications
from . import vault_views

urlpatterns = [
    path('users/account-form.js', views.account_form_script),
    path('notifications/api', notifications.feed),
    path('notifications/seen', notifications.seen),
    path('notifications/bell.js', notifications.bell_script),
    path('notifications', notifications.overview),
    path('notifications/<str:event_id>/resolve', notifications.resolve),
    path('notifications/<str:event_id>', notifications.detail),
    path('join', views.join), path('join/plan', vault_views.plan),
    path('users/vault.js', vault_views.crypto_script), path('users/complete', vault_views.complete),
    path('users/complete.js', vault_views.complete_script),
    path('users/clients/<str:client>', vault_views.download),
    path('users/sign-in', views.sign_in),
    path('users/add', views.add_user), path('users/sign-out', views.sign_out),
    path('users/session', views.session), path('internal/gate', views.gate),
]

from .vault_repair import repair as repair_existing_vault
urlpatterns += [path("users/repair-vault", repair_existing_vault)]
