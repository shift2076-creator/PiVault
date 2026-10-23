from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=80)
    role = models.CharField(max_length=5, choices=[('admin', 'Admin'), ('user', 'User')])
    service_access = models.JSONField(default=dict)
    vault_user_id = models.CharField(max_length=64, blank=True)
    vault_items = models.JSONField(default=dict)


class Invitation(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=5)
    services = models.JSONField(default=dict)
    created_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True)


class Attempt(models.Model):
    scope = models.CharField(max_length=64, db_index=True)
    at = models.DateTimeField(db_index=True)


class Notification(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    source = models.CharField(max_length=64, db_index=True)
    kind = models.CharField(max_length=64)
    level = models.CharField(max_length=8, choices=[('info', 'Info'), ('warning', 'Warning'), ('error', 'Error')])
    title = models.CharField(max_length=200)
    message = models.TextField()
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=8, default='open', choices=[('open', 'Open'), ('resolved', 'Resolved')], db_index=True)
    resolution = models.CharField(max_length=16, blank=True)
    resolved_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='notification_resolutions')
    resolved_at = models.DateTimeField(null=True)


class NotificationReceipt(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='receipts')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['notification', 'user'], name='notification_seen_per_admin')]


class ClientRelease(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    client = models.CharField(max_length=16)
    version = models.CharField(max_length=64)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    url = models.URLField(max_length=2048)
    release_url = models.URLField(max_length=2048)
    discovered_at = models.DateTimeField(auto_now_add=True)
    notification = models.OneToOneField(Notification, null=True, on_delete=models.PROTECT)


class ClientPackage(models.Model):
    client = models.CharField(max_length=16, primary_key=True)
    current = models.ForeignKey(ClientRelease, null=True, on_delete=models.PROTECT, related_name='+')
    previous = models.ForeignKey(ClientRelease, null=True, on_delete=models.PROTECT, related_name='+')
    latest = models.ForeignKey(ClientRelease, null=True, on_delete=models.PROTECT, related_name='+')
    checked_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=200, blank=True)
