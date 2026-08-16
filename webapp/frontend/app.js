// SPDX-License-Identifier: Apache-2.0
(() => {
  'use strict';

  const API_BASE = '/api';

  // ---------- Theme ----------
  const THEME_KEY = 'sca-scanner-theme';

  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }

  function currentEffectiveTheme() {
    const explicit = document.documentElement.getAttribute('data-theme');
    if (explicit) return explicit;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function initTheme() {
    applyTheme(localStorage.getItem(THEME_KEY));
    document.getElementById('theme-toggle').addEventListener('click', () => {
      const next = currentEffectiveTheme() === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      localStorage.setItem(THEME_KEY, next);
    });
  }

  // ---------- Toasts ----------
  function toast(message, { type = 'info', duration = 5000 } = {}) {
    const region = document.getElementById('toast-region');
    const el = document.createElement('div');
    el.className = 'toast' + (type === 'error' ? ' toast--error' : '');
    el.textContent = message;
    region.appendChild(el);
    setTimeout(() => el.remove(), duration);
  }

  // ---------- API client ----------
  async function apiRequest(path, options = {}) {
    const res = await fetch(API_BASE + path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let body = null;
    if (text) {
      try { body = JSON.parse(text); } catch { body = null; }
    }
    if (!res.ok) {
      const detail = (body && (body.detail || body.message)) || `Request failed (${res.status})`;
      const message = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
        : detail;
      throw new Error(message);
    }
    return body;
  }

  const api = {
    createScan: (payload) => apiRequest('/scans', { method: 'POST', body: JSON.stringify(payload) }),
    listScans: (params) => apiRequest('/scans?' + new URLSearchParams(params).toString()),
  };

  // ---------- Routing ----------
  const views = {
    new: document.getElementById('view-new'),
    history: document.getElementById('view-history'),
  };
  const navLinks = document.querySelectorAll('.main-nav__link');

  function navigate() {
    const hash = (window.location.hash || '#/history').replace('#/', '');
    const route = views[hash] ? hash : 'history';
    Object.entries(views).forEach(([name, el]) => { el.hidden = name !== route; });
    navLinks.forEach((link) => link.classList.toggle('is-active', link.dataset.route === route));
    if (route === 'history') {
      loadHistory({ reset: true });
      startHistoryPolling();
    } else {
      stopHistoryPolling();
    }
  }
  window.addEventListener('hashchange', navigate);

  // ---------- Form validation & submission ----------
  const form = document.getElementById('scan-form');
  const submitBtn = document.getElementById('submit-scan-btn');

  const FIELD_RULES = [
    { id: 'f-project-name', name: 'project_name', required: true },
    {
      id: 'f-repo-url', name: 'repo_url', required: true,
      pattern: /^(https?:\/\/|git:\/\/|ssh:\/\/|git@[\w.-]+:)/i,
      message: 'Enter a valid http(s), ssh, or git@ repository URL.',
    },
    { id: 'f-branch', name: 'branch', required: true },
    { id: 'f-commit-id', name: 'commit_id', required: false },
  ];

  function validateField(rule) {
    const input = document.getElementById(rule.id);
    const errorEl = document.getElementById('err-' + rule.name.replace(/_/g, '-'));
    const value = input.value.trim();
    let message = '';

    if (rule.required && !value) {
      message = 'This field is required.';
    } else if (value && value.startsWith('-')) {
      message = "Value must not start with '-'.";
    } else if (value && rule.pattern && !rule.pattern.test(value)) {
      message = rule.message || 'Invalid value.';
    }

    if (errorEl) {
      errorEl.textContent = message;
      errorEl.hidden = !message;
    }
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
    return !message;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    let firstInvalid = null;
    let allValid = true;
    for (const rule of FIELD_RULES) {
      if (!validateField(rule)) {
        allValid = false;
        firstInvalid = firstInvalid || document.getElementById(rule.id);
      }
    }
    if (!allValid) {
      firstInvalid.focus();
      return;
    }

    const payload = {
      project_name: document.getElementById('f-project-name').value.trim(),
      repo_url: document.getElementById('f-repo-url').value.trim(),
      branch: document.getElementById('f-branch').value.trim(),
      author: document.getElementById('f-author').value.trim() || null,
      assessment_type: document.getElementById('f-assessment-type').value,
      commit_id: document.getElementById('f-commit-id').value.trim() || null,
      git_token: document.getElementById('f-git-token').value || null,
    };

    setSubmitting(true);
    try {
      const record = await api.createScan(payload);
      toast(`Scan queued for "${record.project_name}".`);
      form.reset();
      document.getElementById('f-branch').value = 'main';
      window.location.hash = '#/history';
      loadHistory({ reset: true });
    } catch (err) {
      toast(err.message || 'Failed to start scan.', { type: 'error', duration: 7000 });
    } finally {
      setSubmitting(false);
    }
  });

  function setSubmitting(isSubmitting) {
    submitBtn.disabled = isSubmitting;
    submitBtn.querySelector('.btn__spinner').hidden = !isSubmitting;
    submitBtn.querySelector('.btn__label').textContent = isSubmitting ? 'Starting…' : 'Start Scan';
  }

  // Token show/hide
  const tokenInput = document.getElementById('f-git-token');
  const tokenToggleBtn = document.getElementById('toggle-token-visibility');
  tokenToggleBtn.addEventListener('click', () => {
    const willShow = tokenInput.type === 'password';
    tokenInput.type = willShow ? 'text' : 'password';
    tokenToggleBtn.setAttribute('aria-pressed', String(willShow));
    tokenToggleBtn.setAttribute('aria-label', willShow ? 'Hide git token' : 'Show git token');
    tokenToggleBtn.querySelector('.icon-eye').hidden = willShow;
    tokenToggleBtn.querySelector('.icon-eye-off').hidden = !willShow;
  });

  // ---------- History ----------
  const ACTIVE_STATUSES = new Set(['queued', 'cloning', 'scanning']);
  const STATUS_LABELS = {
    queued: 'Queued', cloning: 'Cloning', scanning: 'Scanning', completed: 'Completed', failed: 'Failed',
  };
  const PAGE_SIZE = 25;
  const historyState = { skip: 0, q: '', status: '', items: [], total: 0 };
  let pollTimer = null;

  const searchInput = document.getElementById('search-input');
  const statusFilter = document.getElementById('status-filter');
  const refreshBtn = document.getElementById('refresh-btn');
  const loadMoreBtn = document.getElementById('load-more-btn');
  const loadMoreRow = document.getElementById('load-more-row');
  const historyContainer = document.getElementById('history-container');

  let searchDebounce = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      historyState.q = searchInput.value.trim();
      loadHistory({ reset: true });
    }, 300);
  });
  statusFilter.addEventListener('change', () => {
    historyState.status = statusFilter.value;
    loadHistory({ reset: true });
  });
  refreshBtn.addEventListener('click', () => loadHistory({ reset: true }));
  loadMoreBtn.addEventListener('click', () => loadHistory({ reset: false }));

  async function loadHistory({ reset }) {
    if (reset) historyState.skip = 0;
    const params = { limit: PAGE_SIZE, skip: historyState.skip };
    if (historyState.q) params.q = historyState.q;
    if (historyState.status) params.status = historyState.status;

    try {
      const data = await api.listScans(params);
      historyState.items = reset ? data.items : historyState.items.concat(data.items);
      historyState.total = data.total;
      historyState.skip = historyState.items.length;
      renderHistory();
    } catch (err) {
      if (reset) {
        historyContainer.innerHTML = `<p class="empty-state">Couldn't load scan history: ${escapeHtml(err.message)}</p>`;
      } else {
        toast('Failed to load more scans.', { type: 'error' });
      }
    }
  }

  function renderStats(items, total) {
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-active').textContent = items.filter((i) => ACTIVE_STATUSES.has(i.status)).length;
    document.getElementById('stat-completed').textContent = items.filter((i) => i.status === 'completed').length;
    document.getElementById('stat-failed').textContent = items.filter((i) => i.status === 'failed').length;
  }

  function renderHistory() {
    const { items, total } = historyState;
    renderStats(items, total);

    if (items.length === 0) {
      historyContainer.innerHTML = `
        <div class="empty-state">
          <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg>
          <p>No scans yet.</p>
          <a class="btn btn--primary" href="#/new">Start your first scan</a>
        </div>`;
      loadMoreRow.hidden = true;
      return;
    }

    historyContainer.innerHTML = '';
    historyContainer.appendChild(buildTable(items));
    historyContainer.appendChild(buildCards(items));
    loadMoreRow.hidden = items.length >= total;
  }

  function buildTable(items) {
    const wrap = document.createElement('div');
    wrap.className = 'scan-table-wrap';
    const table = document.createElement('table');
    table.className = 'scan-table';
    table.innerHTML = `<thead><tr>
      <th>Project</th><th>Repository</th><th>Branch</th><th>Author</th>
      <th>Status</th><th>Severity</th><th>Started</th><th>Duration</th><th><span class="sr-only">Actions</span></th>
    </tr></thead>`;
    const tbody = document.createElement('tbody');
    const rowTpl = document.getElementById('scan-row-template');

    items.forEach((scan) => {
      const row = rowTpl.content.firstElementChild.cloneNode(true);
      row.querySelector('.scan-row__project').textContent = scan.project_name;
      row.querySelector('.scan-row__meta').textContent = scan.assessment_type || '';
      const repoLink = row.querySelector('.scan-row__repo');
      repoLink.href = scan.repo_url;
      repoLink.textContent = scan.repo_url;
      repoLink.title = scan.repo_url;
      row.querySelector('.scan-row__branch').textContent = scan.branch;
      row.querySelector('.col-author').textContent = scan.author || '—';
      applyStatusBadge(row.querySelector('.scan-row__status'), scan);
      row.querySelector('.severity-mini').replaceWith(buildSeverityMini(scan.severity_counts));
      row.querySelector('.col-started').textContent = formatDate(scan.started_at || scan.created_at);
      row.querySelector('.col-duration').textContent = formatDuration(scan.duration_seconds);
      row.querySelector('.col-actions').appendChild(buildActions(scan));
      tbody.appendChild(row);
    });

    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildCards(items) {
    const list = document.createElement('ul');
    list.className = 'scan-cards';
    const cardTpl = document.getElementById('scan-card-template');

    items.forEach((scan) => {
      const card = cardTpl.content.firstElementChild.cloneNode(true);
      card.querySelector('.scan-card__project').textContent = scan.project_name;
      card.querySelector('.scan-card__meta').textContent = [scan.branch, scan.author].filter(Boolean).join(' · ');
      applyStatusBadge(card.querySelector('.scan-card__status'), scan);
      const repoLink = card.querySelector('.scan-card__repo a');
      repoLink.href = scan.repo_url;
      repoLink.textContent = scan.repo_url;
      card.querySelector('.severity-mini').replaceWith(buildSeverityMini(scan.severity_counts));
      card.querySelector('.scan-card__duration').textContent = formatDuration(scan.duration_seconds);
      card.querySelector('.scan-card__actions').appendChild(buildActions(scan));
      list.appendChild(card);
    });
    return list;
  }

  function applyStatusBadge(el, scan) {
    el.classList.add('badge', `badge--${scan.status}`);
    el.textContent = STATUS_LABELS[scan.status] || scan.status;
    if (scan.status === 'failed' && scan.error_message) {
      el.title = scan.error_message;
    }
  }

  function buildSeverityMini(counts) {
    const wrap = document.createElement('span');
    wrap.className = 'severity-mini';
    if (!counts) {
      wrap.textContent = '—';
      return wrap;
    }
    const entries = [
      ['critical', 'C', counts.critical],
      ['high', 'H', counts.high],
      ['medium', 'M', counts.medium],
      ['low', 'L', counts.low],
    ];
    if (!entries.some(([, , n]) => n > 0)) {
      wrap.textContent = '—';
      return wrap;
    }
    entries.forEach(([key, label, n]) => {
      if (!n) return;
      const span = document.createElement('span');
      span.className = `sev-${key}`;
      span.textContent = `${label} ${n}`;
      wrap.appendChild(span);
    });
    return wrap;
  }

  function buildActions(scan) {
    const wrap = document.createElement('span');
    if (scan.report_available) {
      const link = document.createElement('a');
      link.className = 'row-action-link';
      link.href = `${API_BASE}/scans/${scan.scan_id}/report`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'View report';
      wrap.appendChild(link);
    } else if (scan.status === 'failed') {
      const note = document.createElement('span');
      note.className = 'error-note';
      note.title = scan.error_message || '';
      note.textContent = scan.error_message || 'Scan failed';
      wrap.appendChild(note);
    } else {
      const note = document.createElement('span');
      note.className = 'error-note';
      note.style.color = 'var(--color-muted-foreground)';
      note.textContent = 'In progress…';
      wrap.appendChild(note);
    }
    return wrap;
  }

  function formatDate(value) {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const s = Math.round(seconds);
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function startHistoryPolling() {
    stopHistoryPolling();
    pollTimer = setInterval(() => {
      if (historyState.items.some((i) => ACTIVE_STATUSES.has(i.status))) {
        loadHistory({ reset: true });
      }
    }, 4000);
  }
  function stopHistoryPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ---------- Init ----------
  initTheme();
  navigate();
})();
