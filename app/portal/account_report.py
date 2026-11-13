"""Read-only account report: every native account in every configured service, checked against PiVault users.

Prints account names only. Passwords, tokens, keys and password hashes are never read into the report.
"""
from .models import User
from .services import ADAPTERS, SERVICES, is_open


def _norm(name):
    return str(name).strip().casefold()


def _describe(error):
    messages = getattr(error, 'messages', None)
    return ' '.join(messages) if messages else (str(error) or type(error).__name__)


def _read_service(key):
    try:
        return {'accounts': [str(name) for name in ADAPTERS[key].list_accounts()], 'error': None}
    except Exception as error:
        return {'accounts': [], 'error': _describe(error)}


def build_report():
    users = list(User.objects.order_by('email'))
    services = []
    for key, label in SERVICES:
        section = {'key': key, 'label': label, 'accounts': [], 'error': None, 'note': None}
        if key in ADAPTERS:
            section.update(_read_service(key))
        elif is_open(key):
            section['note'] = 'no per-user accounts'
        else:
            section['note'] = 'not configured'
        services.append(section)

    problems = []
    for section in services:
        key, label = section['key'], section['label']
        if key not in ADAPTERS or section['error'] is not None:
            continue
        adapter = ADAPTERS[key]
        native = {_norm(name) for name in section['accounts']}
        expected = set()
        for user in users:
            if user.service_access.get(key, 'none') == 'none':
                continue
            name = adapter.credential_username(user)
            if name is None:
                continue
            expected.add(_norm(name))
            if _norm(name) not in native:
                problems.append(f'MISSING  {label}: PiVault user {user.email} expects an account named {name}')
        for account in section['accounts']:
            if _norm(account) not in expected:
                problems.append(f'UNMATCHED  {label}: account {account} matches no PiVault user')
    return {'users': users, 'services': services, 'problems': problems}


def format_report(report):
    lines = [f"PiVault users ({len(report['users'])})"]
    for user in report['users']:
        modes = ', '.join(f'{key}={mode}' for key, mode in sorted(user.service_access.items()) if mode != 'none')
        lines.append(f'  {user.email}  [{user.role}]  {modes or "no services"}')
    lines.append('')
    for section in report['services']:
        head = f"{section['label']} ({section['key']})"
        if section['note']:
            lines.append(f"{head}: {section['note']}")
        elif section['error']:
            lines.append(f"{head}: COULD NOT READ ({section['error']})")
        else:
            lines.append(f"{head}: {len(section['accounts'])} account(s)")
            lines.extend(f'  {name}' for name in section['accounts'])
    lines.append('')
    lines.append(f"Cross-check: {len(report['problems'])} problem(s)")
    lines.extend(f'  {problem}' for problem in report['problems'])
    return '\n'.join(lines)
