#!/usr/bin/env python3
"""Embed the PiVault favicon files in stack/homepage-addon.js as data URIs.

Reads stack/homepage/favicon/ (favicon.svg, favicon-16.png, favicon-32.png,
favicon.ico, apple-touch-icon.png) and writes them between the BEGIN and END
markers in the launcher script. Standard library only. From the repository root:

    python3 tools/favicon_block.py
"""
import base64
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAVICON_DIR = ROOT / 'stack/homepage/favicon'
ADDON = ROOT / 'stack/homepage-addon.js'
BEGIN = '/* BEGIN PIVAULT FAVICONS */'
END = '/* END PIVAULT FAVICONS */'
SOURCES = (
    ('svg', 'favicon.svg', 'image/svg+xml'),
    ('png16', 'favicon-16.png', 'image/png'),
    ('png32', 'favicon-32.png', 'image/png'),
    ('ico', 'favicon.ico', 'image/x-icon'),
    ('apple', 'apple-touch-icon.png', 'image/png'),
)


def render_block():
    lines = [BEGIN, '  const FAVICON = {']
    for key, name, mime in SOURCES:
        encoded = base64.b64encode((FAVICON_DIR / name).read_bytes()).decode()
        lines.append(f"    {key}: 'data:{mime};base64,{encoded}',")
    lines += ['  };', '  ' + END]
    return '\n'.join(lines)


def splice(text):
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[:start] + render_block() + text[end:]


if __name__ == '__main__':
    ADDON.write_text(splice(ADDON.read_text()))
    print('wrote favicon block to', ADDON)
