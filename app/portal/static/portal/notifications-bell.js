/* Keep this bell in sync with stack/homepage-addon.js, which serves the Homepage launcher.
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
      #pv-admin-notifications{position:fixed;top:clamp(20px,3vw,40px);right:var(--pv-gutter,24px);z-index:1000;color:var(--pv-text,#e7ecf2);font:14px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
      #pv-admin-notifications [hidden]{display:none!important}
      #pv-admin-notifications .pv-bell{position:relative;display:inline-flex;align-items:center;gap:8px;height:auto;padding:0;border:0;background:transparent;color:var(--pv-text,#e7ecf2);font:inherit;font-size:14px;cursor:pointer}
      #pv-admin-notifications .pv-bell:hover{text-decoration:underline;text-underline-offset:4px}
      #pv-admin-notifications .pv-bell:focus-visible,#pv-admin-notifications a:focus-visible{outline:2px solid var(--pv-accent,#b4c0ce);outline-offset:4px}
      #pv-admin-notifications .pv-count{min-width:20px;padding:1px 6px;border-radius:999px;background:var(--pv-accent,#6cc0e6);color:var(--pv-detail,#1c222b);font-size:12px;font-weight:600;text-align:center}
      #pv-admin-notifications .pv-panel{position:absolute;right:0;top:34px;width:min(440px,calc(100vw - 48px));max-height:min(620px,calc(100dvh - 100px));overflow:auto;padding:22px;border:1px solid var(--pv-menu-border,#3a4553);border-radius:.5em;background:var(--pv-background,#151c22);box-shadow:0 10px 30px #0005}
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
