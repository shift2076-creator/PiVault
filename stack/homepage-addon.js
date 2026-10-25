/* Add after the existing custom.js. The API must be installed before enabling. */
(() => {
  if (window.piVaultUsersLoaded) return;
  window.piVaultUsersLoaded = true;
  let admin = false;
  function update() {
    const present = document.getElementById('pivault-add-user');
    if (!admin) { if (present) present.remove(); return; }
    if (present) return;
    const source = document.getElementById('pivault-file-sync')?.closest('.service');
    if (!source) return;
    const item = source.cloneNode(true);
    item.removeAttribute('id');
    item.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    item.id = 'pivault-add-user';
    item.querySelectorAll('a').forEach(el => {
      el.href = '/users/add';
      el.removeAttribute('target');
      el.removeAttribute('rel');
      el.removeAttribute('aria-label');
    });
    const name = item.querySelector('.service-name');
    if (name) name.textContent = 'Add User';
    item.querySelectorAll('.service-description').forEach(el => el.remove());
    const icon = item.querySelector('.service-icon');
    if (icon) {
      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('viewBox', '0 0 24 24');
      svg.setAttribute('fill', 'none');
      svg.setAttribute('stroke', '#91acc3');
      svg.setAttribute('stroke-width', '1.7');
      svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', 'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6M17 11h6M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0');
      svg.append(path);
      icon.replaceChildren(svg);
    }
    source.parentElement.append(item);
  }
  async function refresh() {
    try {
      const response = await fetch('/users/session', {credentials:'same-origin', cache:'no-store'});
      const state = response.ok ? await response.json() : {};
      admin = state.authenticated === true && state.admin === true;
    } catch { admin = false; }
    update();
  }
  const observer = new MutationObserver(update);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  refresh();
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
      #pv-admin-notifications{position:fixed;top:clamp(20px,3vw,40px);right:var(--pv-gutter,24px);z-index:1000;color:var(--pv-text,#e8edf2);font:15px/1.45 Arial,Helvetica,sans-serif}
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
    panel.append(heading,summary,list,all); root.append(style,button,panel); document.body.append(root);
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

/* Unified search: a field under the PiVault title and a tickbox before each searchable app.
   The portal decides which apps this user may search; unticked apps are never queried.
   The field sits in the launcher's own "title" grid area, which every launcher layout keeps,
   so it follows the launcher's gutters and breakpoints. */
(() => {
  if (window.piVaultSearchLoaded) return;
  window.piVaultSearchLoaded = true;
  const STORE = 'pivault-search-unticked';
  const CHECK = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.5 8.5l3 3 6-7' fill='none' stroke='%23151c22' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\")";
  let apps = [], bar = null, hint = null, queued = false;
  const unticked = () => { try { return new Set(JSON.parse(localStorage.getItem(STORE) || '[]')); } catch { return new Set(); } };
  const remember = set => { try { localStorage.setItem(STORE, JSON.stringify([...set])); } catch { /* Per-browser convenience only. */ } };
  const make = (tag, props = {}) => Object.assign(document.createElement(tag), props);
  const style = make('style', {id: 'pv-search-style'});
  // The repeated ID outranks the launcher CSS (body:has(#pivault-books) #layout-groups ...).
  const L = '#layout-groups#layout-groups#layout-groups';
  style.textContent = `
    ${L} > #pv-search{grid-area:title;align-self:start;display:flex;flex-wrap:wrap;align-items:center;gap:10px 12px;
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
    #pv-search .pv-hint{flex:1 1 100%;color:var(--pv-secondary,#91acc3);font-size:14px}
    #pv-search .pv-hint button{padding:0 2px;border:0;background:none;color:var(--pv-accent,#b4c0ce);font:inherit;text-decoration:underline;cursor:pointer}
    #pv-search :focus-visible,.pv-pick input:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:3px}
    ${L} .services-group.pv-picks .service{position:relative!important;padding-left:30px!important}
    .pv-pick{position:absolute;left:0;top:0;bottom:0;z-index:40;display:flex;align-items:center;width:26px;cursor:pointer}
    .pv-pick input{appearance:none;-webkit-appearance:none;width:18px;height:18px;margin:0;border:1.5px solid var(--pv-secondary,#91acc3);
      border-radius:4px;background:transparent;cursor:pointer;color:transparent;box-shadow:none}
    .pv-pick input:checked{background:var(--pv-secondary,#91acc3) ${CHECK} center/14px no-repeat;border-color:var(--pv-secondary,#91acc3)}
  `;
  function mountBar() {
    const host = document.getElementById('layout-groups');
    if (!host || bar?.isConnected) return;
    if (!style.isConnected) document.head.append(style);
    bar = make('form', {id: 'pv-search', method: 'get', action: '/search', role: 'search'});
    const field = make('label', {className: 'pv-field'});
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    for (const [k, v] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'1.8', 'stroke-linecap':'round', 'aria-hidden':'true'})) svg.setAttribute(k, v);
    const lens = document.createElementNS(ns, 'path'); lens.setAttribute('d', 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4'); svg.append(lens);
    const input = make('input', {type: 'search', name: 'q', required: true, minLength: 2, maxLength: 200,
      autocomplete: 'off', placeholder: 'Search ' + apps.map(a => a.label).join(', ')});
    input.setAttribute('aria-label', 'Search PiVault');
    field.append(svg, input);
    hint = make('div', {className: 'pv-hint'});
    hint.setAttribute('aria-live', 'polite');
    bar.append(field, make('button', {type: 'submit', className: 'pv-go', textContent: 'Search'}), hint);
    bar.addEventListener('submit', event => {
      if (!document.querySelector('.pv-pick input:checked')) {
        event.preventDefault(); showHint('Tick at least one app to search.');
      }
    });
    host.prepend(bar);
    showHint();
  }
  function showHint(message) {
    if (!hint) return;
    hint.replaceChildren(message || 'Tick the apps below to choose where to search: ');
    if (message) return;
    for (const [text, value] of [['All', true], ['None', false]]) {
      const b = make('button', {type: 'button', textContent: text});
      b.addEventListener('click', () => setAll(value));
      hint.append(b, ' ');
    }
  }
  function setAll(value) {
    remember(value ? new Set() : new Set(apps.map(a => a.key)));
    document.querySelectorAll('.pv-pick input').forEach(box => { box.checked = value; });
  }
  function decorate() {
    queued = false;
    if (!apps.length) return;
    mountBar();
    const off = unticked();
    for (const app of apps) {
      const tile = document.querySelector(`li.service[data-name="${CSS.escape(app.label)}"]`);
      if (!tile || tile.querySelector(':scope > .pv-pick')) continue;
      const pick = make('label', {className: 'pv-pick', title: 'Include ' + app.label + ' in search'});
      const box = make('input', {type: 'checkbox', name: 'app', value: app.key, checked: !off.has(app.key)});
      box.setAttribute('form', 'pv-search');
      box.setAttribute('aria-label', 'Search ' + app.label);
      box.addEventListener('change', () => {
        const set = unticked(); box.checked ? set.delete(app.key) : set.add(app.key); remember(set);
      });
      pick.append(box);
      tile.prepend(pick);
      // Indent every tile in this column alike, so tiles without a tickbox stay aligned.
      tile.closest('.services-group')?.classList.add('pv-picks');
    }
  }
  const observer = new MutationObserver(() => {
    if (!queued) { queued = true; requestAnimationFrame(decorate); }
  });
  async function start() {
    try {
      const response = await fetch('/search/apps', {credentials: 'same-origin', cache: 'no-store'});
      apps = response.ok ? ((await response.json()).apps || []) : [];
    } catch { apps = []; }
    if (!apps.length) return;  // Signed out, or nothing this account may search.
    decorate();
    observer.observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
