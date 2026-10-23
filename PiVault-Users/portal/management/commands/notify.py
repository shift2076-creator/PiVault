from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from portal.notifications import notify


class Command(BaseCommand):
    help = 'Add one persistent admin notification. The incident key deduplicates repeated delivery.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True)
        parser.add_argument('--key', required=True)
        parser.add_argument('--title', required=True)
        parser.add_argument('--message', required=True)
        parser.add_argument('--kind', default='service.notice')
        parser.add_argument('--level', choices=['info', 'warning', 'error'], default='warning')

    def handle(self, **options):
        try:
            item = notify(options['source'], options['key'], options['title'], options['message'],
                          kind=options['kind'], level=options['level'])
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from error
        self.stdout.write(item.id)
