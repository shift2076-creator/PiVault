"""Read and write the vCard 3.0 fields that the contacts editor shows.

Properties the editor does not manage are returned as raw lines and written back
unchanged, so saving a contact never discards data the editor does not understand.
"""
import re

ADDRESS_FIELDS = ('street', 'city', 'region', 'postal', 'country')
REGENERATED = {'BEGIN', 'END', 'VERSION', 'PRODID', 'REV', 'UID'}
TYPE_TOKEN = re.compile(r'[^A-Za-z0-9,-]')


def unfold(text):
    lines = []
    for raw in text.replace('\r\n', '\n').split('\n'):
        if raw.startswith((' ', '\t')) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def escape(value):
    return (str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\n').replace('\n', '\\n'))


def unescape(value):
    out, index = [], 0
    while index < len(value):
        char = value[index]
        if char == '\\' and index + 1 < len(value):
            following = value[index + 1]
            out.append('\n' if following in 'nN' else following)
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def split_fields(value):
    """Split a structured value on unescaped semicolons and unescape each field."""
    fields, current, index = [], [], 0
    while index < len(value):
        char = value[index]
        if char == '\\' and index + 1 < len(value):
            current.append(value[index:index + 2])
            index += 2
            continue
        if char == ';':
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)
        index += 1
    fields.append(''.join(current))
    return [unescape(field) for field in fields]


def fold(line):
    """Fold a content line at 75 octets, as RFC 6350 requires."""
    chunks, current, size, limit = [], '', 0, 75
    for char in line:
        width = len(char.encode('utf-8'))
        if size + width > limit:
            chunks.append(current)
            current, size, limit = char, width, 74
        else:
            current += char
            size += width
    chunks.append(current)
    return '\r\n '.join(chunks)


def _types(params):
    types = []
    for param in params:
        key, sep, value = param.partition('=')
        if sep and key.upper() == 'TYPE':
            types.extend(part.strip().lower() for part in value.split(',') if part.strip())
        elif not sep and param.strip():
            types.append(param.strip().lower())
    return ','.join(types)


def _type_param(kind):
    kind = TYPE_TOKEN.sub('', kind or '')
    return f';TYPE={kind}' if kind else ''


def parse(text):
    """Return (contact, extra_lines) for one vCard."""
    contact = {'uid': '', 'full_name': '', 'given': '', 'family': '', 'name_rest': [],
               'org': '', 'org_units': [], 'title': '', 'url': '', 'birthday': '', 'note': '',
               'emails': [], 'phones': [], 'address': {field: '' for field in ADDRESS_FIELDS},
               'adr_head': []}
    extra, address_seen = [], False
    for line in unfold(text):
        head, _, value = line.partition(':')
        name, *params = head.split(';')
        name = name.upper()
        if name in REGENERATED:
            if name == 'UID':
                contact['uid'] = unescape(value)
            continue
        if name == 'FN':
            contact['full_name'] = unescape(value)
        elif name == 'N':
            names = split_fields(value) + ['', '']
            contact['family'], contact['given'] = names[0], names[1]
            contact['name_rest'] = names[2:]
        elif name == 'ORG':
            parts = split_fields(value) + ['']
            contact['org'], contact['org_units'] = parts[0], parts[1:]
        elif name == 'TITLE':
            contact['title'] = unescape(value)
        elif name == 'URL':
            contact['url'] = unescape(value)
        elif name == 'BDAY':
            contact['birthday'] = value.strip()
        elif name == 'NOTE':
            contact['note'] = unescape(value)
        elif name == 'EMAIL':
            contact['emails'].append({'type': _types(params), 'value': unescape(value)})
        elif name == 'TEL':
            contact['phones'].append({'type': _types(params), 'value': unescape(value)})
        elif name == 'ADR' and not address_seen:
            parts = split_fields(value) + [''] * 7
            contact['adr_head'] = parts[0:2]
            contact['address'] = dict(zip(ADDRESS_FIELDS, parts[2:7]))
            address_seen = True
        else:
            extra.append(line)
    return contact, extra


def _first_email(contact):
    for entry in contact.get('emails', []):
        if entry.get('value'):
            return entry['value']
    return ''


def serialize(contact, extra, rev):
    """Return the vCard text for a contact, followed by its preserved extra lines."""
    given, family = contact.get('given', ''), contact.get('family', '')
    display = ' '.join(part for part in (given, family) if part)
    full_name = (display or contact.get('full_name', '') or contact.get('org', '')
                 or _first_email(contact) or 'Unnamed')
    names = [family, given] + list(contact.get('name_rest', []))
    names += [''] * (5 - len(names))
    org = [contact.get('org', '')] + list(contact.get('org_units', []))
    lines = ['BEGIN:VCARD', 'VERSION:3.0', 'PRODID:-//PiVault//Contacts//EN',
             'UID:' + escape(contact.get('uid', '')), 'REV:' + rev,
             'N:' + ';'.join(escape(part) for part in names),
             'FN:' + escape(full_name)]
    if any(org):
        lines.append('ORG:' + ';'.join(escape(part) for part in org))
    if contact.get('title'):
        lines.append('TITLE:' + escape(contact['title']))
    for entry in contact.get('emails', []):
        if entry.get('value'):
            lines.append('EMAIL' + _type_param(entry.get('type')) + ':' + escape(entry['value']))
    for entry in contact.get('phones', []):
        if entry.get('value'):
            lines.append('TEL' + _type_param(entry.get('type')) + ':' + escape(entry['value']))
    address = contact.get('address') or {}
    if any(address.get(field) for field in ADDRESS_FIELDS):
        head = (list(contact.get('adr_head', [])) + ['', ''])[:2]
        parts = head + [address.get(field, '') for field in ADDRESS_FIELDS]
        lines.append('ADR:' + ';'.join(escape(part) for part in parts))
    if contact.get('url'):
        lines.append('URL:' + escape(contact['url']))
    if contact.get('birthday'):
        lines.append('BDAY:' + escape(contact['birthday']))
    if contact.get('note'):
        lines.append('NOTE:' + escape(contact['note']))
    lines.extend(extra)
    lines.append('END:VCARD')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'
