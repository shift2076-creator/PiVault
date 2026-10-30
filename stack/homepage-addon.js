/* Add after the existing custom.js. The API must be installed before enabling. */
/* Shared top-right toolbar: the notification bell, then the settings gear to its right. */
window.piVaultToolbar = window.piVaultToolbar || (() => {
  let bar = document.getElementById('pv-toolbar');
  if (bar?.isConnected) return bar;
  bar = document.createElement('div');
  bar.id = 'pv-toolbar';
  bar.style.cssText = 'position:fixed;top:clamp(20px,3vw,40px);right:max(var(--pv-gutter,24px),calc((100vw - 1120px) / 2 + var(--pv-gutter,24px)));z-index:1000;display:flex;gap:20px;align-items:center;font-size:14px';
  document.body.append(bar);
  return bar;
});

/* Settings menu: Password Manager for everyone, plus Add User, System and Radicale for Admins.
   The server, not the browser, enforces Admin access on /users/add and /system. */
(() => {
  if (window.piVaultUsersLoaded) return;
  window.piVaultUsersLoaded = true;
  let root, button, panel, add, sys, rad, opened = false, admin = false;
  const ns = 'http://www.w3.org/2000/svg';
  // Gear, user-plus, refresh and lock outlines from Feather Icons (MIT License, compatible with this project).
  const GEAR = 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z';
  const USER_PLUS = 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6M17 11h6M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0';
  const LOCK = 'M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM7 11V7a5 5 0 0 1 10 0v4';
  const CALENDAR = 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z';
  // These apps leave the launcher columns for this menu; their links come from their tiles.
  const MOVED = [['li.service[data-name="Password Manager"]', 'Password Manager', LOCK]];
  // Hide the moved tiles from the launcher. Each tile stays in the page so its link can still be copied.
  const hide = document.createElement('style');
  hide.id = 'pv-moved';
  hide.textContent = `${MOVED.map(([selector]) => selector).join(',')}{display:none!important}`;
  document.head.append(hide);
  function icon(d, size) {
    const svg = document.createElementNS(ns, 'svg');
    for (const [name, value] of Object.entries({viewBox:'0 0 24 24', width:size, height:size, fill:'none',
      stroke:'currentColor', 'stroke-width':'1.7', 'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true'}))
      svg.setAttribute(name, value);
    const path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); svg.append(path);
    return svg;
  }
  function close() {
    if (!root) return;
    opened = false; panel.hidden = true; button.setAttribute('aria-expanded', 'false');
  }
  function mount() {
    if (root?.isConnected) return;
    root = document.createElement('div'); root.id = 'pv-settings';
    const style = document.createElement('style');
    style.textContent = `
      #pv-settings{position:relative;order:2;color:var(--pv-text,#e8edf2);font:inherit;font-size:16px;line-height:1.45}
      #pv-settings [hidden]{display:none!important}
      #pv-settings .pv-gear{display:inline-flex;align-items:center;height:auto;padding:0;border:0;background:transparent;color:var(--pv-text,#e7ecf2);font:inherit;font-size:14px;cursor:pointer}
      #pv-settings .pv-gear:hover,#pv-settings .pv-gear[aria-expanded=true]{text-decoration:underline;text-underline-offset:4px}
      #pv-settings .pv-menu{position:absolute;right:0;top:34px;min-width:240px;padding:8px;border:1px solid var(--pv-menu-border,#3a4553);border-radius:.5em;background:var(--pv-surface,#1c222b);box-shadow:0 10px 30px #0005}
      #pv-settings h2{margin:6px 10px 8px;font-size:12px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--pv-secondary,#91acc3)}
      #pv-settings a{display:flex;align-items:center;gap:12px;min-height:44px;padding:0 10px;border-radius:.4em;color:var(--pv-text,#e8edf2);text-decoration:none}
      #pv-settings a:hover{background:rgba(151,170,190,.1)}
      #pv-settings a svg{color:var(--pv-secondary,#a3afbe)}
      #pv-settings .pv-gear:focus-visible,#pv-settings a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:3px}
    `;
    button = document.createElement('button');
    button.type = 'button'; button.className = 'pv-gear';
    button.setAttribute('aria-label', 'Settings'); button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'pv-settings-menu');
    button.textContent = 'Settings';
    panel = document.createElement('nav'); panel.id = 'pv-settings-menu'; panel.className = 'pv-menu';
    panel.setAttribute('aria-label', 'Settings'); panel.hidden = true;
    const heading = document.createElement('h2'); heading.textContent = 'Settings';
    panel.append(heading);
    for (const [selector, label, path] of MOVED) {
      const link = document.createElement('a'); link.dataset.source = selector;
      link.append(icon(path, 20), document.createTextNode(label));
      panel.append(link);
    }
    add = document.createElement('a'); add.href = '/users/add'; add.hidden = !admin;
    add.append(icon(USER_PLUS, 20), document.createTextNode('Add User'));
    panel.append(add);
    sys = document.createElement('a'); sys.href = '/system'; sys.hidden = !admin;
    sys.append(icon(GEAR, 20), document.createTextNode('System'));
    panel.append(sys);
    // Radicale serves plain HTTP on port 5232 at the launcher's address; the launcher itself is HTTPS.
    rad = document.createElement('a'); rad.href = `http://${location.hostname}:5232/`; rad.hidden = !admin;
    rad.append(icon(CALENDAR, 20), document.createTextNode('Radicale'));
    panel.append(rad);
    root.append(style, button, panel);
    window.piVaultToolbar().append(root);
    button.addEventListener('click', () => {
      if (opened) { close(); return; }
      links();
      opened = true; panel.hidden = false; button.setAttribute('aria-expanded', 'true');
    });
    links();
  }
  function links() {
    // Copy each app's address (and new-tab choice) from its launcher tile.
    for (const link of panel.querySelectorAll('a[data-source]')) {
      const tile = document.querySelector(link.dataset.source)?.querySelector('a[href]');
      link.hidden = !tile;
      if (!tile) continue;
      link.href = tile.href;
      if (tile.target) { link.target = tile.target; link.rel = 'noopener noreferrer'; }
    }
  }
  async function refresh() {
    admin = false;
    try {
      const response = await fetch('/users/session', {credentials:'same-origin', cache:'no-store'});
      const state = response.ok ? await response.json() : {};
      admin = state.authenticated === true && state.admin === true;
    } catch { admin = false; }
    mount();
    if (add) add.hidden = !admin;
    if (sys) sys.hidden = !admin;
    if (rad) rad.hidden = !admin;
  }
  document.addEventListener('click', event => { if (root && !root.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) { close(); button?.focus(); } });
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh, {once:true});
  else refresh();
})();

/* Keep this bell in sync with app/portal/static/portal/notifications-bell.js, which serves the portal pages.
   The same admin inbox is implemented in both places, so each change must land in both. */
/* PiVault's shared admin inbox. The server, not the browser, enforces Admin access. */
(() => {
  if (window.piVaultNotificationsLoaded) return;
  window.piVaultNotificationsLoaded = true;
  let root, button, badge, panel, list, summary, state, busy = false, opened = false;
  const make = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  function hide() {
    root?.remove(); root = null; state = null; opened = false;
  }
  function close() {
    if (!root) return;
    opened = false; panel.hidden = true; button.setAttribute('aria-expanded', 'false');
  }
  function mount() {
    if (root?.isConnected) return;
    root = make('div'); root.id = 'pv-admin-notifications';
    const style = make('style');
    style.textContent = `
      #pv-admin-notifications{position:relative;order:1;color:var(--pv-text,#e7ecf2);font:14px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
      #pv-admin-notifications [hidden]{display:none!important}
      #pv-admin-notifications .pv-bell{position:relative;display:inline-flex;align-items:center;gap:8px;height:auto;padding:0;border:0;background:transparent;color:var(--pv-text,#e7ecf2);font:inherit;font-size:14px;cursor:pointer}
      #pv-admin-notifications .pv-bell:hover{text-decoration:underline;text-underline-offset:4px}
      #pv-admin-notifications .pv-bell:focus-visible,#pv-admin-notifications a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:4px}
      #pv-admin-notifications .pv-count{min-width:20px;padding:1px 6px;border-radius:999px;background:var(--pv-accent,#6cc0e6);color:var(--pv-detail,#1c222b);font-size:12px;font-weight:600;text-align:center}
      #pv-admin-notifications .pv-panel{position:absolute;right:0;top:34px;width:min(440px,calc(100vw - 48px));max-height:min(620px,calc(100dvh - 100px));overflow:auto;padding:22px;border:1px solid var(--pv-menu-border,#3a4553);border-radius:.5em;background:var(--pv-surface,#1c222b);box-shadow:0 10px 30px #0005}
      #pv-admin-notifications h2{font-size:17px;margin:0 0 4px;color:var(--pv-accent,#b4c0ce)}
      #pv-admin-notifications .pv-summary,#pv-admin-notifications time{color:var(--pv-secondary,#91acc3);font-size:12px}
      #pv-admin-notifications ol{list-style:none;margin:14px 0;padding:0}
      #pv-admin-notifications li{border-top:1px solid var(--pv-divider,rgba(151,170,190,.48));padding:15px 0}
      #pv-admin-notifications p{margin:7px 0;overflow-wrap:anywhere}
      #pv-admin-notifications a{color:var(--pv-secondary,#91acc3);text-decoration:none}
      #pv-admin-notifications a:hover{color:var(--pv-text,#e8edf2)}
    `;
    button = make('button', undefined, 'pv-bell');
    button.type = 'button'; button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'pv-notification-panel');
    badge = make('span', '', 'pv-count'); badge.hidden = true;
    button.append(make('span', 'Notices'), badge);
    panel = make('section', undefined, 'pv-panel'); panel.id = 'pv-notification-panel'; panel.hidden = true;
    panel.setAttribute('aria-label', 'Admin notifications');
    const heading = make('h2','Admin notifications');
    summary = make('div','', 'pv-summary'); list = make('ol');
    const all = make('a','All notifications'); all.href = '/notifications';
    panel.append(heading,summary,list,all); root.append(style,button,panel); window.piVaultToolbar().append(root);
    button.addEventListener('click', async () => {
      if (opened) { close(); return; }
      opened = true; panel.hidden = false; button.setAttribute('aria-expanded', 'true');
      await refresh();
      if (!state || !opened || !state.items.length) return;
      const body = new URLSearchParams(); state.items.forEach(item => body.append('ids',item.id));
      try {
        const response = await fetch('/notifications/seen', {method:'POST',credentials:'same-origin',
          headers:{'X-CSRFToken':state.csrf_token}, body});
        if (response.ok) await refresh();
      } catch { /* Keep the saved events visible; no local-only read acknowledgement. */ }
    });
  }
  function draw() {
    if (!state) return;
    mount();
    badge.textContent = state.unread_count > 99 ? '99+' : String(state.unread_count);
    badge.hidden = state.unread_count === 0;
    button.setAttribute('aria-label', `Admin notifications: ${state.unread_count} unread, ${state.open_count} open`);
    summary.textContent = `${state.open_count} open · ${state.unread_count} unread`;
    list.replaceChildren();
    if (!state.items.length) list.append(make('li','No open notifications.'));
    for (const item of state.items) {
      const row = make('li'); const link = make('a',item.title);
      // The server only issues same-origin detail paths; keep that invariant in the UI.
      link.href = `/notifications/${encodeURIComponent(item.id)}`;
      const time = make('time', `${item.source} · ${new Date(item.created_at).toLocaleString()}`);
      time.dateTime = item.created_at; time.style.display = 'block';
      row.append(link,time,make('p',item.message));
      if (item.pending_delivery) row.append(make('p','Decision saved; delivery pending.','pv-summary'));
      list.append(row);
    }
  }
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch('/notifications/api',{credentials:'same-origin',cache:'no-store'});
      if (!response.ok) { hide(); return; }
      state = await response.json(); draw();
    } catch { /* A network interruption must not erase already displayed notifications. */ }
    finally { busy = false; }
  }
  document.addEventListener('click', event => { if (root && !root.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) { close(); button?.focus(); } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('pageshow',refresh);
  function start() { refresh(); window.setInterval(refresh,60000); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();

/* Unified search: the H3 field under the PiVault title, with a scope control beneath it.
   "All" searches every app this user may search; each other segment searches one app. The form
   sends the apps for the chosen scope as GET parameters, so results load without script. Segments
   appear only for apps this user may search. "/" moves focus to the field. */
(() => {
  if (window.piVaultSearchLoaded) return;
  window.piVaultSearchLoaded = true;
  try { localStorage.removeItem('pivault-search-unticked'); } catch { /* Old per-browser choice; nothing else to clear. */ }
  const SCOPES = [['All', null], ['Books', 'books'], ['Music', 'music'], ['Offline Wiki', 'wiki']];
  const L = '#layout-groups#layout-groups#layout-groups';
  const style = document.createElement('style');
  style.id = 'pv-search-style';
  // The repeated ID outranks the launcher CSS; the field and scope control sit in the "search" grid area.
  style.textContent = `
    ${L} > #pv-search{grid-area:search;display:flex;flex-direction:column;align-items:center;gap:14px;width:100%;margin:12px 0 0;
      color:var(--pv-text,#e7ecf2);font:inherit;font-size:16px;z-index:1}
    #pv-search .pv-field{display:flex;align-items:center;gap:12px;width:100%;max-width:720px;height:64px;box-sizing:border-box;
      padding:0 14px 0 20px;border:1px solid var(--pv-border,#2c3542);border-radius:16px;background:var(--pv-surface,#1c222b);box-shadow:0 10px 30px rgba(0,0,0,.35)}
    #pv-search .pv-field:focus-within{border-color:var(--pv-secondary,#a3afbe)}
    #pv-search .pv-field svg{flex:none;width:20px;height:20px;color:var(--pv-secondary,#a3afbe)}
    #pv-search input[type=search]{flex:1;min-width:0;height:100%;padding:0;border:0;background:transparent;box-shadow:none;
      color:var(--pv-text,#e7ecf2);font:inherit;font-size:18px;outline:none}
    #pv-search input[type=search]::placeholder{color:var(--pv-secondary,#a3afbe);opacity:1}
    #pv-search kbd{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;padding:3px 8px;border:1px solid var(--pv-border,#2c3542);
      border-radius:6px;color:var(--pv-secondary,#a3afbe)}
    #pv-search .pv-scopes{display:inline-flex;flex-wrap:wrap;gap:4px;padding:4px;border-radius:12px;background:var(--pv-chip,#252d39)}
    #pv-search .pv-scope{padding:7px 16px;border:0;border-radius:9px;background:transparent;color:var(--pv-secondary,#a3afbe);font:inherit;font-size:14px;cursor:pointer}
    #pv-search .pv-scope[aria-pressed=true]{background:var(--pv-surface,#1c222b);color:var(--pv-text,#e7ecf2);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.4)}
    #pv-search :focus-visible{outline:2px solid var(--pv-accent,#6cc0e6);outline-offset:3px}
  `;
  const make = (tag, props = {}) => Object.assign(document.createElement(tag), props);
  let apps = [], bar = null, scope = SCOPES[0];
  function mount() {
    const host = document.getElementById('layout-groups');
    if (!host || bar?.isConnected) return;
    if (!style.isConnected) document.head.append(style);
    bar = make('form', {id: 'pv-search', method: 'get', action: '/search', role: 'search'});
    const field = make('label', {className: 'pv-field'});
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    for (const [k, v] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'1.8', 'stroke-linecap':'round', 'aria-hidden':'true'})) svg.setAttribute(k, v);
    const lens = document.createElementNS(ns, 'path'); lens.setAttribute('d', 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4'); svg.append(lens);
    const input = make('input', {type: 'search', name: 'q', required: true, minLength: 2, maxLength: 200,
      autocomplete: 'off', placeholder: 'Search PiVault'});
    input.setAttribute('aria-label', 'Search PiVault');
    const kbd = make('kbd', {textContent: '/'}); kbd.setAttribute('aria-hidden', 'true');
    field.append(svg, input, kbd);
    const group = make('div', {className: 'pv-scopes'});
    group.setAttribute('role', 'group'); group.setAttribute('aria-label', 'Search in');
    for (const [label, key] of SCOPES) {
      if (key !== null && !apps.some(app => app.key === key)) continue;
      const b = make('button', {type: 'button', className: 'pv-scope', textContent: label});
      b.setAttribute('aria-pressed', String(scope[0] === label));
      b.addEventListener('click', () => {
        scope = [label, key];
        group.querySelectorAll('.pv-scope').forEach(other => other.setAttribute('aria-pressed', String(other === b)));
      });
      group.append(b);
    }
    bar.append(field, group);
    // The apps for the chosen scope travel as GET parameters; FormData reads them after this handler.
    bar.addEventListener('submit', () => {
      bar.querySelectorAll('input[name=app]').forEach(node => node.remove());
      const keys = scope[1] === null ? apps.map(app => app.key) : [scope[1]];
      for (const key of keys) bar.append(make('input', {type: 'hidden', name: 'app', value: key}));
    });
    host.prepend(bar);
  }
  // "/" focuses the search field unless the user is already typing somewhere.
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
    const input = bar?.querySelector('input[name=q]');
    if (!input) return;
    event.preventDefault(); input.focus();
  });
  async function start() {
    try {
      const response = await fetch('/search/apps', {credentials: 'same-origin', cache: 'no-store'});
      apps = response.ok ? ((await response.json()).apps || []) : [];
    } catch { apps = []; }
    if (!apps.length) return;  // Signed out, or nothing this account may search.
    mount();
    // Homepage can re-render the launcher; put the bar back if it disappears.
    new MutationObserver(mount).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();

/* Tab icon: the keyhole board, as an inline SVG so nothing is fetched. The launcher's title mark
   is drawn by custom.css. Next.js can re-render <head>, so the icon is re-applied when that happens. */
(() => {
  if (window.piVaultLogoLoaded) return;
  window.piVaultLogoLoaded = true;
  const icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#202a35"/>' +
    '<svg x="3" y="3" width="26" height="26" viewBox="0 0 120 120" fill="none">' +
    '<rect x="8" y="20" width="104" height="80" rx="10" stroke="#91acc3" stroke-width="11"/>' +
    '<path d="M53 60A13 13 0 1 1 67 60L72 86H48Z" stroke="#91acc3" stroke-width="11" stroke-linejoin="round"/></svg></svg>';
  const uri = svg => 'data:image/svg+xml,' + encodeURIComponent(svg);
  const tab = Object.assign(document.createElement('link'), {id: 'pv-icon', rel: 'icon', type: 'image/svg+xml', href: uri(icon)});
  const apply = () => {
    document.head.querySelectorAll('link[rel~="icon"]:not(#pv-icon),link[rel="apple-touch-icon"]').forEach(link => link.remove());
    if (!tab.isConnected) document.head.append(tab);
  };
  const start = () => { apply(); new MutationObserver(apply).observe(document.head, {childList: true}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
