from django.core.management.base import BaseCommand
from portal.client_packages import ClientRunBusy, maintain
from django.utils import timezone
from portal.models import Notification, SiteSettings


class Command(BaseCommand):
    help = 'Apply explicitly approved local client releases, then check for new stable releases.'

    def add_arguments(self, parser):
        parser.add_argument('--check-only', action='store_true')

    def handle(self, *args, **options):
        if SiteSettings.is_offline():
            self.stdout.write('Offline mode is on: release checks and approved downloads are paused.')
            return
        # A client that fails its own check gets its own notice from maintain(); the run still finishes,
        # so one client's problem does not mark the whole nightly job as failed.
        try:
            errors = maintain(check_only=options['check_only'])
        except ClientRunBusy:
            self.stdout.write('Another client maintenance run is in progress; this run is skipped.')
            return
        Notification.objects.filter(source='maintenance', title='Nightly client downloads needs review',
            status='open').update(status='resolved', resolution='resolve', resolved_at=timezone.now())
        if errors:
            self.stdout.write('Client checks needing review: ' + ', '.join(errors))
        else:
            self.stdout.write('Approved local client updates processed; official release checks complete.')
