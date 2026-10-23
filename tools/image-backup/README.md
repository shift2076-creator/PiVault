# PiVault image backup

This folder holds a copy of every program (container image) PiVault runs, so the Pi can be rebuilt **without the internet**. Keep it on Greenie, or anywhere with about 3 GB free.

It does **not** contain your data: documents, media, passwords and app databases live in `/srv/pi-server/config/` and on the big USB drive.

## What is here

| File | Purpose |
|---|---|
| `*.tar.gz` | One compressed image per app |
| `images.txt` | The exact images the Pi uses, one per line |
| `docker-compose.yml` | The Pi's live stack file at backup time |
| `backup-images.sh` | Refreshes this folder from the Pi |
| `restore-images.sh` | Loads the images back onto the Pi |

## Refresh the backup (while the internet still exists)

Run after any app upgrade or PiVault install. On **Greenie**:

```sh
bash ~/Desktop/pivault-images/backup-images.sh
```

It stops if an image is pinned by `@sha256` digest; those cannot be restored offline.

## Restore after the Pi lost its apps

1. On **pivault**: make sure Docker is installed and `/home/snx/pi-services/docker-compose.yml` exists. If it is gone, copy the one from this folder there.
2. On **Greenie**, load whatever is missing:

```sh
bash ~/Desktop/pivault-images/restore-images.sh
```

   Use `--all` to reload every image even if the Pi already has it.
3. On **pivault**, start the apps:

```sh
cd /home/snx/pi-services && sudo docker-compose -f docker-compose.yml up -d
```

Both scripts use the `pivault` SSH alias. Set `PIVAULT_HOST` to use a different one.
