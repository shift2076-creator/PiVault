(() => {
  'use strict';
  const password = document.getElementById('password');
  const confirmation = document.getElementById('password-confirm');
  if (!password) return;
  const hide = [];
  for (const button of document.querySelectorAll('[data-password-toggle]')) {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input || input.disabled) continue;
    const label = input.id === 'password-confirm' ? 'confirm password' : 'password';
    const setVisible = visible => {
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-label', (visible ? 'Hide ' : 'Show ') + label);
      button.setAttribute('aria-pressed', String(visible));
      button.querySelector('.eye-slash').toggleAttribute('hidden', !visible);
    };
    setVisible(false);
    button.hidden = false;
    button.addEventListener('click', () => setVisible(input.type === 'password'));
    hide.push(() => setVisible(false));
  }
  window.addEventListener('pageshow', () => hide.forEach(reset => reset()));
  if (!confirmation) return;
  const message = document.getElementById('password-confirm-error');
  let confirmationTouched = false;
  const matches = () => {
    const valid = password.value === confirmation.value;
    confirmation.setCustomValidity(valid ? '' : 'Passwords do not match.');
    const showError = confirmationTouched && !valid;
    confirmation.setAttribute('aria-invalid', String(showError));
    if (message) {
      message.hidden = !showError;
      message.textContent = showError ? 'Passwords do not match.' : '';
    }
    return valid;
  };
  const revealValidation = () => { confirmationTouched = true; return matches(); };
  password.addEventListener('input', matches);
  confirmation.addEventListener('input', matches);
  confirmation.addEventListener('blur', revealValidation);
  confirmation.addEventListener('invalid', revealValidation);
  password.form.addEventListener('submit', event => {
    if (!revealValidation()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      confirmation.reportValidity();
    }
  }, true);
})();
