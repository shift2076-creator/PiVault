/* PiVault launcher behaviour for Homepage v2.6.0.

   Homepage runs this after its own custom.js. installer/configuration.py (homepage_js) keeps it between
   the PIVAULT managed markers, so an update replaces it in place. Styling lives in stack/homepage/custom.css.

   Each module is independent, loads once (guarded by a window flag) and re-applies itself when Homepage
   re-renders the launcher. The server enforces every permission; this file only decides what to show.

   Endpoints: /users/session, /users/add, /system, /devices, /notifications, /notifications/api,
   /notifications/seen, /notifications/<id>, /search/apps, /search, /services/status, /services/<key>/<verb>. */


/* Shared helpers, read by the modules below as window.piVault. */
window.piVault = window.piVault || (() => {
  const SVG_NS = 'http://www.w3.org/2000/svg';
  return {
    make(tag, props = {}) {
      return Object.assign(document.createElement(tag), props);
    },
    icon(d, size = 20, strokeWidth = '1.7') {
      const svg = document.createElementNS(SVG_NS, 'svg');
      const attributes = {
        viewBox: '0 0 24 24', width: String(size), height: String(size), fill: 'none', stroke: 'currentColor',
        'stroke-width': strokeWidth, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
      };
      for (const [name, value] of Object.entries(attributes)) svg.setAttribute(name, value);
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('d', d);
      svg.append(path);
      return svg;
    },
    ready(fn) {
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn, {once: true});
      else fn();
    },
  };
})();


/* Shared top-right toolbar: the admin bell, then the Settings gear. Position and spacing are in custom.css. */
window.piVaultToolbar = window.piVaultToolbar || (() => {
  let bar = document.getElementById('pv-toolbar');
  if (bar?.isConnected) return bar;
  bar = window.piVault.make('div', {id: 'pv-toolbar'});
  document.body.append(bar);
  return bar;
});


/* Launcher title: one heading above the grid. custom.css draws the mark beside it. */
(() => {
  if (window.piVaultTitleLoaded) return;
  window.piVaultTitleLoaded = true;
  const {make, ready} = window.piVault;
  const ensure = () => {
    const host = document.getElementById('layout-groups');
    if (!host || document.getElementById('pv-title')) return;
    host.prepend(make('h1', {id: 'pv-title', textContent: 'PiVault'}));
  };
  ready(() => {
    ensure();
    new MutationObserver(ensure).observe(document.body, {childList: true, subtree: true});
  });
})();


/* Settings menu: Password Manager for everyone, plus Add User, System, Sync repo and Radicale for Admins.
   Password Manager's address is copied from its launcher tile, which custom.css hides. */
(() => {
  if (window.piVaultUsersLoaded) return;
  window.piVaultUsersLoaded = true;
  const {make, icon, ready} = window.piVault;
  // Feather Icons outlines (MIT License, compatible with this project).
  const GEAR = 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z';
  const USER_PLUS = 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6M17 11h6M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0';
  const LOCK = 'M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM7 11V7a5 5 0 0 1 10 0v4';
  const SYNC = 'M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15';
  const CALENDAR = 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z';
  // Apps whose launcher tile is hidden; each menu link copies its address from that tile.
  const MOVED = [['li.service[data-name="Password Manager"]', 'Password Manager', LOCK]];
  const adminLinks = [];
  let root, button, panel, opened = false, admin = false;

  function menuLink(href, path, label) {
    const link = make('a', {href});
    link.append(icon(path, 20), document.createTextNode(label));
    return link;
  }

  function close() {
    if (!root) return;
    opened = false;
    panel.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  }

  function copyTileAddresses() {
    for (const link of panel.querySelectorAll('a[data-source]')) {
      const tile = document.querySelector(link.dataset.source)?.querySelector('a[href]');
      link.hidden = !tile;
      if (!tile) continue;
      link.href = tile.href;
      if (tile.target) {
        link.target = tile.target;
        link.rel = 'noopener noreferrer';
      }
    }
  }

  function mount() {
    if (root?.isConnected) return;
    root = make('div', {id: 'pv-settings'});
    button = make('button', {type: 'button', className: 'pv-gear'});
    button.setAttribute('aria-label', 'Settings');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'pv-settings-menu');
    button.append(icon(GEAR, 20));

    panel = make('nav', {id: 'pv-settings-menu', className: 'pv-menu', hidden: true});
    panel.setAttribute('aria-label', 'Settings');
    panel.append(make('h2', {textContent: 'Settings'}));
    for (const [selector, label, path] of MOVED) {
      const link = menuLink('', path, label);
      link.dataset.source = selector;
      panel.append(link);
    }

    // The Android device registry and its approved-app sync live here, not on the System page.
    // Radicale is served over HTTPS on port 5232 at the launcher's address.
    adminLinks.length = 0;
    adminLinks.push(
      menuLink('/users/add', USER_PLUS, 'Add User'),
      menuLink('/system', GEAR, 'System'),
      menuLink('/devices', SYNC, 'Sync repo'),
      menuLink(`https://${location.hostname}:5232/`, CALENDAR, 'Radicale'),
    );
    panel.append(...adminLinks);

    root.append(button, panel);
    window.piVaultToolbar().append(root);
    button.addEventListener('click', () => {
      if (opened) {
        close();
        return;
      }
      copyTileAddresses();
      opened = true;
      panel.hidden = false;
      button.setAttribute('aria-expanded', 'true');
    });
    copyTileAddresses();
  }

  async function refresh() {
    admin = false;
    try {
      const response = await fetch('/users/session', {credentials: 'same-origin', cache: 'no-store'});
      const state = response.ok ? await response.json() : {};
      admin = state.authenticated === true && state.admin === true;
    } catch {
      admin = false;
    }
    mount();
    for (const link of adminLinks) link.hidden = !admin;
  }

  document.addEventListener('click', event => {
    if (root && !root.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && opened) {
      close();
      button?.focus();
    }
  });
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  ready(refresh);
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
      #pv-admin-notifications .pv-bell{position:relative;display:grid;place-items:center;width:40px;height:40px;padding:8px;border:0;border-radius:.5em;background:transparent;color:var(--pv-secondary,#91acc3);cursor:pointer}
      #pv-admin-notifications .pv-bell:hover{color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
      #pv-admin-notifications .pv-bell:focus-visible,#pv-admin-notifications a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:4px}
      #pv-admin-notifications .pv-count{position:absolute;top:0;right:0;min-width:19px;padding:1px 4px;border-radius:12px;background:#91acc3;color:#151c22;font-size:11px;font-weight:700;text-align:center}
      #pv-admin-notifications .pv-panel{position:absolute;right:0;top:48px;width:min(440px,calc(100vw - 48px));max-height:min(620px,calc(100dvh - 100px));overflow:auto;padding:22px;border:1px solid var(--pv-menu-border,#3a4553);border-radius:.5em;background:var(--pv-surface,#1c222b);box-shadow:0 10px 30px #0005}
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
    button.hidden = true;  // Shown only while there are open notices.
    button.setAttribute('aria-controls', 'pv-notification-panel');
    badge = make('span', '', 'pv-count'); badge.hidden = true;
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    for (const [name, value] of Object.entries({viewBox:'0 0 24 24', width:'22', height:'22', fill:'none', stroke:'currentColor', 'stroke-width':'1.7', 'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true'})) svg.setAttribute(name, value);
    const bell = document.createElementNS(ns, 'path'); bell.setAttribute('d', 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4'); svg.append(bell);
    button.append(svg, badge);
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
    button.hidden = state.items.length === 0;
    if (button.hidden) close();
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


/* Unified search: a field under the PiVault title, with a scope control beneath it. "All" searches every app
   this user may search; each other segment searches one app. The form sends the apps for the chosen scope as
   GET parameters, so results load without script. Segments appear only for apps this user may search.
   "/" moves focus to the field. */
(() => {
  if (window.piVaultSearchLoaded) return;
  window.piVaultSearchLoaded = true;
  const {make, icon, ready} = window.piVault;
  try { localStorage.removeItem('pivault-search-unticked'); } catch { /* Old per-browser choice; nothing else to clear. */ }
  const SCOPES = [['All', null], ['Books', 'books'], ['Music', 'music'], ['Offline Wiki', 'wiki']];
  const LENS = 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4';
  let apps = [], bar = null, scope = SCOPES[0];

  function mount() {
    const host = document.getElementById('layout-groups');
    if (!host || bar?.isConnected) return;
    bar = make('form', {id: 'pv-search', method: 'get', action: '/search', role: 'search'});
    const input = make('input', {type: 'search', name: 'q', required: true, minLength: 2, maxLength: 200,
      autocomplete: 'off', placeholder: 'Search PiVault'});
    input.setAttribute('aria-label', 'Search PiVault');
    const kbd = make('kbd', {textContent: '/'});
    kbd.setAttribute('aria-hidden', 'true');
    const field = make('label', {className: 'pv-field'});
    field.append(icon(LENS, 20, '1.8'), input, kbd);

    const group = make('div', {className: 'pv-scopes'});
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Search in');
    for (const [label, key] of SCOPES) {
      if (key !== null && !apps.some(app => app.key === key)) continue;
      const choice = make('button', {type: 'button', className: 'pv-scope', textContent: label});
      choice.setAttribute('aria-pressed', String(scope[0] === label));
      choice.addEventListener('click', () => {
        scope = [label, key];
        group.querySelectorAll('.pv-scope').forEach(other => other.setAttribute('aria-pressed', String(other === choice)));
      });
      group.append(choice);
    }
    bar.append(field, group);

    // The apps for the chosen scope travel as GET parameters; FormData reads them after this handler.
    bar.addEventListener('submit', () => {
      bar.querySelectorAll('input[name="app"]').forEach(node => node.remove());
      const keys = scope[1] === null ? apps.map(app => app.key) : [scope[1]];
      for (const key of keys) bar.append(make('input', {type: 'hidden', name: 'app', value: key}));
    });

    const title = document.getElementById('pv-title');
    if (title) title.after(bar);
    else host.prepend(bar);
  }

  // "/" focuses the search field unless the user is already typing somewhere.
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
    const input = bar?.querySelector('input[name="q"]');
    if (!input) return;
    event.preventDefault();
    input.focus();
  });

  async function start() {
    try {
      const response = await fetch('/search/apps', {credentials: 'same-origin', cache: 'no-store'});
      apps = response.ok ? ((await response.json()).apps || []) : [];
    } catch {
      apps = [];
    }
    if (!apps.length) return;  // Signed out, or nothing this account may search.
    mount();
    // Homepage can re-render the launcher; put the bar back if it disappears.
    new MutationObserver(mount).observe(document.body, {childList: true, subtree: true});
  }
  ready(start);
})();


/* Tab icon: the keyhole board, as an inline SVG so nothing is fetched. Next.js can re-render <head>,
   so the icon is re-applied when that happens. */
(() => {
  if (window.piVaultLogoLoaded) return;
  window.piVaultLogoLoaded = true;
  const {make, ready} = window.piVault;
  const markup = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#202a35"/>' +
    '<svg x="3" y="3" width="26" height="26" viewBox="0 0 120 120" fill="none">' +
    '<rect x="8" y="20" width="104" height="80" rx="10" stroke="#91acc3" stroke-width="11"/>' +
    '<path d="M53 60A13 13 0 1 1 67 60L72 86H48Z" stroke="#91acc3" stroke-width="11" stroke-linejoin="round"/></svg></svg>';
  const tab = make('link', {id: 'pv-icon', rel: 'icon', type: 'image/svg+xml',
    href: 'data:image/svg+xml,' + encodeURIComponent(markup)});
  const apply = () => {
    document.head.querySelectorAll('link[rel~="icon"]:not(#pv-icon),link[rel="apple-touch-icon"]').forEach(link => link.remove());
    if (!tab.isConnected) document.head.append(tab);
  };
  ready(() => {
    apply();
    new MutationObserver(apply).observe(document.head, {childList: true});
  });
})();


/* Launcher status: each tile shows a dot (running, needs attention, stopped or unknown). Admins also get a menu
   on the dot with Stop and Restart, or Start for a stopped service. The server checks Admin access, the CSRF
   token and the list of containers each request may name. */
(() => {
  if (window.piVaultStatusLoaded) return;
  window.piVaultStatusLoaded = true;
  const {make, ready} = window.piVault;
  // Launcher tile name -> key the server accepts. Contacts is a host service and has no dot.
  const KEYS = {'Books': 'books', 'Games': 'games', 'Music': 'music', 'Videos': 'videos',
    'Documents': 'documents', 'Offline Wiki': 'offline-wiki', 'Inventory': 'inventory',
    'Pantry': 'pantry', 'Photos': 'photos'};
  const WORDS = {running: 'running', attention: 'needs attention', stopped: 'stopped', unknown: 'status unavailable'};
  let services = {}, canControl = false, csrf = '', openLed = null;
  const busy = new Set();

  const tiles = () => [...document.querySelectorAll('li.service[data-name]')].filter(li => KEYS[li.dataset.name]);
  const stateOf = key => services[key] || 'unknown';
  const verbsFor = state => (state === 'stopped' ? ['start'] : (state === 'unknown' ? [] : ['stop', 'restart']));

  function fillMenu(menu, li, key) {
    const verbs = verbsFor(stateOf(key));
    const current = [...menu.querySelectorAll('button')].map(button => button.dataset.verb).join(',');
    if (current !== verbs.join(',')) {
      const items = verbs.map(verb => {
        const item = make('button', {type: 'button', textContent: verb[0].toUpperCase() + verb.slice(1)});
        item.dataset.verb = verb;
        item.setAttribute('role', 'menuitem');
        item.addEventListener('click', () => {
          close();
          act(li, key, verb);
        });
        return item;
      });
      menu.replaceChildren(...items, make('p'));
    }
    for (const button of menu.querySelectorAll('button')) button.disabled = busy.has(key);
  }

  function render() {
    for (const li of tiles()) {
      const key = KEYS[li.dataset.name];
      const card = li.querySelector('.service-card');
      const row = li.querySelector('.service-title');
      if (!card || !row) continue;
      let control = row.querySelector(':scope > .pv-led');
      // The first render can run before the status reply arrives, so rebuild the control when the mode changes.
      if (control && (control.tagName === 'BUTTON') !== canControl) {
        close();
        control.remove();
        card.querySelector(':scope > .pv-launch-menu')?.remove();
        control = null;
      }
      if (!control) {
        control = canControl ? make('button', {type: 'button', className: 'pv-led'}) : make('span', {className: 'pv-led'});
        if (canControl) {
          control.setAttribute('aria-haspopup', 'menu');
          control.setAttribute('aria-expanded', 'false');
          control.addEventListener('click', () => toggle(li, control));
        } else {
          control.setAttribute('role', 'img');
        }
        const dot = make('span', {className: 'pv-dot'});
        dot.setAttribute('aria-hidden', 'true');
        control.append(dot);
        row.append(control);
      }

      const state = stateOf(key);
      const dot = control.querySelector('.pv-dot');
      if (dot.dataset.state !== state) dot.dataset.state = state;
      const text = `${li.dataset.name}: ${WORDS[state] || WORDS.unknown}`;
      const wanted = canControl ? `${text}. Open menu` : text;
      if (control.getAttribute('aria-label') !== wanted) control.setAttribute('aria-label', wanted);

      if (canControl) {
        control.disabled = busy.has(key) || state === 'unknown';
        let menu = card.querySelector(':scope > .pv-launch-menu');
        if (!menu) {
          menu = make('div', {className: 'pv-launch-menu', hidden: true});
          menu.setAttribute('role', 'menu');
          menu.setAttribute('aria-label', li.dataset.name);
          card.append(menu);
        }
        fillMenu(menu, li, key);
      }
    }
  }

  function close() {
    if (!openLed) return;
    const menu = openLed.closest('.service-card')?.querySelector(':scope > .pv-launch-menu');
    if (menu) menu.hidden = true;
    openLed.setAttribute('aria-expanded', 'false');
    openLed = null;
  }

  function toggle(li, control) {
    if (openLed === control) {
      close();
      return;
    }
    close();
    const menu = li.querySelector('.service-card > .pv-launch-menu');
    if (!menu) return;
    menu.hidden = false;
    control.setAttribute('aria-expanded', 'true');
    openLed = control;
    menu.querySelector('button')?.focus();
  }

  async function refresh() {
    try {
      const response = await fetch('/services/status', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) {
        services = {};
        canControl = false;
        render();
        return;
      }
      const data = await response.json();
      services = data.services || {};
      canControl = data.can_control === true;
      csrf = data.csrf_token || csrf;
    } catch {
      services = {};
    }
    render();
  }

  async function act(li, key, verb) {
    busy.add(key);
    render();
    const note = li.querySelector('.service-card > .pv-launch-menu > p');
    try {
      const response = await fetch(`/services/${key}/${verb}`, {method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf}});
      if (note) note.textContent = response.ok ? '' : `Could not ${verb} ${li.dataset.name}.`;
    } catch {
      if (note) note.textContent = `Could not ${verb} ${li.dataset.name}.`;
    } finally {
      busy.delete(key);
    }
    await refresh();
  }

  document.addEventListener('click', event => {
    if (openLed && !openLed.closest('.service-card')?.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && openLed) {
      const control = openLed;
      close();
      control.focus();
    }
  });

  ready(() => {
    refresh();
    window.setInterval(refresh, 15000);
    // Homepage can re-render the launcher; put the dots back when that happens.
    new MutationObserver(render).observe(document.body, {childList: true, subtree: true});
  });
})();
