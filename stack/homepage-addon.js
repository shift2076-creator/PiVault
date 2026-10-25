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

/* Settings menu (Admin only): holds Add User. The server, not the browser, enforces Admin access. */
(() => {
  if (window.piVaultUsersLoaded) return;
  window.piVaultUsersLoaded = true;
  let root, button, panel, opened = false;
  const ns = 'http://www.w3.org/2000/svg';
  // Gear and user-plus outlines from Feather Icons (MIT License, compatible with this project).
  const GEAR = 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z';
  const USER_PLUS = 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6M17 11h6M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0';
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
      #pv-settings{position:relative;order:2;color:var(--pv-text,#e8edf2);font:15px/1.45 Arial,Helvetica,sans-serif}
      #pv-settings [hidden]{display:none!important}
      #pv-settings .pv-gear{display:grid;place-items:center;width:46px;height:46px;padding:0;border:0;border-radius:.5em;background:transparent;color:var(--pv-secondary,#91acc3);cursor:pointer}
      #pv-settings .pv-gear:hover,#pv-settings .pv-gear[aria-expanded=true]{color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
      #pv-settings .pv-menu{position:absolute;right:0;top:54px;min-width:220px;padding:8px;border:1px solid var(--pv-divider,rgba(151,170,190,.48));border-radius:.5em;background:var(--pv-background,#151c22);box-shadow:0 10px 30px #0005}
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
    const add = document.createElement('a'); add.href = '/users/add';
    add.append(icon(USER_PLUS, 20), document.createTextNode('Add User'));
    panel.append(heading, add);
    root.append(style, button, panel);
    window.piVaultToolbar().append(root);
    button.addEventListener('click', () => {
      if (opened) { close(); return; }
      opened = true; panel.hidden = false; button.setAttribute('aria-expanded', 'true');
    });
  }
  function hide() { root?.remove(); root = null; opened = false; }
  async function refresh() {
    let admin = false;
    try {
      const response = await fetch('/users/session', {credentials:'same-origin', cache:'no-store'});
      const state = response.ok ? await response.json() : {};
      admin = state.authenticated === true && state.admin === true;
    } catch { admin = false; }
    if (admin) mount(); else hide();
  }
  document.addEventListener('click', event => { if (root && !root.contains(event.target)) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && opened) { close(); button?.focus(); } });
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh, {once:true});
  else refresh();
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

/* Launcher columns: Entertainment spans two columns of two tiles, so the launcher shows
   five columns of two rows wherever five fit. Narrower screens keep Homepage's layout. */
(() => {
  if (window.piVaultColumnsLoaded) return;
  window.piVaultColumnsLoaded = true;
  const style = document.createElement('style');
  style.id = 'pv-columns';
  style.textContent = `
    @media (min-width:1280px){
      .pv-five{display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr));align-items:start}
      .pv-five>.services-group{flex-basis:auto!important;max-width:none!important;min-width:0}
      .pv-five>.pv-media{grid-column:span 2}
      .pv-media .services-list{display:grid!important;grid-template-rows:repeat(2,auto);grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);column-gap:1.5rem}
    }
  `;
  let queued = false;
  function apply() {
    queued = false;
    if (!style.isConnected) document.head.append(style);
    for (const group of document.querySelectorAll('.services-group:not(.subgroup)')) {
      const name = group.querySelector('.service-group-name')?.textContent.trim().toLowerCase();
      if (name !== 'entertainment' || group.classList.contains('pv-media')) continue;
      group.classList.add('pv-media');
      group.parentElement?.classList.add('pv-five');
    }
  }
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(apply); } })
    .observe(document.documentElement, {childList: true, subtree: true});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply, {once: true});
  else apply();
})();
