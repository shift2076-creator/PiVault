"""Installer front end for the Greenie driver: sends the archive after sudo is accepted."""
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
    return drive(args, marker=MARKER, timeout=1800,
        timeout_message='Installation timed out. Review the last printed step before retrying.',
        key_message='Unlock your SSH key in your SSH agent, then run this installer again.',
        # Raw local PTY: the single SSH stdin carries password lines and then the archive.
        # No canonical line-size truncation or terminal echo.
        child_setup=lambda: tty.setraw(0),
        payload=(READY, (PAYLOAD_B64 + '\n').encode(),
                 'The remote installer did not request its archive. Nothing was applied.'))


if __name__ == '__main__':
    raise SystemExit(main())
