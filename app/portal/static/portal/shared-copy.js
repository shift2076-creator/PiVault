/* Admin only: read this admin's own Vaultwarden entries in this browser, pick one login per shared service,
   seal them with the key from the QR fragment, and attach the sealed copy to the open invitation.
   The master password and the plaintext entries never leave this page. */
(() => {
  'use strict';
  const root = document.getElementById('shared-copy');
  if (!root) return;
  const SLOTS = [['books', 'Books'], ['games', 'Games'], ['music', 'Music'], ['videos', 'Videos'],
    ['photos', 'Photos'], ['radicale', 'Calendar and contacts']];
  const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
  const csrf = csrfInput ? csrfInput.value : '';
  const status = document.getElementById('shared-status');
  const openButton = document.getElementById('shared-open');
  const master = document.getElementById('shared-master');
  const picker = document.getElementById('shared-pick');
  let entries = null;

  const say = text => { status.textContent = text; };

  function showPicker() {
    picker.textContent = '';
    picker.hidden = false;
    const selects = {};
    for (const [key, label] of SLOTS) {
      const wrap = document.createElement('div');
      const name = document.createElement('label');
      name.textContent = label + ' login';
      const select = document.createElement('select');
      select.dataset.slot = key;
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = 'Choose an entry';
      select.appendChild(blank);
      entries.forEach((entry, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = entry.name + (entry.username ? ' · ' + entry.username : '');
        select.appendChild(option);
      });
      name.appendChild(select);
      wrap.appendChild(name);
      picker.appendChild(wrap);
      selects[key] = select;
    }
    const attach = document.createElement('button');
    attach.type = 'button';
    attach.textContent = 'Seal and attach to this code';
    attach.addEventListener('click', () => attachCopy(selects));
    picker.appendChild(attach);
  }

  async function attachCopy(selects) {
    const chosen = {};
    for (const [key, label] of SLOTS) {
      const index = selects[key].value;
      if (index === '') { say('Choose an entry for ' + label + '.'); return; }
      const entry = entries[Number(index)];
      chosen[key] = { name: entry.name, username: entry.username, password: entry.password, uri: entry.uri };
    }
    say('Sealing…');
    try {
      const sealed = await PiVaultVaultCrypto.seal(chosen, root.dataset.key);
      const response = await fetch('/users/add/shared', {
        method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({code: root.dataset.code, sealed}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The shared logins could not be attached.');
      entries = null;
      root.removeAttribute('data-key');
      picker.hidden = true;
      say('Attached. The new person must scan the QR code within 30 minutes.');
    } catch (failure) {
      say(failure.message || 'The shared logins could not be attached.');
    }
  }

  openButton.addEventListener('click', async () => {
    if (!master.value) { say('Type your master password first.'); return; }
    openButton.disabled = true;
    say('Unlocking your vault…');
    try {
      entries = await PiVaultVaultCrypto.unlockEntries(root.dataset.email, master.value, csrf);
      master.value = '';
      if (!entries.length) { say('No login entries were found in your vault.'); return; }
      showPicker();
      say(entries.length + ' login entries found. Pick one for each shared service.');
    } catch (failure) {
      say(failure.message || 'Could not unlock your vault.');
    } finally {
      openButton.disabled = false;
    }
  });
})();
