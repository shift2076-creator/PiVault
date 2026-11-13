// Browser-side shared-login tests. Run from app/: node --test portal/tests/js/
//
// The encrypt and decrypt helpers below are written separately from static/portal/vault-crypto.js, so these
// tests check the module against the Bitwarden wire format rather than against itself.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

vm.runInThisContext(readFileSync(new URL('../../static/portal/vault-crypto.js', import.meta.url), 'utf8'));
const Vault = globalThis.PiVaultVaultCrypto;
const subtle = webcrypto.subtle;
const enc = new TextEncoder();
const dec = new TextDecoder();
const b64 = bytes => Buffer.from(bytes).toString('base64');
const unb64 = text => new Uint8Array(Buffer.from(text, 'base64'));
const cat = (...parts) => Buffer.concat(parts.map(p => Buffer.from(p)));

const EMAIL = 'owner@example.com';
const PASSWORD = 'Owner-master-passphrase-42!';
const ITERATIONS = 600000;

async function pbkdf2(password, salt, iterations) {
  const key = await subtle.importKey('raw', password, 'PBKDF2', false, ['deriveBits']);
  return new Uint8Array(await subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, key, 256));
}
async function hmac(key, data) {
  const k = await subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await subtle.sign('HMAC', k, data));
}
async function stretch(master) {
  return cat(await hmac(master, cat(enc.encode('enc'), [1])), await hmac(master, cat(enc.encode('mac'), [1])));
}
async function encryptBytes(bytes, key) {
  const iv = webcrypto.getRandomValues(new Uint8Array(16));
  const aes = await subtle.importKey('raw', key.slice(0, 32), 'AES-CBC', false, ['encrypt']);
  const cipher = new Uint8Array(await subtle.encrypt({ name: 'AES-CBC', iv }, aes, bytes));
  const mac = await hmac(key.slice(32, 64), cat(iv, cipher));
  return `2.${b64(iv)}|${b64(cipher)}|${b64(mac)}`;
}
const encryptText = (text, key) => encryptBytes(enc.encode(text), key);
async function decryptBytes(value, key) {
  const [iv, cipher, mac] = value.slice(2).split('|').map(unb64);
  assert.deepEqual(new Uint8Array(await hmac(key.slice(32, 64), cat(iv, cipher))), mac, 'MAC must verify');
  const aes = await subtle.importKey('raw', key.slice(0, 32), 'AES-CBC', false, ['decrypt']);
  return Buffer.from(await subtle.decrypt({ name: 'AES-CBC', iv }, aes, cipher));
}
const decryptText = async (value, key) => (await decryptBytes(value, key)).toString('utf8');
const secret = () => b64(webcrypto.getRandomValues(new Uint8Array(32)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

test('a sealed copy opens with the same QR secret and no other', async () => {
  const key = secret();
  const entries = { music: { name: 'Music', username: 'family', password: 'pw-1', uri: 'https://192.168.4.90/music' } };
  const sealed = await Vault.seal(entries, key);
  assert.match(sealed, /^2\./);
  assert.deepEqual(await Vault.open(sealed, key), entries);
  await assert.rejects(Vault.open(sealed, secret()), 'a different secret must not open the copy');
});

test('a secret of the wrong length is refused', async () => {
  await assert.rejects(Vault.seal({}, 'short'), /not valid/);
});

test('create writes shared entries from the sealed copy, and only the PiVault login uses the owner password', async () => {
  const shared = { username: 'family', password: 'shared-pw-7', uri: 'https://192.168.4.90:8096' };
  const items = {
    pivault: { name: 'PiVault', username: EMAIL, uri: 'https://192.168.4.90/users/sign-in' },
    music: { name: 'PiVault Music', shared: true },
  };
  const bundle = await Vault.create(EMAIL, PASSWORD, items, { music: shared });

  const master = await pbkdf2(enc.encode(PASSWORD), enc.encode(EMAIL), ITERATIONS);
  const stretched = await stretch(master);
  const userKey = await decryptBytes(bundle.key, stretched);
  assert.equal(userKey.length, 64);
  assert.equal(await decryptText(bundle.items.music.username, userKey), 'family');
  assert.equal(await decryptText(bundle.items.music.password, userKey), 'shared-pw-7');
  assert.equal(await decryptText(bundle.items.music.uri, userKey), 'https://192.168.4.90:8096');
  assert.equal(await decryptText(bundle.items.pivault.password, userKey), PASSWORD);
  assert.equal(await decryptText(bundle.items.music.name, userKey), 'PiVault Music');
});

test('create refuses a shared login that the sealed copy does not hold', async () => {
  const items = { music: { name: 'PiVault Music', shared: true } };
  await assert.rejects(Vault.create(EMAIL, PASSWORD, items, {}), /missing from this invitation/);
});

test('the admin reads its own shared entries through the relay, and the master password never leaves the browser', async () => {
  const master = await pbkdf2(enc.encode(PASSWORD), enc.encode(EMAIL), ITERATIONS);
  const stretched = await stretch(master);
  const userKey = webcrypto.getRandomValues(new Uint8Array(64));
  const wrappedKey = await encryptBytes(userKey, stretched);
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (path, init = {}) => {
    seen.push({ path, init });
    const json = body => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (path === '/users/vault-proxy/prelogin') return json({ Kdf: 0, KdfIterations: ITERATIONS });
    if (path === '/users/vault-proxy/token') return json({ access_token: 'bearer-1', Key: wrappedKey });
    if (path === '/users/vault-proxy/sync') {
      assert.equal(init.headers.Authorization, 'Bearer bearer-1');
      return json({ Ciphers: [
        { Type: 1, Name: await encryptText('Music shared', userKey),
          Login: { Username: await encryptText('family', userKey), Password: await encryptText('shared-pw-7', userKey),
            Uris: [{ Uri: await encryptText('https://192.168.4.90/music', userKey) }] } },
        { Type: 2, Name: await encryptText('A note', userKey) },
        { Type: 1, OrganizationId: 'org-1', Name: await encryptText('Org entry', userKey),
          Login: { Username: await encryptText('x', userKey), Password: await encryptText('y', userKey), Uris: [] } },
      ] });
    }
    throw new Error('unexpected path ' + path);
  };
  try {
    const entries = await Vault.unlockEntries(EMAIL, PASSWORD, 'csrf-token-1');
    assert.deepEqual(entries, [{ name: 'Music shared', username: 'family', password: 'shared-pw-7',
      uri: 'https://192.168.4.90/music' }]);
    assert.deepEqual(seen.map(s => s.path), ['/users/vault-proxy/prelogin', '/users/vault-proxy/token', '/users/vault-proxy/sync']);
    for (const { init } of seen.slice(0, 2)) assert.equal(init.headers['X-CSRFToken'], 'csrf-token-1');
    const form = seen[1].init.body;
    assert.ok(![...form.values()].includes(PASSWORD), 'the master password must not be sent to the token call');
    assert.ok(!seen[0].init.body.includes(PASSWORD), 'the master password must not be sent to prelogin');
    assert.equal(form.get('grant_type'), 'password');
    assert.equal(form.get('scope'), 'api offline_access');
  } finally {
    globalThis.fetch = realFetch;
  }
});

test('a wrong master password stops at the relay with a plain message', async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async path => path.endsWith('/prelogin')
    ? new Response(JSON.stringify({ Kdf: 0, KdfIterations: ITERATIONS }), { status: 200 })
    : new Response(JSON.stringify({ error: 'rejected' }), { status: 400 });
  try {
    await assert.rejects(Vault.unlockEntries(EMAIL, 'wrong-password-value', 'csrf'), /not accepted/);
  } finally {
    globalThis.fetch = realFetch;
  }
});
