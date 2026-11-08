"""PiVault's HTTPS tools, as listed on the Settings page.

Every address is served by the same Caddy with the same local certificate authority, so one trusted root covers
all of them. Each port must be a Caddy site in installer/configuration.py (APP_SITES), or the launcher (8443) or
the vault (443). installer/test_install.py checks that.
"""

TOOLS = (
    ('Home launcher', 8443, '/'),
    ('Password Manager', 443, '/'),
    ('Music', 8443, '/music/'),
    ('Books', 8083, '/'),
    ('Offline Wiki', 8084, '/'),
    ('Documents', 8085, '/'),
    ('Inventory', 8087, '/'),
    ('Pantry', 9283, '/'),
    ('Photos', 8446, '/'),
    ('Videos', 8096, '/'),
    ('Arcade', 8000, '/'),
    ('Contacts', 8089, '/'),
    ('Calendar and contacts sync (Radicale)', 5232, '/'),
)


def tool_links(host):
    # 443 is the browser default, so it is left out of the address.
    return [(name, f'https://{host}' + ('' if port == 443 else f':{port}') + path) for name, port, path in TOOLS]
