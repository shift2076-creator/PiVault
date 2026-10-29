# Unified admin notifications

Implemented in the existing Admin/User backend. The combined Users installer deploys these routes, the shared outbox mount and the launcher bell. Existing launcher CSS is preserved.

## Behavior

- One persistent SQLite inbox for PiVault services. The bell appears at the dashboard's upper right only after the backend authorizes the current Admin session.
- Unread receipts are per admin. Reading an event does not resolve it or hide it from other admins. Resolution is shared, with the acting admin and timestamp retained.
- Movie filesize exceptions include title, year, genre, reference runtime, resolution, published filesize, estimate, accepted interval and source URL.
- **Approve this download** allows that exact film/source/filesize/checksum at the next scheduled round. It does not bypass the storage cap or the daily genre quota.
- **Skip this film** leaves the candidate excluded. The downloader continues to the next film in that genre. A pending review also does not stop other candidates or genres.
- Other service notices use the same inbox and a **Mark resolved** action. Repeated delivery of an incident does not create duplicate notifications.
- Decisions persist before delivery. If writing the decision to the shared folder fails, the UI reports delivery pending and later admin API requests retry it.

## Deployment prerequisites

The combined Users installer handles the following steps. These manual details describe the deployment contract; they are not additional steps required after a successful installer run. All-service SSO remains separate unfinished work.

1. Apply migration `0002_notification_notificationreceipt.py` to the persistent Users database using the backend's established deployment environment.
2. Give the backend read/write access to the same notification folder used by the Bash downloader: `/srv/pi-server/config/notifications` on the Pi host. Set `PIVAULT_NOTIFICATION_ROOT` to its actual mounted path inside the backend. `inbox/` and `decisions/` must persist across restarts and be writable by the existing producer/backend identities; do not expose the folder through a web file server.
3. Route `/notifications`, `/notifications/*`, `/users/*` and `/join` to the same private Users backend over the existing HTTPS origin. Preserve the current certificate data and unrelated service routes. Do not publish the backend's port or expose `/internal/gate` publicly.
4. Enable the updated `homepage-addon.js` through Homepage's existing `custom.js` facility after the routes and authenticated Admin session work. Preserve any current JavaScript when combining it. This addon includes the existing Add User tool and the notification bell; it does not replace the accepted launcher stylesheet.
5. Verify an Admin can read and resolve a test event and an ordinary User receives 403 on those same endpoints. Only then is the dashboard feature live.

The combined cinema package stores events immediately even if no browser is open. Those events remain pending until an admin opens the deployed dashboard/API. This source checkpoint does not itself alter the Pi's current Caddy or Compose configuration.

## Shared producer entry point

Existing service integrations can publish through the backend function `portal.notifications.notify` or its local command. Run commands inside the established backend environment, using an incident-specific key:

```sh
python manage.py notify --source documents --key import-20261021-42 \
  --level warning --title 'Document import needs review' \
  --message 'The source file could not be imported.'
```

This is a local producer API, not a publicly writable HTTP endpoint. The movie producer uses the versioned filesystem outbox so its Bash downloader does not need Django or an admin password. No new health polling, email, push service or external notification account is added.

## Endpoints

All endpoints below require a current, active Admin session on the server. Client-supplied role headers do not grant access. POSTs require Django CSRF protection.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/notifications/api` | GET | Import queued events; return pending items and unread/open counts |
| `/notifications/seen` | POST | Acknowledge reading for the current admin only |
| `/notifications` | GET | Pending inbox and recent resolution history |
| `/notifications/<id>` | GET | Review details |
| `/notifications/<id>/resolve` | POST | Save the permitted decision |
| `/notifications/bell.js` | GET | Shared admin bell component |

## Checks

Run `python manage.py test portal` and `python manage.py check` using the existing requirements and a development-only secret. The notification tests cover permissions, CSRF, separate unread receipts, deduplication, competing admin decisions, durable delivery retry, source validation and escaped content.

## Approved local client releases

`clients.update` events use the same Admin inbox, role checks, CSRF protection, unread receipts and resolution history. Approve update queues the exact release until nightly maintenance. Skip this version resolves it without a download. An approval stays open until the original package passes size, SHA-256 and archive checks and is atomically published. Failures retain the current local copy and notify Admins. The Add User page displays current/latest versions, last check time and review links.

`pivault-maintenance.timer` runs at 2:00 AM America/New_York, without catch-up runs. Its coordinator applies previously approved client copies, checks for new releases, then starts the existing cinema service when video scheduling was already enabled. A client-check failure does not suppress the cinema step. Disabled cinema scheduling stays disabled. The existing cinema filesize approvals, quota and cap are unchanged. No automatic OS, container or device-app upgrades are included.

## Maintained copies

- The admin inbox bell is implemented twice: `app/portal/static/portal/notifications-bell.js` for portal pages and the bell section of `stack/homepage-addon.js` for the Homepage launcher. Each change must land in both.
- Atomic JSON inbox writes are repeated in `app/portal/notifications.py`, `jobs/maintenance.py` and `jobs/cinema/cinema-notifications.py`. The jobs run standalone under systemd and cannot import portal code.
- Event IDs are derived per producer. Maintenance events use `sha256("maintenance:" + key)`. The portal checks an event ID against its details only for `cinema.filesize` events (`app/portal/notifications.py`), so the other producers' IDs are accepted as written.
