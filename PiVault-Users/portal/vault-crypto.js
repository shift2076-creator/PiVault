/* Bitwarden PBKDF2 / AES-256-CBC-HMAC-SHA256 wire format. WebCrypto only.
   Protocol references and independent compatibility tests are in VAULT.md. */
(() => {
  'use strict';
  const utf8 = value => new TextEncoder().encode(value);
  const b64 = value => btoa(String.fromCharCode(...new Uint8Array(value)));
  const concat = (...values) => {
    const result = new Uint8Array(values.reduce((n, value) => n + value.length, 0));
    let offset = 0; for (const value of values) { result.set(value, offset); offset += value.length; } return result;
  };
  async function hmac(key, value) {
    const imported = await crypto.subtle.importKey('raw', key, {name:'HMAC',hash:'SHA-256'}, false, ['sign']);
    return new Uint8Array(await crypto.subtle.sign('HMAC', imported, value));
  }
  async function pbkdf2(password, salt, iterations) {
    const key = await crypto.subtle.importKey('raw', password, 'PBKDF2', false, ['deriveBits']);
    return new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt,iterations},key,256));
  }
  async function encrypt(value, key) {
    const iv = crypto.getRandomValues(new Uint8Array(16));
    const aes = await crypto.subtle.importKey('raw', key.slice(0,32), 'AES-CBC', false, ['encrypt']);
    const cipher = new Uint8Array(await crypto.subtle.encrypt({name:'AES-CBC',iv},aes,value));
    const mac = await hmac(key.slice(32,64), concat(iv,cipher));
    return `2.${b64(iv)}|${b64(cipher)}|${b64(mac)}`;
  }
  async function create(email, password, items) {
    email = email.trim().toLowerCase();
    const master = await pbkdf2(utf8(password),utf8(email),600000);
    // HKDF-Expand, not HKDF-Extract: each 32-byte output is one HMAC block.
    const stretched = concat(await hmac(master,concat(utf8('enc'),new Uint8Array([1]))),
                             await hmac(master,concat(utf8('mac'),new Uint8Array([1]))));
    const key = crypto.getRandomValues(new Uint8Array(64));
    const rsa = await crypto.subtle.generateKey({name:'RSA-OAEP',modulusLength:2048,
      publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['encrypt','decrypt']);
    const bundle = {masterPasswordHash:b64(await pbkdf2(master,utf8(password),1)),
      key:await encrypt(key,stretched), keys:{publicKey:b64(await crypto.subtle.exportKey('spki',rsa.publicKey)),
      encryptedPrivateKey:await encrypt(new Uint8Array(await crypto.subtle.exportKey('pkcs8',rsa.privateKey)),key)},
      folder:await encrypt(utf8('PiVault'),key),items:{}};
    for (const [service,item] of Object.entries(items)) {
      bundle.items[service] = {name:await encrypt(utf8(item.name),key),
        username:await encrypt(utf8(item.username),key),password:await encrypt(utf8(password),key),
        uri:await encrypt(utf8(item.uri),key)};
    }
    master.fill(0); stretched.fill(0); key.fill(0);
    return bundle;
  }
  globalThis.PiVaultVaultCrypto = {create};
  if (typeof document === 'undefined') return;
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
        const bundle = await create(email,form.elements.password.value,plan.items);
        form.elements.vault_bundle.value = JSON.stringify({token:plan.token,bundle});
      }
      HTMLFormElement.prototype.submit.call(form);
    } catch (failure) {
      error.textContent = failure.message || 'Account setup could not finish. Please try again.';
      error.hidden = false; button.disabled = false; button.textContent = 'Join PiVault'; busy = false;
    }
  });
})();
