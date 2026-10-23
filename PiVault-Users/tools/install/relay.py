"""One SSH process; a single local password reused for SSH and sudo."""
import getpass
import os
import pty
import re
import select
import signal
import sys
import termios
import time
import tty

REMOTE_COMMAND = '__REMOTE_COMMAND__'
PAYLOAD_B64 = '__PAYLOAD_B64__'
MARKER = '__PIVAULT_INSTALL_SUDO__'
READY = '__PIVAULT_INSTALL_PAYLOAD_READY__'


def main():
    args = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'NumberOfPasswordPrompts=1', '-o', 'KbdInteractiveAuthentication=no',
        '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=15',
        '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
        'snx@192.168.4.90', REMOTE_COMMAND]
    pid, fd = pty.fork()
    if pid == 0:
        # Raw local PTY: the single SSH stdin carries password lines and then
        # the archive. No canonical line-size truncation or terminal echo.
        tty.setraw(0)
        os.execvp('ssh', args)
    password = None
    pending = b''
    seen = {'ssh': 0, 'sudo': 0}
    deadline = time.monotonic() + 1800
    status = None
    payload_sent = False
    try:
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError('Installation timed out. Review the last printed step before retrying.')
            ready, _, _ = select.select([fd], [], [], 1)
            if not ready:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            pending += data
            if b'Enter passphrase for key' in pending:
                raise RuntimeError('Unlock your SSH key in your SSH agent, then run this installer again.')
            if READY.encode() in pending:
                if payload_sent:
                    raise RuntimeError('The remote installer requested its archive twice.')
                start = pending.index(READY.encode())
                if start:
                    os.write(1, pending[:start])
                pending = pending[start + len(READY):]
                payload = (PAYLOAD_B64 + '\n').encode()
                offset = 0
                while offset < len(payload):
                    offset += os.write(fd, payload[offset:offset + 16384])
                payload_sent = True
            while True:
                match = re.search(rb'(?im)([^\r\n]*\bpassword:[ \t]*|' + MARKER.encode() + rb')', pending)
                if not match:
                    break
                prefix = pending[:match.start()]
                if prefix:
                    os.write(1, prefix)
                kind = 'sudo' if match.group() == MARKER.encode() else 'ssh'
                seen[kind] += 1
                if seen[kind] > 1:
                    raise RuntimeError('PiVault rejected the supplied password. Stopped without another password prompt.')
                if password is None:
                    password = getpass.getpass('PiVault password: ')
                    deadline = time.monotonic() + 1800
                os.write(fd, password.encode() + b'\n')
                pending = pending[match.end():]
            if b'\n' in pending:
                end = pending.rfind(b'\n') + 1
                os.write(1, pending[:end])
                pending = pending[end:]
        if pending:
            os.write(1, pending)
        _, status = os.waitpid(pid, 0)
        if os.waitstatus_to_exitcode(status) == 0 and not payload_sent:
            print('The remote installer did not request its archive. Nothing was applied.', file=sys.stderr)
            return 1
    except (KeyboardInterrupt, RuntimeError, EOFError) as error:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        if not isinstance(error, KeyboardInterrupt):
            print(str(error), file=sys.stderr)
        return 1
    finally:
        password = None
        os.close(fd)
    return os.waitstatus_to_exitcode(status)


if __name__ == '__main__':
    raise SystemExit(main())
