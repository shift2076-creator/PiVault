/* Admin only: Add user as six steps, one screen at a time.
   The server flow is unchanged. Step 3 posts the form; the reloaded page starts at step 4 (or 5 when
   no shared logins are needed) with the invitation. Only visibility changes here, and the review text
   is built with textContent, never with HTML. */
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
  const steps = invited ? (sharedCopy ? [4, 5, 6] : [5, 6]) : [1, 2, 3];
  const CHOICES = { shared: 'Shared', isolated: 'Per user', none: 'No access' };
  // A validation error on the first submit comes back with the form filled in; show it at step 3, where it was raised.
  let index = !invited && account.querySelector(':scope > .error') ? 2 : 0;

  document.body.classList.add('wizard-on');
  for (const item of nav.querySelectorAll('[data-wizard-step]')) {
    item.hidden = !steps.includes(Number(item.dataset.wizardStep));
  }
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
    account.hidden = step === 2;
    if (invitation) invitation.hidden = !invited;
    for (const item of nav.querySelectorAll('[data-wizard-step]')) {
      const n = Number(item.dataset.wizardStep);
      item.classList.toggle('done', steps.indexOf(n) !== -1 && steps.indexOf(n) < index);
      if (n === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    }
    back.hidden = index === 0;
    // Step 3 has its own Create button, inside the form.
    next.hidden = step === 3 || index === steps.length - 1;
    restart.hidden = !invited || index !== steps.length - 1;
  }

  function go(to) {
    index = to;
    if (steps[index] === 3) buildReview();
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

  // Pressing Enter in a field submits the form. Only step 3 may submit; elsewhere Enter means "continue".
  form.addEventListener('submit', event => {
    if (!invited && steps[index] === 3) return;
    event.preventDefault();
    if (!invited && !next.hidden) next.click();
  });

  render();
})();
