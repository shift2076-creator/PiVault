/* Admin only: Add user as five steps, one screen at a time.
   Step 2 (Access) holds the review and the Create invitation button; submitting it posts the form, and the
   reloaded page starts at step 3 (or 4 when no shared logins are needed) with the invitation. Only visibility
   changes here, and the review text is built with textContent, never with HTML. */
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
  const review = account.querySelector('.review');
  const back = controls.querySelector('[data-wizard-back]');
  const next = controls.querySelector('[data-wizard-next]');
  const restart = controls.querySelector('[data-wizard-restart]');
  const invited = Boolean(invitation);
  const steps = invited ? (sharedCopy ? [3, 4, 5] : [4, 5]) : [1, 2];
  const CHOICES = { shared: 'Shared', isolated: 'Per user', none: 'No access' };
  // A validation error on the submit comes back with the form filled in; show it at step 2, where it was raised.
  let index = !invited && account.querySelector(':scope > .error') ? 1 : 0;

  document.body.classList.add('wizard-on');
  nav.hidden = false;
  controls.hidden = false;
  heading.hidden = invited;

  function row(label, value) {
    const wrap = document.createElement('div');
    wrap.className = 'review-row';
    const left = document.createElement('span');
    left.textContent = label;
    const right = document.createElement('strong');
    right.textContent = value;
    wrap.append(left, right);
    review.append(wrap);
  }

  function buildReview() {
    review.textContent = '';
    row('Name', name.value);
    const role = document.querySelector('input[name="role"]:checked');
    row('Role', role && role.value === 'admin' ? 'Admin' : 'User');
    for (const service of document.querySelectorAll('.service')) {
      const label = service.querySelector('.service-name').firstChild.textContent.trim();
      const choice = service.querySelector('input[type="radio"]:checked');
      row(label, choice ? CHOICES[choice.value] || choice.value : 'No access');
    }
  }

  function render() {
    const step = steps[index];
    for (const el of document.querySelectorAll('[data-pane]')) {
      el.hidden = el.dataset.pane !== String(step);
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
    if (steps[index] === 2) buildReview();
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
