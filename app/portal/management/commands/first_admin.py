from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from portal.invitations import issue
from portal.models import Invitation, User
from portal.services import ADAPTERS


class Command(BaseCommand):
    help = 'Issue the first Admin invitation locally; never enables public bootstrap.'

    def add_arguments(self, parser):
        parser.add_argument('name')

    def handle(self, *args, **options):
        with transaction.atomic():
            if User.objects.exists():
                raise CommandError('An account already exists. Sign in as Admin.')
            Invitation.objects.filter(created_by=None, used_at=None).delete()
            # The Admin account gets shared Music when Music is configured. Private storage belongs to a separate User account.
            access = {'music': 'shared'} if 'music' in ADAPTERS else {}
            invitation, code = issue(options['name'], 'admin', access)
        self.stdout.write(settings.JOIN_URL)
        self.stdout.write(f'Code: {code} (expires in 30 minutes)')
