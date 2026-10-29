"""Shared Greenie-side SSH driver: one PTY and one password prompt reused for SSH and sudo.

The build scripts place this file ahead of installer/relay.py and tools/check/relay.py,
so each generated launcher stays a single self-contained file.
"""
import getpass
import os
import pty
import re
import select
import signal
import sys
import time


def drive(args, *, marker, timeout, timeout_message, key_message, child_setup, payload=None):
    """Run ssh in a PTY and answer its password prompts.

    payload, when given, is (ready_text, data, missing_message). The data is sent once the
    remote side prints ready_text. A zero exit without that request reports missing_message.
    """
    pid, fd = pty.fork()
    if pid == 0:
        child_setup()
        os.execvp('ssh', args)
    password = None
    pending = b''
    seen = {'ssh': 0, 'sudo': 0}
    deadline = time.monotonic() + timeout
    status = None
    payload_sent = False
    ready = payload[0].encode() if payload else None
    marker_bytes = marker.encode()
    try:
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(timeout_message)
            ready_fds, _, _ = select.select([fd], [], [], 1)
            if not ready_fds:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            pending += data
            if b'Enter passphrase for key' in pending:
                raise RuntimeError(key_message)
            if ready and ready in pending:
                if payload_sent:
                    raise RuntimeError('The remote side requested its archive twice.')
                start = pending.index(ready)
                if start:
                    os.write(1, pending[:start])
                pending = pending[start + len(ready):]
                archive = payload[1]
                offset = 0
                while offset < len(archive):
                    offset += os.write(fd, archive[offset:offset + 16384])
                payload_sent = True
            while True:
                match = re.search(rb'(?im)([^\r\n]*\bpassword:[ \t]*|' + re.escape(marker_bytes) + rb')', pending)
                if not match:
                    break
                prefix = pending[:match.start()]
                if prefix:
                    os.write(1, prefix)
                kind = 'sudo' if match.group() == marker_bytes else 'ssh'
                seen[kind] += 1
                if seen[kind] > 1:
                    raise RuntimeError('PiVault rejected the supplied password. Stopped without another password prompt.')
                if password is None:
                    password = getpass.getpass('PiVault password: ')
                    deadline = time.monotonic() + timeout
                os.write(fd, password.encode() + b'\n')
                pending = pending[match.end():]
            if b'\n' in pending:
                end = pending.rfind(b'\n') + 1
                os.write(1, pending[:end])
                pending = pending[end:]
        if pending:
            os.write(1, pending)
        _, status = os.waitpid(pid, 0)
        if payload and os.waitstatus_to_exitcode(status) == 0 and not payload_sent:
            print(payload[2], file=sys.stderr)
            return 1
    except (KeyboardInterrupt, RuntimeError, EOFError) as error:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        if isinstance(error, RuntimeError):
            print(str(error), file=sys.stderr)
        return 1
    finally:
        password = None
        os.close(fd)
    return os.waitstatus_to_exitcode(status)
