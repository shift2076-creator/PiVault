// Android devices page: enables the Sync to phone buttons when the local PiVault sync service answers on this computer.
(function () {
  'use strict';
  var SERVICE = 'http://127.0.0.1:8765';
  var helper = document.getElementById('helper-section');
  var status = document.getElementById('sync-status');
  var buttons = document.querySelectorAll('[data-sync-client]');

  function setReady(ready) {
    if (helper) { helper.hidden = ready; }
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = !ready; }
    status.textContent = ready ? '' : 'Start the PiVault sync service on this computer to enable Sync to phone.';
  }

  function reason(error) {
    return error instanceof TypeError ? 'Cannot reach the PiVault sync service on this computer.' : error.message;
  }

  fetch(SERVICE + '/status', { cache: 'no-store' })
    .then(function (response) { setReady(response.ok); })
    .catch(function () { setReady(false); });

  Array.prototype.forEach.call(buttons, function (button) {
    button.addEventListener('click', function () {
      var label = button.textContent;
      var client = button.getAttribute('data-sync-client');
      button.disabled = true;
      button.textContent = 'Syncing…';
      status.textContent = '';
      fetch(SERVICE + '/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apps: [client] })
      })
        .then(function (response) {
          return response.json().then(function (body) { return { ok: response.ok, body: body }; });
        })
        .then(function (result) {
          if (result.ok) { window.location.reload(); return; }
          throw new Error(result.body.error || 'Sync failed.');
        })
        .catch(function (error) {
          button.disabled = false;
          button.textContent = label;
          status.textContent = reason(error);
        });
    });
  });
}());
