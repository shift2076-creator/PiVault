from django.core.management.base import BaseCommand
from portal.models import SiteSettings


class Command(BaseCommand):
    help = 'Show or set offline mode, which pauses nightly jobs that need the internet. Prints on or off.'

    def add_arguments(self, parser):
        choice = parser.add_mutually_exclusive_group()
        choice.add_argument('--on', action='store_true', help='Turn offline mode on.')
        choice.add_argument('--off', action='store_true', help='Turn offline mode off.')
        choice.add_argument('--status', action='store_true', help='Only print the current state (default).')

    def handle(self, *args, **options):
        if options['on'] or options['off']:
            SiteSettings.set_offline(options['on'])
        self.stdout.write('on' if SiteSettings.is_offline() else 'off')
