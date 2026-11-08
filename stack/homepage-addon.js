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


/* Favicons: the keyhole board in a rounded square, as an SVG and as PNG, ICO and Apple touch icon
   data URIs generated from stack/homepage/favicon/ by tools/favicon_block.py. Nothing is fetched.
   Next.js can re-render <head>, so the links are re-applied when it does. */
(() => {
  if (window.piVaultLogoLoaded) return;
  window.piVaultLogoLoaded = true;
  const {make, ready} = window.piVault;
  /* BEGIN PIVAULT FAVICONS */
  const FAVICON = {
    svg: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PGNpcmNsZSBjeD0iOCIgY3k9IjgiIHI9IjcuMjUiIGZpbGw9IiMxNTFjMjIiIHN0cm9rZT0iIzkxYWNjMyIgc3Ryb2tlLXdpZHRoPSIxIi8+PHBhdGggZD0iTTYuMTE2IDYuOTMyQTMuMTQwIDMuMTQwIDAgMSAxIDkuODg0IDYuOTMyTDExLjI5NyAxMy44NzJMNC43MDMgMTMuODcyWiIgZmlsbD0iIzkxYWNjMyIvPjwvc3ZnPgo=',
    png16: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABmJLR0QA/wD/AP+gvaeTAAACjklEQVQ4jYWTX0hTcRTHv/f/7pVZm5vbNK09tAgNggaRhNhTM8FgmfgShgaDMOjPWxEI0ptBQj4MUTIKQp1gL1PozfJJXxpmzQZKhTq3Xd2/e+/udm9P2u720IHfwzm/7+9zzvkdDoEKG55ZZy1EslNVlG5VK54FAJqiooxJ+MgnTOFAwKuW64lyZ2zu842CLE8m93eEvT/bZoutngCA5H5cd506nbHVO3Mcyw8+uH01XAV4+eHTY1VRR0JvXgt2VyOu++8YKlsMvUNy7zf8/UN5hjM9e9J37RUAkEeZVbkwMjH6XPgVi6Kjq6eyM3R03cJ27AcmRp8LqiK/GJtf9gEAOTyzzhZkeTI0PS6kxRQEcy04jq8CmEw8hJpapMUUQtPjgirJU8HgKkOepERfcn9H2NrcAABk04fIHIpVgPSBiFwmDQDYin5DMr5bo9RJPrIoSTcjq1/MR0Jd1xCefQutpB0/1koaFuemoev/YpG1FXNBkbpJtVT0iIk9wzRiG18NYh0aYt8jhorERJxQi0UPCQJ6xTThaGwGRdPHPkXRcLiaKpoiAEAnGZLetNodevmVq+lM1R84m90G32Kz6wxNb5I0zy+0XmrL/A9QGbvgbcvQLL9AHpQsi7Z6Z87tOV8mdlcBGsoA7nMtsNqdWT7FL5HDvS0FjuUH/f1D+VqLFTTDwuZqrALYnU2gGRYnLHXw99/PMxw3EAh4VQoAwrNTPzv77uUvXm5vz2XTjLOhGbKUNxxFlqAoEvx3h/IcZ3r6qLfjPaqWaX7Zp0ryVDK+WxNZWzGLiTgBAFabQ2/1XslY7a4sw7EDD3valwyzKLdgcJVR6iRfQZG61WLRAwAMTUdpll/gU/xS5Tr/BT9KDLtY7W3fAAAAAElFTkSuQmCC',
    png32: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABmJLR0QA/wD/AP+gvaeTAAAFX0lEQVRYhaWX6U9UVxTAf/e+cWDYFaHIMrIUl2rVippaROuSYknUAhaXuDZNmn6pS9T/ocUUtR8aa2KjNVahuCZudUkRbFPFhYprGBEKVqRKURhnee/2wzCUbZhHej69e+9Zfue8d+89TzBE2VVaEQe8hZDJhjDiAaSSLSjjT+DOF0Wznw3FnzAbVAm5FqmWoJgJaAFUdeCKEhyXutpvBmZQgOL9Z8OttvDNCLUViATwetw0N9bj9bgRQgKgMLBYrCTa07BYhvnN24Wg2NXRWbJ1TW7HkAF2lFdNF8o4AiQDPLh9g5qrlYSGhjEnr4DI6OG99NvbXvDLqXJcLieTZ+SQOWGKP0CjMsjfUJRTbRpgZ1nFcoTYC9iaGhycP/ojjY8eMCevkJwPFg9WNCrOHKPizFFS0sawIH8FSfZ0lFJOpFy/sXDW4aAAXcEPAqK66iLnyg+gGzpjJr5D0acbBw3ul8N7vuZh7S00qZFbuIqp2fMAlBJiRV8I2XPwzU+V07oyF+ePH+Z02T50Qwdg9sICU8EB5iwsBEA3dE6V7ePCiUO+ZA3j+52ll7MGBCjefzbcQB0FbNVVF/nt0qlupejhsSQk200DJKSMJirmv2/k14unuV51ESGEDcmR3SevhfUDsNrCNwPJzY/rOFd+oJfDmNg408H9Mjz2jV7js+UHaGpwANhfu5ybegF8e6Qqvmur8fOxQ91l94uzM+AuCiidna96jXVD58KxQ/7htq4DzQfgUsZqIPLB7Rs0PnrQz9nzZ0/Rda/p4Lqh8+LZ037zDY77PKy9CRClpFjdDYAiH6Dm98oBHXo9bmquVpkGuHfrGl6vZ8C1mqv+GGIJgNxVWhEn4F2v10PdvT8COq08d4LXr51BgzudHVw6WRZwve5uTRecyt5+8NJIqTQ5EdCaHzvwuF0BDf953krpdyW4XIEhXC4npXtKaHse+Apwu1w01dcBaBarliWBUQDtbc+DZtfguI/HFRjS43bT6HgY1M/fLX/5HpTIkBgkArxqfxHUMDJ6OBFRMQHXIyKj+90RA4mz4yUAQopoqaRSvungN/OolFQTOmlBdXqKFIgnwKCZdTu3B3eeYALSFh4JgFBGmzQM1QwQFTMiqGFiSnpwHROQsfEJABjgkJpStYCeODoda0jIoIZmXkGSfXBIa2goSakZALrXrVfLrrbpisUyjPRxbwc0jB4xkrCIyKAAtvAIokeMDLieMW6Sr2sSVG5ZObdVAijBcYDJM3ICGpoprV8Gq9Sk6bN8D4oT+I9ir8u7D2jPnDCF1MzxAztNDl7+YLr2jLFkTpgM8NIq5YFugC0r57YKQTHA3EVFaLJ/0zuU7TVQtTSpseCjFf7hl58XZLfQsx9wdXSWAA1J9nRyC1f1MhZCkJAy2jRAgj0NIXqfK7lL15DoS6IhNMRW4p/vBti6JrcDgwKgc2r2PGbO+7DbOCY2HltYhGkAmy28VxMzc34eU997H6WU0xAq/7NF0zr7AQBsKMqpVkJ8Aqj5i5eT9/FaNKmRaGL79ZVRKWloUiOvaD3zFy0DUELIdZsKZ1/vqTfg+bujvHKZUGovENbc4KD+/h1Tp2BPedL4iNSxE0hMSUMp5RRCrtuwdFZpX72AF8DO0stZSI4AdoCHtTepuVpJ3d0a3AFuRGtICBnjJzFpeo7/awdoMITK75t5UACA3SevhXU1kNuAKACv10NTfR2tLU9wOX2vMtQWTmx8Asmpb6JZLH7zduCr0BBbSc93PiQAv+wqrYjz9XBiCajswX9ORRWo48JQP/zvn9OBZPvBSyMtVi3L10zgv9baDXB43Xr1lpVzW4fi719svPLHzcibWQAAAABJRU5ErkJggg==',
    ico: 'data:image/x-icon;base64,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',
    apple: 'data:image/png;base64,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',
  };
  /* END PIVAULT FAVICONS */
  const links = [
    make('link', {id: 'pv-icon', rel: 'icon', type: 'image/svg+xml', href: FAVICON.svg}),
    make('link', {rel: 'icon', type: 'image/png', sizes: '16x16', href: FAVICON.png16}),
    make('link', {rel: 'icon', type: 'image/png', sizes: '32x32', href: FAVICON.png32}),
    make('link', {rel: 'icon', type: 'image/x-icon', sizes: '48x48', href: FAVICON.ico}),
    make('link', {rel: 'apple-touch-icon', sizes: '180x180', href: FAVICON.apple}),
    make('link', {rel: 'manifest', href: '/manifest.webmanifest'}),
  ];
  const apply = () => {
    document.head.querySelectorAll('link[rel~="icon"],link[rel="apple-touch-icon"]').forEach(link => {
      if (!links.includes(link)) link.remove();
    });
    links.forEach(link => { if (!link.isConnected) document.head.append(link); });
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
    'Documents': 'documents', 'Offline Wiki': 'offline-wiki', 'Photos': 'photos'};
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
