/*
 * Progressive enhancement only.
 *
 * Every page works with this file blocked: forms submit, filters have a
 * button, and the rate summary appears on the review screen either way. What
 * this adds is the live panel while typing, the mobile sidebar and dismissible
 * messages.
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------- Sidebar */

  var sidebar = document.getElementById('sidebar');
  var toggle = document.getElementById('sidebar-toggle');
  var scrim = document.getElementById('sidebar-scrim');

  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.dataset.open = open ? 'true' : 'false';
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (scrim) scrim.hidden = !open;
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      setSidebar(sidebar.dataset.open !== 'true');
    });
  }
  if (scrim) scrim.addEventListener('click', function () { setSidebar(false); });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') setSidebar(false);
  });

  /* -------------------------------------------------------------- Toasts */

  document.querySelectorAll('.toast-close').forEach(function (button) {
    button.addEventListener('click', function () {
      var toast = button.closest('.toast');
      if (toast) toast.remove();
    });
  });

  /* ------------------------------------------------- Auto-submit filters */

  document.querySelectorAll('[data-autosubmit]').forEach(function (form) {
    form.querySelectorAll('select, input[type="date"]').forEach(function (field) {
      field.addEventListener('change', function () { form.submit(); });
    });

    var search = form.querySelector('input[type="search"]');
    if (search) {
      var timer = null;
      search.addEventListener('input', function () {
        window.clearTimeout(timer);
        // Same 300 ms debounce the React version used.
        timer = window.setTimeout(function () { form.submit(); }, 300);
      });
    }
  });

  /* ------------------------------------------------- Live rate preview */

  var panel = document.getElementById('rate-preview');
  if (!panel) return;

  var endpoint = panel.dataset.endpoint;
  var excludeId = panel.dataset.exclude || '';
  var form = document.getElementById('rate-form');
  if (!endpoint || !form) return;

  var fields = {
    product: form.querySelector('[name="product"]'),
    wholesaler: form.querySelector('[name="wholesaler"]'),
    rate: form.querySelector('[name="purchase_rate"]'),
    quantity: form.querySelector('[name="quantity"]'),
    transport: form.querySelector('[name="transport_cost"]'),
    other: form.querySelector('[name="other_cost"]'),
  };

  var slots = {
    effective: panel.querySelector('[data-slot="effective"]'),
    previous: panel.querySelector('[data-slot="previous"]'),
    previousSource: panel.querySelector('[data-slot="previous-source"]'),
    movement: panel.querySelector('[data-slot="movement"]'),
    cheapest: panel.querySelector('[data-slot="cheapest"]'),
    unit: panel.querySelectorAll('[data-slot="unit"]'),
  };

  var TREND_TEXT = { up: 'Price increased', down: 'Price decreased', same: 'No change' };
  var TREND_CLASS = {
    up: 'text-rose-700',
    down: 'text-emerald-700',
    same: 'text-slate-600',
  };
  var ARROW = { up: '▲', down: '▼', same: '•' };

  function groupIndian(digits) {
    if (digits.length <= 3) return digits;
    var head = digits.slice(0, -3);
    var tail = digits.slice(-3);
    var pairs = [];
    while (head.length > 2) {
      pairs.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) pairs.unshift(head);
    return pairs.concat([tail]).join(',');
  }

  function money(value, symbol) {
    var amount = Number(value || 0);
    var sign = amount < 0 ? '-' : '';
    var parts = Math.abs(amount).toFixed(2).split('.');
    return sign + (symbol || '') + groupIndian(parts[0]) + '.' + parts[1];
  }

  function signed(value, symbol) {
    return Number(value) > 0 ? '+' + money(value, symbol) : money(value, symbol);
  }

  var pending = null;

  function refresh() {
    if (!fields.product || !fields.product.value) return;

    var params = new URLSearchParams({
      product: fields.product.value,
      wholesaler: (fields.wholesaler && fields.wholesaler.value) || '',
      rate: (fields.rate && fields.rate.value) || '',
      quantity: (fields.quantity && fields.quantity.value) || '',
      transport: (fields.transport && fields.transport.value) || '',
      other: (fields.other && fields.other.value) || '',
    });
    if (excludeId) params.set('exclude', excludeId);

    if (pending) pending.abort();
    var controller = new AbortController();
    pending = controller;

    fetch(endpoint + '?' + params.toString(), {
      signal: controller.signal,
      headers: { 'X-Requested-With': 'fetch' },
    })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data || !data.ok) return;
        panel.hidden = false;

        slots.unit.forEach(function (node) { node.textContent = data.unit || ''; });

        if (slots.effective) {
          slots.effective.textContent =
            money(data.effective_rate, data.symbol) + ' per ' + (data.unit || 'unit');
        }

        if (slots.previous) {
          slots.previous.textContent = data.previous
            ? money(data.previous.rate, data.symbol) + ' per ' + (data.unit || 'unit')
            : 'No earlier rate recorded';
        }
        if (slots.previousSource) {
          slots.previousSource.textContent = data.previous ? data.previous.source : '';
        }

        if (slots.movement) {
          if (data.previous && Number(data.difference) !== 0) {
            slots.movement.className = 'num text-sm font-semibold ' + TREND_CLASS[data.trend];
            slots.movement.textContent =
              ARROW[data.trend] + ' ' + signed(data.difference, data.symbol) +
              ' (' + (Number(data.percentage_change) > 0 ? '+' : '') +
              Number(data.percentage_change).toFixed(2) + '%)';
          } else {
            slots.movement.className = 'text-sm text-slate-500';
            slots.movement.textContent = data.previous ? TREND_TEXT.same : '—';
          }
        }

        if (slots.cheapest) {
          slots.cheapest.textContent = data.cheapest
            ? 'Lowest recorded: ' + money(data.cheapest.rate, data.symbol) +
              ' from ' + data.cheapest.wholesaler
            : '';
        }
      })
      .catch(function () { /* Offline or aborted: the review screen still works. */ });
  }

  var debounce = null;
  Object.keys(fields).forEach(function (key) {
    var field = fields[key];
    if (!field) return;
    var event = field.tagName === 'SELECT' ? 'change' : 'input';
    field.addEventListener(event, function () {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(refresh, 250);
    });
  });

  if (fields.product && fields.product.value) refresh();
})();
