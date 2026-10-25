/* Add after the existing custom.js. The API must be installed before enabling. */
/* Shared top-right toolbar: the notification bell, then the settings gear to its right. */
window.piVaultToolbar = window.piVaultToolbar || (() => {
  let bar = document.getElementById('pv-toolbar');
  if (bar?.isConnected) return bar;
  bar = document.createElement('div');
  bar.id = 'pv-toolbar';
  bar.style.cssText = 'position:fixed;top:clamp(20px,3vw,40px);right:var(--pv-gutter,24px);z-index:1000;display:flex;gap:6px;align-items:flex-start';
  document.body.append(bar);
  return bar;
});

/* Settings menu: File Sync and Password Manager for everyone, plus Add User for Admins.
   The server, not the browser, enforces Admin access on /users/add. */
(() => {
  if (window.piVaultUsersLoaded) return;
  window.piVaultUsersLoaded = true;
  let root, button, panel, add, opened = false, admin = false;
  const ns = 'http://www.w3.org/2000/svg';
  // Gear, user-plus, refresh and lock outlines from Feather Icons (MIT License, compatible with this project).
  const GEAR = 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z';
  const USER_PLUS = 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6M17 11h6M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0';
  const SYNC = 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15';
  const LOCK = 'M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM7 11V7a5 5 0 0 1 10 0v4';
  // These apps leave the launcher columns for this menu; their links come from their tiles.
  const MOVED = [['#pivault-file-sync', 'File Sync', SYNC], ['li.service[data-name="Password Manager"]', 'Password Manager', LOCK]];
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
      #pv-settings .pv-gear{display:grid;place-items:center;width:46px;height:46px;padding:0;border:0;border-radius:.5em;background:transparent;color:var(--pv-secondary,#91acc3);cursor:pointer}
      #pv-settings .pv-gear:hover,#pv-settings .pv-gear[aria-expanded=true]{color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
      #pv-settings .pv-menu{position:absolute;right:0;top:54px;min-width:240px;padding:8px;border:1px solid var(--pv-divider,rgba(151,170,190,.48));border-radius:.5em;background:var(--pv-background,#151c22);box-shadow:0 10px 30px #0005}
      #pv-settings h2{margin:6px 10px 8px;font-size:12px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase;color:var(--pv-secondary,#91acc3)}
      #pv-settings a{display:flex;align-items:center;gap:12px;min-height:44px;padding:0 10px;border-radius:.4em;color:var(--pv-text,#e8edf2);text-decoration:none}
      #pv-settings a:hover{background:rgba(151,170,190,.1)}
      #pv-settings a svg{color:#91acc3}
      #pv-settings .pv-gear:focus-visible,#pv-settings a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:3px}
    `;
    button = document.createElement('button');
    button.type = 'button'; button.className = 'pv-gear';
    button.setAttribute('aria-label', 'Settings'); button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'pv-settings-menu');
    button.append(icon(GEAR, 24));
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
  }
  document.addEventListener('click', event => { if (root && !root.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) { close(); button?.focus(); } });
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh, {once:true});
  else refresh();
})();

/* Launcher columns: Entertainment spans two columns of two tiles, and Tools (now in the
   settings menu) leaves the launcher, so wide screens show four columns of two rows. The
   launcher CSS places each group in a named grid area; this only redefines the row of areas.
   The repeated ID outranks the launcher CSS's own rules. */
(() => {
  if (window.piVaultColumnsLoaded) return;
  window.piVaultColumnsLoaded = true;
  const L = '#layout-groups#layout-groups#layout-groups';
  const style = document.createElement('style');
  style.id = 'pv-columns';
  style.textContent = `
    ${L} > .services-group:has(#pivault-file-sync){display:none!important}
    @media (min-width:1280px){
      ${L}:has(#pivault-books){grid-template-columns:repeat(4,minmax(0,1fr))!important;grid-template-areas:"title title title title" "entertainment entertainment knowledge organization"!important}
    }
    @media (min-width:768px){
      ${L} > .services-group:has(#pivault-books) .services-list{grid-template-columns:repeat(2,minmax(0,1fr))!important;column-gap:var(--pv-column-gap,48px)!important}
    }
  `;
  const mount = () => { if (!style.isConnected) document.head.append(style); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once: true});
  else mount();
})();

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
      #pv-admin-notifications{position:relative;order:1;color:var(--pv-text,#e8edf2);font:15px/1.45 Arial,Helvetica,sans-serif}
      #pv-admin-notifications [hidden]{display:none!important}
      #pv-admin-notifications .pv-bell{position:relative;display:grid;place-items:center;width:46px;height:46px;padding:9px;border:0;border-radius:.5em;background:transparent;color:var(--pv-secondary,#91acc3);cursor:pointer}
      #pv-admin-notifications .pv-bell:hover{color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
      #pv-admin-notifications .pv-bell:focus-visible,#pv-admin-notifications a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:4px}
      #pv-admin-notifications .pv-count{position:absolute;top:0;right:0;min-width:19px;padding:1px 4px;border-radius:12px;background:#91acc3;color:#151c22;font-size:11px;font-weight:700;text-align:center}
      #pv-admin-notifications .pv-panel{position:absolute;right:0;top:54px;width:min(440px,calc(100vw - 48px));max-height:min(620px,calc(100dvh - 100px));overflow:auto;padding:22px;border:1px solid var(--pv-divider,rgba(151,170,190,.48));border-radius:.5em;background:var(--pv-background,#151c22);box-shadow:0 10px 30px #0005}
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
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    for (const [name,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','aria-hidden':'true'})) svg.setAttribute(name,value);
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d','M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4');
    svg.append(path); badge = make('span', '', 'pv-count'); badge.hidden = true;
    button.append(svg,badge);
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

/* Unified search: a field under the PiVault title with a row of tickboxes directly beneath it,
   one per app this user may search. Unticked apps are never queried. The field sits in the
   launcher's own "title" grid area, which every launcher layout keeps, so it follows the
   launcher's gutters and breakpoints. App tiles are left untouched. */
(() => {
  if (window.piVaultSearchLoaded) return;
  window.piVaultSearchLoaded = true;
  const STORE = 'pivault-search-unticked';
  const CHECK = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.5 8.5l3 3 6-7' fill='none' stroke='%23151c22' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\")";
  let apps = [], bar = null, hint = null;
  const unticked = () => { try { return new Set(JSON.parse(localStorage.getItem(STORE) || '[]')); } catch { return new Set(); } };
  const remember = set => { try { localStorage.setItem(STORE, JSON.stringify([...set])); } catch { /* Per-browser convenience only. */ } };
  const make = (tag, props = {}) => Object.assign(document.createElement(tag), props);
  const style = make('style', {id: 'pv-search-style'});
  // The repeated ID outranks the launcher CSS (body:has(#pivault-books) #layout-groups ...).
  const L = '#layout-groups#layout-groups#layout-groups';
  style.textContent = `
    ${L} > #pv-search{grid-area:title;align-self:start;display:flex;flex-wrap:wrap;align-items:center;gap:12px;
      margin:64px 0 56px;color:var(--pv-text,#e8edf2);font:inherit;font-size:16px;z-index:1}
    @media (max-width:1279px){${L} > #pv-search{margin:60px 0 0}}
    #pv-search .pv-field{flex:1 1 320px;display:flex;align-items:center;gap:12px;min-height:52px;padding:0 16px;
      border:1px solid var(--pv-divider,rgba(151,170,190,.48));border-radius:.5em;background:rgba(151,170,190,.06);box-sizing:border-box}
    #pv-search .pv-field:focus-within{border-color:var(--pv-secondary,#91acc3)}
    #pv-search .pv-field svg{flex:none;width:20px;height:20px;color:var(--pv-secondary,#91acc3)}
    #pv-search input[type=search]{flex:1;min-width:0;height:50px;padding:0;border:0;background:transparent;box-shadow:none;
      color:var(--pv-text,#e8edf2);font:inherit;font-size:17px;outline:none}
    #pv-search input[type=search]::placeholder{color:var(--pv-secondary,#91acc3);opacity:.8}
    #pv-search .pv-go{min-height:52px;padding:0 26px;border:0;border-radius:.5em;cursor:pointer;color:var(--pv-text,#e8edf2);
      font:inherit;font-size:16px;font-weight:500;background:linear-gradient(145deg,#2b3643,#202a35);box-shadow:inset 0 1px 0 rgb(206 220 235 / 26%)}
    #pv-search .pv-go:hover{box-shadow:0 4px 6px -1px rgb(0 0 0 / 20%),inset 0 1px 0 rgb(206 220 235 / 26%)}
    #pv-search fieldset{flex:1 1 100%;display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:0;padding:0;border:0;min-width:0}
    #pv-search legend{float:left;margin-right:6px;padding:0;color:var(--pv-secondary,#91acc3);font-size:12px;font-weight:600;letter-spacing:1.6px;text-transform:uppercase}
    #pv-search .pv-chip{display:flex;align-items:center;gap:9px;min-height:36px;padding:0 14px;border:1px solid var(--pv-divider,rgba(151,170,190,.48));
      border-radius:18px;font-size:15px;cursor:pointer;user-select:none;color:var(--pv-secondary,#91acc3)}
    #pv-search .pv-chip:has(input:checked){color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
    #pv-search .pv-chip input{appearance:none;-webkit-appearance:none;flex:none;width:16px;height:16px;margin:0;border:1.5px solid var(--pv-secondary,#91acc3);
      border-radius:4px;background:transparent;cursor:pointer;color:transparent;box-shadow:none}
    #pv-search .pv-chip input:checked{background:var(--pv-secondary,#91acc3) ${CHECK} center/12px no-repeat}
    #pv-search .pv-all{padding:0 4px;border:0;background:none;color:var(--pv-accent,#b4c0ce);font:inherit;font-size:14px;text-decoration:underline;cursor:pointer}
    #pv-search .pv-hint{flex:1 1 100%;color:var(--pv-secondary,#91acc3);font-size:14px}
    #pv-search .pv-hint:empty{display:none}
    #pv-search :focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:3px}
  `;
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
    field.append(svg, input);
    const picks = make('fieldset');
    picks.append(make('legend', {textContent: 'Search in'}));
    const off = unticked();
    for (const app of apps) {
      const chip = make('label', {className: 'pv-chip'});
      const box = make('input', {type: 'checkbox', name: 'app', value: app.key, checked: !off.has(app.key)});
      box.addEventListener('change', () => {
        const set = unticked(); box.checked ? set.delete(app.key) : set.add(app.key); remember(set); hint.textContent = '';
      });
      chip.append(box, document.createTextNode(app.label));
      picks.append(chip);
    }
    for (const [text, value] of [['All', true], ['None', false]]) {
      const b = make('button', {type: 'button', className: 'pv-all', textContent: text});
      b.addEventListener('click', () => {
        remember(value ? new Set() : new Set(apps.map(a => a.key)));
        picks.querySelectorAll('input').forEach(box => { box.checked = value; });
      });
      picks.append(b);
    }
    hint = make('div', {className: 'pv-hint'});
    hint.setAttribute('aria-live', 'polite');
    bar.append(field, make('button', {type: 'submit', className: 'pv-go', textContent: 'Search'}), picks, hint);
    bar.addEventListener('submit', event => {
      if (!picks.querySelector('input:checked')) { event.preventDefault(); hint.textContent = 'Tick at least one app to search.'; }
    });
    host.prepend(bar);
  }
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

/* Board Keyhole logo: the mark before the launcher's "PiVault" title (custom.css draws the
   title as #layout-groups::before) and the tab icon. The title shares its grid area with the
   search bar, so it is pinned to the top and centred rather than stretched over the search row.
   Its 44px Manrope 200 in the mark's colour sets the cap height (0.72em) to the board outline's
   height and the stem weight closest to the mark's stroke. Both are inline SVG data URIs, so nothing
   is fetched; the wordmark keeps Homepage's bundled Manrope. Next.js can re-render <head>, so
   any icon it re-adds is replaced again. */
(() => {
  if (window.piVaultLogoLoaded) return;
  window.piVaultLogoLoaded = true;
  const L = '#layout-groups#layout-groups#layout-groups';
  const pins = [27, 35, 43, 51, 59, 67, 75, 83, 91].map(x => `<rect x="${x}" y="28" width="5" height="5" rx="1"/>`).join('');
  const holes = [[16, 30], [104, 30], [16, 90], [104, 90]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="3"/>`).join('');
  const mark = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" fill="none">' +
    '<rect x="8" y="22" width="104" height="76" rx="7" stroke="#91acc3" stroke-width="3"/>' +
    `<g fill="#91acc3">${pins}</g><g stroke="#91acc3" stroke-width="2">${holes}</g>` +
    '<path d="M54 61.9A10 10 0 1 1 66 61.9L70.5 84H49.5Z" stroke="#91acc3" stroke-width="3" stroke-linejoin="round"/></svg>';
  const icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#202a35"/>' +
    '<svg x="3" y="3" width="26" height="26" viewBox="0 0 120 120" fill="none">' +
    '<rect x="8" y="20" width="104" height="80" rx="10" stroke="#91acc3" stroke-width="11"/>' +
    '<path d="M53 60A13 13 0 1 1 67 60L72 86H48Z" stroke="#91acc3" stroke-width="11" stroke-linejoin="round"/></svg></svg>';
  const uri = svg => 'data:image/svg+xml,' + encodeURIComponent(svg);
  const style = document.createElement('style');
  style.id = 'pv-logo';
  style.textContent = `
    body:has(#pivault-books) ${L}::before{align-self:start;justify-self:center;height:48px;padding-left:60px;
      font-size:44px;font-weight:200;line-height:48px;letter-spacing:-1.4px;color:#91acc3;
      background:url("${uri(mark)}") no-repeat left 0 top 1px/48px 48px}
  `;
  const tab = Object.assign(document.createElement('link'), {id: 'pv-icon', rel: 'icon', type: 'image/svg+xml', href: uri(icon)});
  const apply = () => {
    document.head.querySelectorAll('link[rel~="icon"]:not(#pv-icon),link[rel="apple-touch-icon"]').forEach(link => link.remove());
    if (!style.isConnected) document.head.append(style);
    if (!tab.isConnected) document.head.append(tab);
  };
  const start = () => { apply(); new MutationObserver(apply).observe(document.head, {childList: true}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
