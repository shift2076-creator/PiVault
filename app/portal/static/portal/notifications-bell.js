/* Keep this bell in sync with stack/homepage-addon.js, which serves the Homepage launcher.
   The same admin inbox is implemented in both places, so each change must land in both. */
/* PiVault's shared admin inbox. The server, not the browser, enforces Admin access. */
(() => {
  if (window.piVaultNotificationsLoaded) return;
  window.piVaultNotificationsLoaded = true;
  let root, button, offlineButton, badge, panel, list, summary, state, busy = false, opened = false, offlineBusy = false;
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
      #pv-admin-notifications{position:fixed;top:clamp(20px,3vw,40px);right:var(--pv-gutter,24px);z-index:1000;display:flex;align-items:center;gap:8px;color:var(--pv-text,#e8edf2);font:15px/1.45 Arial,Helvetica,sans-serif}
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
      #pv-admin-notifications .pv-offline{display:grid;place-items:center;min-width:40px;height:46px;padding:0 9px;border:1px solid transparent;border-radius:.5em;background:transparent;color:var(--pv-secondary,#91acc3);cursor:pointer;font:700 12px/1 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;letter-spacing:.08em}
      #pv-admin-notifications .pv-offline:hover{color:var(--pv-text,#e8edf2);background:rgba(151,170,190,.1)}
      #pv-admin-notifications .pv-offline:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:4px}
      #pv-admin-notifications .pv-offline:disabled{opacity:.6;cursor:progress}
      #pv-admin-notifications .pv-offline-on{border-color:#e0b25c;color:#f2c46d;background:rgba(224,178,92,.12)}
      #pv-admin-notifications .pv-offline-on:hover{color:#ffd98a;background:rgba(224,178,92,.2)}
    `;
    offlineButton = make('button', undefined, 'pv-offline');
    offlineButton.type = 'button'; offlineButton.setAttribute('aria-label', 'Offline mode');
    offlineButton.setAttribute('aria-pressed', 'false');
    offlineButton.addEventListener('click', toggleOffline);
    button = make('button', undefined, 'pv-bell');
    button.type = 'button'; button.setAttribute('aria-expanded', 'false');
    button.hidden = true;  // Shown only while there are open notices.
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
    panel.append(heading,summary,list,all); root.append(style,offlineButton,button,panel); document.body.append(root);
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
  // Feather globe (MIT License). Online shows the globe; offline shows the word OFFLINE.
  const GLOBE = 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z';
  function globeIcon() {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    for (const [name, value] of Object.entries({viewBox:'0 0 24 24', width:'22', height:'22', fill:'none', stroke:'currentColor', 'stroke-width':'1.7', 'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true'})) svg.setAttribute(name, value);
    const path = document.createElementNS(ns, 'path'); path.setAttribute('d', GLOBE); svg.append(path);
    return svg;
  }
  function drawOffline() {
    const offline = state.offline === true;
    offlineButton.className = offline ? 'pv-offline pv-offline-on' : 'pv-offline';
    offlineButton.setAttribute('aria-pressed', String(offline));
    offlineButton.title = offline ? 'Offline mode is on. Click to turn it off.' : 'Online. Click to turn offline mode on.';
    offlineButton.replaceChildren(offline ? document.createTextNode('OFFLINE') : globeIcon());
  }
  async function toggleOffline() {
    if (!state || offlineBusy) return;
    offlineBusy = true; offlineButton.disabled = true;
    const body = new URLSearchParams({offline: state.offline === true ? 'off' : 'on'});
    try {
      const response = await fetch('/users/offline-mode', {method:'POST', credentials:'same-origin',
        headers:{'X-CSRFToken':state.csrf_token}, body});
      if (!response.ok) throw new Error(String(response.status));
      state.offline = (await response.json()).offline === true;
      drawOffline();
    } catch {
      await refresh(); // A failed change shows the server's own state.
    } finally {
      offlineBusy = false; offlineButton.disabled = false;
    }
  }
  function draw() {
    if (!state) return;
    mount();
    drawOffline();
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
