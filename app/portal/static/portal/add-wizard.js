/* Admin only: Add user as five steps, one screen at a time.
   Step 2 (Access) holds the Create invitation button; submitting it posts the form, and the
   reloaded page starts at step 3 (or 4 when no shared logins are needed) with the invitation. Only visibility
   changes here. */
(() => {
  'use strict';
  if (document.body.dataset.mode !== 'admin') return;
  const form = document.querySelector('.layout').closest('form');
  const nav = document.querySelector('.wizard-steps');
  const controls = document.querySelector('.wizard-nav');
  const account = document.querySelector('.layout > .account');
  const heading = account.querySelector(':scope > h2');
  const invitation = account.querySelector(':scope > .invitation');
  const sharedCopy = document.getElementById('shared-copy');
  const name = document.getElementById('name');
  const back = controls.querySelector('[data-wizard-back]');
  const next = controls.querySelector('[data-wizard-next]');
  const restart = controls.querySelector('[data-wizard-restart]');
  const invited = Boolean(invitation);
  const steps = invited ? (sharedCopy ? [3, 4, 5] : [4, 5]) : [1, 2];
  // A validation error on the submit comes back with the form filled in; show it at step 2, where it was raised.
  let index = !invited && account.querySelector(':scope > .error') ? 1 : 0;

  document.body.classList.add('wizard-on');
  nav.hidden = false;
  controls.hidden = false;
  heading.hidden = invited;

  function render() {
    const step = steps[index];
    // data-pane takes one step or several, separated by spaces (the invitation code shows on steps 3 and 4).
    for (const el of document.querySelectorAll('[data-pane]')) {
      el.hidden = !el.dataset.pane.split(' ').includes(String(step));
    }
    if (invitation) invitation.hidden = !invited;
    // Every step stays in the bar. Shared logins (step 3) is marked as not needed when the invitation has none.
    for (const item of nav.querySelectorAll('[data-wizard-step]')) {
      const n = Number(item.dataset.wizardStep);
      const skipped = n === 3 && invited && !sharedCopy;
      item.classList.toggle('skipped', skipped);
      item.classList.toggle('done', !skipped && n < step);
      item.classList.toggle('upcoming', !skipped && n > step);
      if (n === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    }
    back.hidden = index === 0;
    // Step 2 has its own Create invitation button, inside the form.
    next.hidden = index === steps.length - 1;
    restart.hidden = !invited || index !== steps.length - 1;
  }

  function go(to) {
    index = to;
    render();
  }

  next.addEventListener('click', () => {
    if (steps[index] === 1) {
      name.value = name.value.trim();
      if (!name.reportValidity()) return;
    }
    go(index + 1);
  });

  back.addEventListener('click', () => go(index - 1));

  // Pressing Enter in a field submits the form. Only step 2 may submit; elsewhere Enter means "continue".
  form.addEventListener('submit', event => {
    if (!invited && steps[index] === 2) return;
    event.preventDefault();
    if (!invited && !next.hidden) next.click();
  });

  render();
})();
