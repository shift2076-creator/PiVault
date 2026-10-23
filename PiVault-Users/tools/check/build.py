"""Build the single-file Greenie launcher from the maintained checker sources."""
import base64
from pathlib import Path
import sys

root=Path(__file__).parent
destination=Path(sys.argv[1])
report=(root/'report.py').read_bytes()
relay=(root/'relay.py').read_text().replace('__REPORT_B64__',base64.b64encode(report).decode())
script='''#!/usr/bin/env bash
# Read-only PiVault user-integration check. Run from Greenie.
# One SSH connection; at most one local password prompt.
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\\n' 'python3 is required on Greenie. No configuration changed.' >&2
  exit 1
fi
python3 - <<'PIVAULT_CHECK_PYTHON'
'''+relay+'\nPIVAULT_CHECK_PYTHON\n'
destination.write_text(script)
destination.chmod(0o755)
