'use strict';

const state = { contacts: [], selected: null, etag: '', creating: false };
const $ = (id) => document.getElementById(id);
const form = $('editor');
const FIELDS = ['given', 'family', 'org', 'title', 'url', 'birthday', 'note'];
const ADDRESS = ['street', 'city', 'region', 'postal', 'country'];

async function api(path, options = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  const response = await fetch(path, Object.assign({ credentials: 'same-origin' }, options, { headers }));
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `The request failed (${response.status}).`);
  }
  return data;
}

function setStatus(text, isError = false) {
  const status = $('status');
  status.textContent = text;
  status.className = isError ? 'status error' : 'status';
}

function displayName(contact) {
  return [contact.given, contact.family].filter(Boolean).join(' ')
    || contact.org || contact.full_name || (contact.emails[0] && contact.emails[0].value) || 'Unnamed';
}

function searchText(contact) {
  const parts = [displayName(contact), contact.org, contact.title, contact.note, contact.url];
  contact.emails.forEach((entry) => parts.push(entry.value));
  contact.phones.forEach((entry) => parts.push(entry.value));
  return parts.join(' ').toLowerCase();
}

function render() {
  const query = $('search').value.trim().toLowerCase();
  const list = $('list');
  list.replaceChildren();
  const shown = state.contacts.filter((contact) => !query || searchText(contact).includes(query));
  shown.forEach((contact) => {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'contact' + (contact.id === state.selected && !state.creating ? ' selected' : '');
    const name = document.createElement('span');
    name.className = 'contact-name';
    name.textContent = displayName(contact);
    const detail = document.createElement('span');
    detail.className = 'contact-detail';
    detail.textContent = contact.org || (contact.emails[0] && contact.emails[0].value) || '';
    button.append(name, detail);
    button.addEventListener('click', () => select(contact.id));
    item.append(button);
    list.append(item);
  });
  $('empty-list').hidden = shown.length > 0;
}

function input(className, value, placeholder) {
  const element = document.createElement('input');
  element.className = className;
  element.value = value || '';
  element.placeholder = placeholder;
  return element;
}

function addRow(kind, entry = { type: '', value: '' }) {
  const row = document.createElement('div');
  row.className = 'row';
  const type = input('type', entry.type, 'Type (work, home, cell)');
  const value = input('value', entry.value, kind === 'emails' ? 'Email address' : 'Phone number');
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'remove';
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => row.remove());
  row.append(type, value, remove);
  $(kind).append(row);
}

function rows(kind) {
  return Array.from($(kind).querySelectorAll('.row')).map((row) => ({
    type: row.querySelector('.type').value.trim(),
    value: row.querySelector('.value').value.trim(),
  })).filter((entry) => entry.value);
}

function fill(contact) {
  FIELDS.forEach((field) => { form.elements[field].value = contact ? contact[field] || '' : ''; });
  ADDRESS.forEach((field) => { form.elements[field].value = contact ? contact.address[field] || '' : ''; });
  $('emails').replaceChildren();
  $('phones').replaceChildren();
  ((contact && contact.emails) || []).forEach((entry) => addRow('emails', entry));
  ((contact && contact.phones) || []).forEach((entry) => addRow('phones', entry));
}

function showEditor(visible) {
  form.hidden = !visible;
  $('placeholder').hidden = visible;
  $('delete').hidden = !visible || state.creating;
}

function select(id) {
  const contact = state.contacts.find((item) => item.id === id);
  if (!contact) return;
  state.selected = id;
  state.etag = contact.etag;
  state.creating = false;
  fill(contact);
  showEditor(true);
  setStatus('');
  render();
}

function startNew() {
  state.selected = null;
  state.etag = '';
  state.creating = true;
  fill(null);
  showEditor(true);
  setStatus('');
  render();
  form.elements.given.focus();
}

function collect() {
  const payload = {};
  FIELDS.forEach((field) => { payload[field] = form.elements[field].value.trim(); });
  payload.address = {};
  ADDRESS.forEach((field) => { payload.address[field] = form.elements[field].value.trim(); });
  payload.emails = rows('emails');
  payload.phones = rows('phones');
  return payload;
}

async function load() {
  state.contacts = await api('/api/contacts');
  if (state.selected && !state.contacts.some((item) => item.id === state.selected)) {
    state.selected = null;
    showEditor(false);
  }
  render();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  setStatus('Saving…');
  try {
    const payload = collect();
    if (state.creating) {
      const created = await api('/api/contacts', { method: 'POST', body: JSON.stringify(payload) });
      state.selected = created.id;
    } else {
      payload.etag = state.etag;
      await api('/api/contacts/' + encodeURIComponent(state.selected),
        { method: 'PUT', body: JSON.stringify(payload) });
    }
    state.creating = false;
    await load();
    select(state.selected);
    setStatus('Saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

$('delete').addEventListener('click', async () => {
  if (!state.selected || state.creating) return;
  if (!window.confirm('Delete this contact? This cannot be undone.')) return;
  try {
    await api('/api/contacts/' + encodeURIComponent(state.selected),
      { method: 'DELETE', headers: { 'If-Match': state.etag } });
    state.selected = null;
    await load();
    showEditor(false);
    setStatus('');
  } catch (error) {
    setStatus(error.message, true);
  }
});

$('cancel').addEventListener('click', () => {
  if (state.creating) {
    state.creating = false;
    showEditor(false);
    render();
  } else if (state.selected) {
    select(state.selected);
  }
});

$('new').addEventListener('click', startNew);
$('search').addEventListener('input', render);
document.querySelectorAll('.add').forEach((button) => {
  button.addEventListener('click', () => addRow(button.dataset.kind));
});

load().catch((error) => setStatus(error.message, true));
