"""Radicale per-user logins for Calendar and contacts sync through its htpasswd file.

Radicale authenticates against the htpasswd file in its config folder. Each enrolled
user gets one bcrypt entry there. Radicale's owner_only rights already limit each
login to its own collections, so this adapter never edits the rights file. The
entry is written atomically, read back and password-checked, and removed again if
the enrollment fails. Passwords longer than bcrypt's 72-byte limit are refused.
"""
import fcntl
import os
import tempfile
from contextlib import contextmanager
from functools import partial

import bcrypt
from django.core.exceptions import ValidationError

from .native_http import CreatedAccount

MAX_PASSWORD_BYTES = 72  # bcrypt refuses longer passwords rather than truncating them.


@contextmanager
def _folder_lock(folder):
    # Lock the folder, not the file: the file is replaced on every write.
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # Closing the descriptor releases the lock.


def _login_name(line):
    return line.partition(':')[0].strip().casefold()


class RadicaleAdapter:
    modes = ('isolated',)

    def __init__(self, users_file):
        if not isinstance(users_file, str) or not users_file.startswith('/'):
            raise ValueError('The Radicale users file must be an absolute path.')
        self.users_file = users_file

    def credential_username(self, user):
        return user.email.strip().lower()

    def _read(self):
        try:
            with open(self.users_file, encoding='utf-8') as handle:
                return [line for line in handle.read().splitlines() if line.strip()]
        except FileNotFoundError:
            raise ValidationError('The Radicale login file is missing. An admin must restore it.')

    def _write(self, lines):
        folder = os.path.dirname(self.users_file)
        fd, temp = tempfile.mkstemp(dir=folder, prefix='.users-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write('\n'.join(lines) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, self.users_file)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise

    def _verifies(self, name, password):
        for line in self._read():
            if _login_name(line) == name:
                return bcrypt.checkpw(password, line.partition(':')[2].strip().encode('ascii'))
        return False

    def provision(self, *, user, password, mode):
        if mode not in self.modes:
            raise ValidationError('Radicale does not support that access mode.')
        name = self.credential_username(user)
        if not name or ':' in name or any(ch.isspace() for ch in name):
            raise ValidationError('Radicale needs an email address as its login.')
        secret = password.encode('utf-8')
        if len(secret) > MAX_PASSWORD_BYTES:
            raise ValidationError('Radicale passwords must be 72 bytes or fewer. Choose a shorter password.')
        digest = bcrypt.hashpw(secret, bcrypt.gensalt(rounds=12)).decode('ascii')
        with _folder_lock(os.path.dirname(self.users_file)):
            lines = self._read()
            if name in [_login_name(line) for line in lines]:
                raise ValidationError('A Radicale login already uses that email. An admin must resolve it before joining.')
            self._write(lines + [f'{name}:{digest}'])
            if not self._verifies(name, secret):
                self._remove(name)
                raise ValidationError('The Radicale login could not be verified.')
        return CreatedAccount(service='radicale', native_id=name, rollback=partial(self.remove, name))

    def remove(self, name):
        with _folder_lock(os.path.dirname(self.users_file)):
            self._remove(name)

    def _remove(self, name):
        lines = self._read()
        kept = [line for line in lines if _login_name(line) != name]
        if len(kept) != len(lines):
            self._write(kept)
