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
    svg: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAzMiAzMiI+PHJlY3Qgd2lkdGg9IjMyIiBoZWlnaHQ9IjMyIiByeD0iOCIgZmlsbD0iIzIwMmEzNSIvPjxzdmcgeD0iMyIgeT0iMyIgd2lkdGg9IjI2IiBoZWlnaHQ9IjI2IiB2aWV3Qm94PSIwIDAgMTIwIDEyMCIgZmlsbD0ibm9uZSI+PHJlY3QgeD0iOCIgeT0iMjAiIHdpZHRoPSIxMDQiIGhlaWdodD0iODAiIHJ4PSIxMCIgc3Ryb2tlPSIjOTFhY2MzIiBzdHJva2Utd2lkdGg9IjExIi8+PHBhdGggZD0iTTUzIDYwQTEzIDEzIDAgMSAxIDY3IDYwTDcyIDg2SDQ4WiIgc3Ryb2tlPSIjOTFhY2MzIiBzdHJva2Utd2lkdGg9IjExIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+PC9zdmc+PC9zdmc+Cg==',
    png16: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABmJLR0QA/wD/AP+gvaeTAAABnElEQVQ4jZ2SX0uaYRiHr/d5H802jJE2l5s1kiJCMHBRMGgwao7GajAYne4z7KRv0TfoNPoDER0EQUEdZWSFFPVCrBNBJ4ibb2463z87UKSC12W/w4f7uvjx3LcCEI6MhCyLeRveAV6aRwd7WzGZu9IONSUcGQmZFidA53/Auymogqj6xP98AYi1CAO02zYvZL02b97PMBiNIVS1KWWZJhepJLub6wBxCXh9gW6Gx8bZWFzAMKqNYZfLDUC1+rfxJl1uPs5+5TSZIJ/LdkgAT5sHyzT4rp01BiOxUd5++IwN7GyscnZ8cKOFgcfTXhM6VR2PT7O1voRQBK8npm4JbsZRUNJ1ugJBhCq4Lv50/BNHwUXqiJ5wPwCX56etC/xPA+QyabCh61mwdYEv0E2wtw+ASvlPc0G5Ukaokr7BCEZ9ZeepJI8e1676d0mnJzxQA1xuhCop16US0PM/Mt6TxB6TM1/udUjH+3vkc1lQ+KW8HHq1BsqnppRzloViMgcUHgDnpRTfxJV2qKmCKLACFO8BFoFlKcXwZSqR/gc7gIZJfWoqEwAAAABJRU5ErkJggg==',
    png32: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABmJLR0QA/wD/AP+gvaeTAAADxUlEQVRYhb2X30+bVRjHP8/bt31HKXMFWn502gplqx1EDTCiQ7eBI9uFId6ZGGMMRi+8MFm88g/wTt3FLkzcjInG6J0xJkvMhprsl7NVohQW2aQsUFm7sUlLE6B9jxelWKAzvtjyvTrv85z3+Xxzzsn5IWxSW6i7y9S014BBIADUbu5jUUtAHOG8zVRnbk5GxkuTUmwEg0EjZ7g/QPEGoP1PaHkJeTH50KllT8ZisZV1A8Fg0Mg59pwDOVoV8FaN1kr2RCwWW9EAco49p3YQDjCQUbXvAcjanI9VbdgfJCFvKh7X1hbczsIBFDZNyYgOPFca13WdnmcGCQRDOF2uirCymQzxqUl+ujhKPpf7x4OoY+IPH0wLygUgIrzw8uv4g/srAt6s+NR1vvrsI5RSxVBaL8IBfP72dfiNid+4t5CqCNhd7yEY7iLQEcLnb2M2frOYqtNLO3qaWwBYXV7mmy8/KXW6Rc5aF817/QDMz86QXco8sK+I8OY772I3DDzNraUG2GDAphc+c/ncv8K7nz7MU4MnsNsdBcOrK1y+cI6fL/9Qtr9Silw+hx1jnVGU5dW/v+tJnj0+vA4HsNsdHD4+zL7OJ6yW2zgC/0U9hwr7VWo+wdeffwzA8EsjNDa10Ns/wO/jY5bqWR6Bek8TAOORqyzeX2Dx/gK/Rq4C4PZ4rZazbiCT/guAh9s6EBFEBH97BwBLi4vVNzDxSwSAYLiLviND9B0Zoj3UWciNXau+geilUZYyaQBcdbtx1e0GYCmTJnLpe8sGLC9CRKhxFu4oyT/nkLUbRY3ThSYaeYvlLI9AY1Mrmlb4LZmY5XZirlBIExq8zVbLWTfQ1OoDwDQVd5Pz3LmdwDRNALytey0bsDwFnuaCgdzqMn1Hh9baKziMXXhbfNU38FC9BwCHsYve/oGyuW0bKJ7Vuk1HRMqeB7HoFQzDAbJp9pRJLHqlLERE0G36BkZZA6n5BAB2w+D5F19l4W6ybMFb0zfKxhtbfPSXmYb6Bi92wwAgOT+3xUAaqAOYm/mD+NR1Ah0h2h/rpL0sZvuanpokMTNdGlqUQLg3BoSLEZuu03NogEf3hXC66ioCzmbSTE9NErn43cYrGYxL4EDvKRRvVYRkUYJ6X7OZ6gxieQOrBD2fF9tZ2707iaS70edFOLjDFk7PTFz7VANwatmTAhd2iqzgfEON+TaADSCVSuV9XvcXKzjcInRX83EKnG6sMV+JRqOrlL6Oi3ok3HtAUzKiRB0DCZRe27cjhWRAxTXUt3mxnb0V+3GiNP83lSg2icZwjccAAAAASUVORK5CYII=',
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
