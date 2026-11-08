# Launcher (Homepage) assets

The PiVault launcher is Homepage v2.6.0 with two files added to it:

| File | Served as | Installed by |
|---|---|---|
| `custom.css` | `/api/config/custom.css` | Not installed by the installer. Copied to the device by hand (see Deploying). |
| `homepage-addon.js` | Merged into `custom.js` between the installer's `JS_BEGIN` and `JS_END` markers | The installer (`installer/configuration.py`, `homepage_js`). |

The device copies live in `/srv/pi-server/config/homepage/` on pivault, which Homepage reads as `/app/config`. `services.yaml`, `settings.yaml` and `widgets.yaml` are device data and are not in this repository.

## Layout

- Every rule is scoped to `body:has(#pivault-books)`, so nothing else on the page changes.
- Title: an `h1#pv-title` created by `homepage-addon.js`, with the keyhole board mark as a `::before` sized to the text height. Text is 44px, centred.
- Categories: full-width rows, 20px below the title for the first, 48px between categories. Entertainment and Knowledge are placed by the `id`s of their tiles (`pivault-books`, `pivault-documents`) in `services.yaml`.
- Entries: a grid of 4 columns, 2 at 1199px and below, 1 at 767px and below. Gap is one third of `--pv-column-gap`. Tile horizontal padding is 12px.
- Tiles: the panel behind each tile is always visible. Hovering removes its shadow and darkens its top edge (`#10161d`). The shadow is never clipped, and every ancestor of the entry list has `overflow: visible`.
- Width: the content, toolbar and categories share a 1200px maximum.
- Hidden: the Password Manager tile (it is in the Settings menu), and the heading of any group whose only tile is that hidden tile (the `Tools` group in `services.yaml`).

## Favicons

`stack/homepage/favicon/` holds the source and the rasters:

- `favicon.svg`: the 16px circle and keyhole mark. Browsers that take an SVG icon use it at every size.
- `favicon-16.png`, `favicon-32.png`: rendered from the SVG.
- `favicon.ico`: 16, 32 and 48px in one file. Still the square design.
- `apple-touch-icon.png`: 180px. Still the square design.

After changing any of these files, run `python3 tools/favicon_block.py` from the repository root. It rewrites the `FAVICON` block in `homepage-addon.js` between `BEGIN PIVAULT FAVICONS` and `END PIVAULT FAVICONS`. The installer test `test_favicon_block_matches_its_files` fails if the block is out of date.

A web app manifest and a root `/favicon.ico` need a Caddy route. They are not included.

## Tests

- `python3 -m unittest discover -s installer -p 'test_homepage_assets.py'`: static checks for the stylesheet and script. Behaviour in a live Homepage must be checked on the device.
- `node --check stack/homepage-addon.js`.
- The full installer suite, which includes the `custom.js` merge tests: `python3 -m unittest discover -s installer -p 'test*.py'`.

## Deploying

- Stylesheet only, from the deploy machine: fetch the branch into its checkout, check the file's SHA-256, copy it to pivault with `scp`, install it with `sudo install -m 644`, and restart `pivault-homepage`. Keep a backup in `/srv/pi-server/backups/homepage/`, not in the config folder.
- `custom.js` (the favicons and any script change): merge `homepage-addon.js` into the live `custom.js` with `installer/configuration.py` (`homepage_js`), check the merged file, then install it the same way.
- The installer deploys `custom.js` only. Run it from `main`, and only after the branch is merged.

## Lessons

- Homepage draws a group's heading even when all its tiles are hidden.
- A `clip-path` on a pseudo-element clips its box-shadow. Remove it instead of trying to contain the shadow.
- After an edit, read the whole file again. A stale rule that appears later in the file overrides the new one.
- Homepage's group panel has `overflow-hidden`. Shadows at the edge of a tile are clipped unless that panel and each wrapper above the tiles are set to `overflow: visible`.
