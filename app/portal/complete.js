(() => {
  'use strict';
  const button = document.getElementById('copy-server');
  const server = document.getElementById('vault-server');
  const status = document.getElementById('copy-server-status');
  if (!button || !server || !status) return;
  button.hidden = false;
  button.addEventListener('click', async () => {
    status.textContent = '';
    try {
      await navigator.clipboard.writeText(server.value.trim());
      status.textContent = 'Server address copied.';
    } catch {
      server.focus();
      server.select();
      status.textContent = 'Copy the selected server address.';
    }
  });
})();
