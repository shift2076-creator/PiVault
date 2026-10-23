from django.core.management.base import BaseCommand, CommandError
from portal.client_packages import maintain


class Command(BaseCommand):
    help = 'Apply explicitly approved local client releases, then check for new stable releases.'

    def add_arguments(self, parser):
        parser.add_argument('--check-only', action='store_true')

    def handle(self, *args, **options):
        errors = maintain(check_only=options['check_only'])
        if errors:
            raise CommandError('Client maintenance needs review: ' + ', '.join(errors))
        self.stdout.write('Approved local client updates processed; official release checks complete.')
