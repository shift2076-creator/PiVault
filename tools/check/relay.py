"""Checker front end for the Greenie driver: read-only report, no archive."""
import base64
import shlex
import termios

REPORT_B64 = '__REPORT_B64__'
MARKER = '__PIVAULT_SUDO_CHECK__'


def disable_echo():
    attrs = termios.tcgetattr(0)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL)
    termios.tcsetattr(0, termios.TCSANOW, attrs)


def main():
    # No password in argv, environment, a file, or the transmitted report source.
    report = base64.b64decode(REPORT_B64).decode()
    command = 'sudo -S -p ' + shlex.quote(MARKER) + ' python3 -c ' + shlex.quote(report)
    args = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'NumberOfPasswordPrompts=1',
            '-o', 'KbdInteractiveAuthentication=no', '-o', 'ControlMaster=no',
            '-o', 'ConnectTimeout=15', 'snx@192.168.4.90', command]
    return drive(args, marker=MARKER, timeout=180,
        timeout_message='Check timed out. No configuration changed.',
        key_message='Unlock your SSH key in your SSH agent, then run this check again.',
        child_setup=disable_echo)


if __name__ == '__main__':
    raise SystemExit(main())
