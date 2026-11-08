# Unified search

The launcher's search field opens `/search`, a portal page that queries each ticked app on the private Docker network and lists every result on one page. Clicking a result opens that item in its own app.

## Who sees what

- Only signed-in portal users can search.
- Videos and Music have per-user portal accounts: a user sees their tickbox only with access to that app. Wiki, Books and Documents have no per-user portal accounts, so every signed-in user sees them.
- Unticked apps are never queried.
- Each app is searched with one read-only search credential. Results are exactly what that credential can see, so give it only content every user with access to that app already shares.

## Configuration

Add an optional `search` object to `/srv/pi-server/config/users/native.json`. Each app is enabled only when present. `origin` is the private address; `link` is the address browsers use for result links.

```json
"search": {
  "wiki":      {"origin": "http://kiwix:8080",       "link": "http://192.168.4.90:8084"},
  "videos":    {"link": "http://192.168.4.90:8096"},
  "music":     {"link": "http://192.168.4.90:4533/music"},
  "books":     {"origin": "http://calibre-web:8083", "link": "http://192.168.4.90:8083", "username": "…", "password": "…"},
  "documents": {"origin": "http://paperless:8000",   "link": "http://192.168.4.90:8085", "token": "…"}
}
```

| App | Upstream API | Credential |
|---|---|---|
| Offline Wiki | kiwix-serve `/catalog/v2/entries`, then `/search?format=xml` once per ZIM language | none |
| Videos | Jellyfin `/Search/Hints` | defaults to the enrollment `jellyfin.api_key` |
| Music | Navidrome Subsonic `search3`, limited to `library_ids` | defaults to the enrollment `navidrome` account; token auth |
| Books | Calibre-Web `/opds/search` | a read-only Calibre-Web user |
| Documents | Paperless-ngx `/api/documents/?query=` | token of a user who can view only shared documents |

### Books account

Calibre-Web OPDS uses HTTP Basic auth unless anonymous browsing is on (it is off), and applies the account's language and tag filters to search results. Create the search user under Admin → Users → Add New User with every permission unticked and **Show Books in Language: Show All**, and leave allowed/denied tags empty.

Restart `pivault-users` after editing `native.json`.
