import base64
import getpass
import os
import pty
import re
import select
import shlex
import signal
import sys
import termios
import time

REPORT_B64 = '__REPORT_B64__'
MARKER = '__PIVAULT_SUDO_CHECK__'


def main():
    # No password in argv, environment, a file, or the transmitted report source.
    report = base64.b64decode(REPORT_B64).decode()
    command = 'sudo -S -p '+shlex.quote(MARKER)+' python3 -c '+shlex.quote(report)
    args = ['ssh','-T','-o','StrictHostKeyChecking=yes','-o','NumberOfPasswordPrompts=1',
            '-o','KbdInteractiveAuthentication=no','-o','ControlMaster=no',
            '-o','ConnectTimeout=15','snx@192.168.4.90',command]
    pid,fd=pty.fork()
    if pid==0:
        attrs=termios.tcgetattr(0)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(0,termios.TCSANOW,attrs)
        os.execvp('ssh',args)
    password=None
    pending=b''
    seen={'ssh':0,'sudo':0}
    deadline=time.monotonic()+180
    status=None
    try:
        while True:
            if time.monotonic()>deadline:
                raise RuntimeError('Check timed out. No configuration changed.')
            ready,_,_=select.select([fd],[],[],1)
            if not ready:
                continue
            try:
                data=os.read(fd,65536)
            except OSError:
                break
            if not data:
                break
            pending+=data
            if b'Enter passphrase for key' in pending:
                raise RuntimeError('Unlock your SSH key in your SSH agent, then run this check again.')
            while True:
                match=re.search(rb'(?im)([^\r\n]*\bpassword:[ \t]*|'+MARKER.encode()+rb')',pending)
                if not match:
                    break
                prefix=pending[:match.start()]
                if prefix:
                    os.write(1,prefix)
                kind='sudo' if match.group()==MARKER.encode() else 'ssh'
                seen[kind]+=1
                if seen[kind]>1:
                    raise RuntimeError('PiVault rejected the supplied password. Stopped without another password prompt.')
                if password is None:
                    password=getpass.getpass('PiVault password: ')
                    # The human may take time typing; reset the execution deadline.
                    deadline=time.monotonic()+180
                os.write(fd,password.encode()+b'\n')
                pending=pending[match.end():]
            if b'\n' in pending:
                end=pending.rfind(b'\n')+1
                os.write(1,pending[:end])
                pending=pending[end:]
        if pending:
            os.write(1,pending)
        _,status=os.waitpid(pid,0)
    except (KeyboardInterrupt,RuntimeError) as error:
        os.kill(pid,signal.SIGTERM)
        os.waitpid(pid,0)
        if isinstance(error,RuntimeError):
            print(str(error),file=sys.stderr)
        return 1
    finally:
        password=None
        os.close(fd)
    return os.waitstatus_to_exitcode(status)


if __name__=='__main__':
    raise SystemExit(main())
