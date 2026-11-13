/* Bitwarden PBKDF2 / AES-256-CBC-HMAC-SHA256 wire format. WebCrypto only.
   Protocol references and independent compatibility tests are in VAULT.md.

   Shared logins: the admin's browser reads its own entries (unlockEntries) and seals the chosen ones with a key
   derived from a random secret that rides only in the QR's URL fragment (seal/open). The server never has the key. */
(() => {
  'use strict';
  const utf8 = value => new TextEncoder().encode(value);
  const utf8Decode = value => new TextDecoder().decode(value);
  const b64 = value => btoa(String.fromCharCode(...new Uint8Array(value)));
  const unb64 = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
  const concat = (...values) => {
    const result = new Uint8Array(values.reduce((n, value) => n + value.length, 0));
    let offset = 0; for (const value of values) { result.set(value, offset); offset += value.length; } return result;
  };
  const same = (a, b) => {
    if (a.length !== b.length) return false;
    let diff = 0; for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i]; return diff === 0;
  };
  async function hmac(key, value) {
    const imported = await crypto.subtle.importKey('raw', key, {name:'HMAC',hash:'SHA-256'}, false, ['sign']);
    return new Uint8Array(await crypto.subtle.sign('HMAC', imported, value));
  }
  async function pbkdf2(password, salt, iterations) {
    const key = await crypto.subtle.importKey('raw', password, 'PBKDF2', false, ['deriveBits']);
    return new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt,iterations},key,256));
  }
  // HKDF-Expand, not HKDF-Extract: each 32-byte output is one HMAC block.
  async function stretch(master) {
    return concat(await hmac(master,concat(utf8('enc'),new Uint8Array([1]))),
                  await hmac(master,concat(utf8('mac'),new Uint8Array([1]))));
  }
  async function encrypt(value, key) {
    const iv = crypto.getRandomValues(new Uint8Array(16));
    const aes = await crypto.subtle.importKey('raw', key.slice(0,32), 'AES-CBC', false, ['encrypt']);
    const cipher = new Uint8Array(await crypto.subtle.encrypt({name:'AES-CBC',iv},aes,value));
    const mac = await hmac(key.slice(32,64), concat(iv,cipher));
    return `2.${b64(iv)}|${b64(cipher)}|${b64(mac)}`;
  }
  async function decrypt(value, key) {
    if (typeof value !== 'string' || !value.startsWith('2.')) throw new Error('Unexpected vault data.');
    const [iv, cipher, mac] = value.slice(2).split('|').map(unb64);
    if (!iv || !cipher || !mac || iv.length !== 16 || cipher.length === 0 || cipher.length % 16 !== 0) {
      throw new Error('Unexpected vault data.');
    }
    if (!same(await hmac(key.slice(32,64), concat(iv,cipher)), mac)) throw new Error('The vault key did not match.');
    const aes = await crypto.subtle.importKey('raw', key.slice(0,32), 'AES-CBC', false, ['decrypt']);
    return new Uint8Array(await crypto.subtle.decrypt({name:'AES-CBC',iv},aes,cipher));
  }
  async function decryptText(value, key) {
    return utf8Decode(await decrypt(value, key));
  }
  async function create(email, password, items, sharedEntries = {}) {
    email = email.trim().toLowerCase();
    const master = await pbkdf2(utf8(password),utf8(email),600000);
    const stretched = await stretch(master);
    const key = crypto.getRandomValues(new Uint8Array(64));
    const rsa = await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,
      publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']);
    const bundle = {masterPasswordHash:b64(await pbkdf2(master,utf8(password),1)),
      key:await encrypt(key,stretched), keys:{publicKey:b64(await crypto.subtle.exportKey('spki',rsa.publicKey)),
      encryptedPrivateKey:await encrypt(new Uint8Array(await crypto.subtle.exportKey('pkcs8',rsa.privateKey)),key)},
      folder:await encrypt(utf8('PiVault'),key),items:{}};
    for (const [service,item] of Object.entries(items)) {
      let fields;
      if (item.shared) {
        // A shared login: its name is fixed by the server, the rest comes from the admin's sealed copy.
        const entry = sharedEntries[service];
        if (!entry) throw new Error('The shared login for ' + item.name.replace('PiVault ', '') + ' is missing from this invitation.');
        fields = {username:entry.username, password:entry.password, uri:entry.uri || ''};
      } else {
        fields = {username:item.username, password:password, uri:item.uri};
      }
      bundle.items[service] = {name:await encrypt(utf8(item.name),key),
        username:await encrypt(utf8(fields.username),key), password:await encrypt(utf8(fields.password),key),
        uri:await encrypt(utf8(fields.uri),key)};
    }
    master.fill(0); stretched.fill(0); key.fill(0);
    return bundle;
  }

  // The admin's own entries: read by the admin's browser through the portal's narrow relay.
  const field = (object, upper, lower) => (object && (object[upper] ?? object[lower])) ?? null;
  async function unlockEntries(email, password, csrf) {
    email = email.trim().toLowerCase();
    const post = (path, body, headers) => fetch(path, {method:'POST', credentials:'same-origin',
      headers:{'X-CSRFToken':csrf, ...headers}, body});
    const pre = await post('/users/vault-proxy/prelogin', JSON.stringify({email}), {'Content-Type':'application/json'});
    if (!pre.ok) throw new Error('The Password Manager did not answer. Try again.');
    const prelogin = await pre.json();
    if (Number(field(prelogin, 'Kdf', 'kdf')) !== 0) throw new Error('This account uses a key-derivation type PiVault does not read.');
    const iterations = Number(field(prelogin, 'KdfIterations', 'kdfIterations'));
    if (!Number.isInteger(iterations) || iterations < 100000) throw new Error('The account iteration count is too low to use.');
    const master = await pbkdf2(utf8(password), utf8(email), iterations);
    const hash = b64(await pbkdf2(master, utf8(password), 1));
    const token = await post('/users/vault-proxy/token', new URLSearchParams({
      grant_type:'password', username:email, password:hash, scope:'api offline_access', client_id:'web',
      deviceType:'9', deviceIdentifier:crypto.randomUUID(), deviceName:'PiVault share'}),
      {'Content-Type':'application/x-www-form-urlencoded'});
    if (!token.ok) throw new Error('Your master password was not accepted.');
    const login = await token.json();
    const bearer = field(login, 'access_token', 'access_token');
    const wrapped = field(login, 'Key', 'key');
    if (typeof bearer !== 'string' || typeof wrapped !== 'string') throw new Error('The Password Manager sent an unreadable sign-in.');
    const stretched = await stretch(master);
    const userKey = await decrypt(wrapped, stretched);
    if (userKey.length !== 64) throw new Error('The account key has an unexpected length.');
    const sync = await fetch('/users/vault-proxy/sync', {credentials:'same-origin',
      headers:{'Authorization':'Bearer ' + bearer, 'Accept':'application/json'}});
    if (!sync.ok) throw new Error('Could not read your vault.');
    const data = await sync.json();
    const entries = [];
    for (const cipher of (field(data, 'Ciphers', 'ciphers') || [])) {
      if (Number(field(cipher, 'Type', 'type')) !== 1 || field(cipher, 'OrganizationId', 'organizationId')) continue;
      const login = field(cipher, 'Login', 'login');
      if (!login) continue;
      const name = field(cipher, 'Name', 'name');
      if (typeof name !== 'string') continue;
      const uris = field(login, 'Uris', 'uris') || [];
      const uri = uris.length ? field(uris[0], 'Uri', 'uri') : null;
      entries.push({
        name: await decryptText(name, userKey),
        username: await optionalText(field(login, 'Username', 'username'), userKey),
        password: await optionalText(field(login, 'Password', 'password'), userKey),
        uri: await optionalText(uri, userKey),
      });
    }
    master.fill(0); stretched.fill(0); userKey.fill(0);
    return entries;
  }
  async function optionalText(value, key) {
    return typeof value === 'string' ? decryptText(value, key) : '';
  }

  // The sealing key comes only from the random secret in the QR fragment.
  async function sealKey(secret) {
    // The server issues 32 random bytes as 43 base64url characters.
    if (typeof secret !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(secret)) throw new Error('The QR code key is not valid.');
    const bytes = unb64(secret.replace(/-/g, '+').replace(/_/g, '/') + '=');
    if (bytes.length !== 32) throw new Error('The QR code key is not valid.');
    const key = await stretch(bytes);
    bytes.fill(0);
    return key;
  }
  async function seal(entries, secret) {
    const key = await sealKey(secret);
    const sealed = await encrypt(utf8(JSON.stringify(entries)), key);
    key.fill(0);
    return sealed;
  }
  async function open(sealed, secret) {
    const key = await sealKey(secret);
    const entries = JSON.parse(utf8Decode(await decrypt(sealed, key)));
    key.fill(0);
    return entries;
  }

  globalThis.PiVaultVaultCrypto = {create, unlockEntries, seal, open};
  if (typeof document === 'undefined') return;

  // Read the QR secret once, then remove it from the address bar and history.
  const fragmentMatch = /[#&]k=([A-Za-z0-9_-]{20,})/.exec(location.hash || '');
  const joinSecret = fragmentMatch ? fragmentMatch[1] : '';
  if (fragmentMatch) history.replaceState(null, '', location.pathname + location.search);

  const form = document.querySelector('body[data-mode="join"] form');
  if (!form) return;
  let busy = false;
  form.addEventListener('submit',async event => {
    event.preventDefault(); if (busy) return;
    busy = true;
    const button = form.querySelector('button[type="submit"]');
    const error = document.getElementById('vault-setup-error');
    button.disabled = true; error.hidden = true;
    try {
      const email = form.elements.email.value.trim().toLowerCase();
      const response = await fetch('/join/plan',{method:'POST',credentials:'same-origin',
        headers:{'X-CSRFToken':form.elements.csrfmiddlewaretoken.value,'Content-Type':'application/x-www-form-urlencoded'},
        body:new URLSearchParams({code:form.elements.code ? form.elements.code.value : '',email})});
      const plan = await response.json();
      if (!response.ok) throw new Error(plan.error || 'Could not prepare account setup.');
      if (plan.vault_required) {
        button.textContent = 'Creating your vault…';
        let sharedEntries = {};
        if (Object.values(plan.items).some(item => item.shared)) {
          if (!joinSecret) throw new Error('Scan the QR code from the admin to join. The code alone cannot open the shared logins.');
          sharedEntries = await globalThis.PiVaultVaultCrypto.open(plan.shared, joinSecret)
            .catch(() => { throw new Error('The shared logins could not be opened. Scan the QR code again.'); });
        }
        const bundle = await create(email,form.elements.password.value,plan.items,sharedEntries);
        form.elements.vault_bundle.value = JSON.stringify({token:plan.token,bundle});
      }
      HTMLFormElement.prototype.submit.call(form);
    } catch (failure) {
      error.textContent = failure.message || 'Account setup could not finish. Please try again.';
      error.hidden = false; button.disabled = false; button.textContent = 'Join PiVault'; busy = false;
    }
  });
})();
