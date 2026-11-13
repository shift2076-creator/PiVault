"""Radicale access through one shared login for Calendar and contacts sync.

Radicale authenticates against the htpasswd file in its config folder. Calendar and contacts
are shared or none: the one shared entry is added to that file by the admin, and its
password lives in the admin's vault. Joining copies that entry into each new vault. This
adapter only reads the file, to list login names. It never writes the file or the rights file.
"""
from django.core.exceptions import ValidationError


def _login_name(line):
    return line.partition(':')[0].strip().casefold()


class RadicaleAdapter:
    modes = ('shared',)

    def __init__(self, users_file):
        if not isinstance(users_file, str) or not users_file.startswith('/'):
            raise ValueError('The Radicale users file must be an absolute path.')
        self.users_file = users_file

    def credential_username(self, user):
        return user.email.strip().lower()

    def list_accounts(self):
        # Login names only: the password hash on each line is never returned.
        return [_login_name(line) for line in self._read()]

    def _read(self):
        try:
            with open(self.users_file, encoding='utf-8') as handle:
                return [line for line in handle.read().splitlines() if line.strip()]
        except FileNotFoundError:
            raise ValidationError('The Radicale login file is missing. An admin must restore it.')

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Radicale is shared or none. Per-user Radicale is not offered.')
        # One shared login serves everyone; the vault copy supplies it, so no per-person entry is written.
        return None
