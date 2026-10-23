"""Build the complete single-file Greenie installer from maintained sources."""
import base64
import gzip
import hashlib
import io
from pathlib import Path
import shlex
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
# Repository paths the Pi-side installer needs; nothing else is shipped.
PAYLOAD = ('app', 'installer', 'stack/homepage-addon.js', 'jobs/maintenance.py')


def payload_files():
    for entry in PAYLOAD:
        base = ROOT / entry
        if base.is_file():
            yield base
        else:
            yield from base.rglob('*')


def package():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(payload_files()):
            relative = path.relative_to(ROOT)
            if (not path.is_file() or path.is_symlink() or
                any(part in ('data', '__pycache__', '.git') for part in relative.parts) or
                path.suffix in ('.pyc', '.env', '.sqlite3', '.sh', '.zip')):
                continue
            content = path.read_bytes()
            info = tarfile.TarInfo(relative.as_posix())
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(buffer.getvalue(), mtime=0)


def bootstrap(payload):
    digest = hashlib.sha256(payload).hexdigest()
    return '''import base64,hashlib,io,os,pathlib,subprocess,sys,tarfile,tempfile
print('__PIVAULT_INSTALL_PAYLOAD_READY__',flush=True)
encoded=sys.stdin.buffer.readline(16*1024*1024)
payload=base64.b64decode(encoded.strip(),validate=True)
if hashlib.sha256(payload).hexdigest()!=''' + repr(digest) + ''':
    raise SystemExit('Installer payload checksum failed.')
if os.geteuid()!=0:
    raise SystemExit('The installer needs authenticated sudo on PiVault.')
with tempfile.TemporaryDirectory(prefix='pivault-users-install-') as folder:
    root=pathlib.Path(folder)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
        for item in archive:
            name=pathlib.PurePosixPath(item.name)
            if name.is_absolute() or '..' in name.parts or not item.isfile():
                raise SystemExit('Unexpected installer archive member.')
            destination=root.joinpath(*name.parts)
            destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with archive.extractfile(item) as source,destination.open('wb') as target:
                target.write(source.read())
            destination.chmod(0o600)
    result=subprocess.run([sys.executable,str(root/'installer/install.py')])
    raise SystemExit(result.returncode)
'''


def build(destination):
    payload = package()
    code = bootstrap(payload)
    command = 'sudo -S -p __PIVAULT_INSTALL_SUDO__ python3 -c ' + shlex.quote(code)
    if len(command.encode()) > 110000:
        raise ValueError('The SSH command exceeds the safe argument-size limit.')
    relay = (ROOT / 'installer/relay.py').read_text()
    relay = relay.replace("'__REMOTE_COMMAND__'", repr(command))
    relay = relay.replace("'__PAYLOAD_B64__'", repr(base64.b64encode(payload).decode()))
    content = '''#!/usr/bin/env bash
# PiVault account portal installer. Run on Greenie.
# One SSH connection; at most one password prompt for both SSH and sudo.
# Includes the application source; no separate download/unzip step.
# Includes personal vault enrollment and approved local client downloads.
# Other native service integrations and all-service SSO remain unfinished.
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\\n' 'python3 is required on Greenie.' >&2
  exit 1
fi
if ! command -v ssh >/dev/null 2>&1; then
  printf '%s\\n' 'ssh is required on Greenie.' >&2
  exit 1
fi
python3 - <<'PIVAULT_INSTALL_PYTHON'
''' + relay + '\nPIVAULT_INSTALL_PYTHON\n'
    destination.write_text(content)
    destination.chmod(0o755)
    return {'file': str(destination), 'bytes': destination.stat().st_size,
            'sha256': hashlib.sha256(content.encode()).hexdigest()}


if __name__ == '__main__':
    import json
    print(json.dumps(build(Path(sys.argv[1]))))
