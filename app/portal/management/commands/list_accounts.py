from django.core.management.base import BaseCommand

from portal.account_report import build_report, format_report


class Command(BaseCommand):
    help = 'Read-only list of every account in every service, checked against PiVault users. Prints names only.'

    def handle(self, *args, **options):
        self.stdout.write(format_report(build_report()))
