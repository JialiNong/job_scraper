/* Unmatched page (templates/unmatched.html) script. Classic script, not a module:
   inline onclick="..." handlers in the page call these globals. */
/* API sources (src/sources/): coloured badge per source so it is clear where a job came from */
const SOURCE_BADGE = {
  ats:            { label: 'ATS',           title: 'Company careers page (ATS board)' },
  arbeitsagentur: { label: 'Bundesagentur', title: 'Bundesagentur für Arbeit Jobsuche API' },
  arbeitnow:      { label: 'Arbeitnow API', title: 'Arbeitnow job board API' },
  adzuna:         { label: 'Adzuna API',    title: 'Adzuna job search API' },
};

function sourceBadgeHtml(job) {
  const key = String(job.source || '').toLowerCase();
  const badge = SOURCE_BADGE[key];
  if (!badge) return '';
  let host = '';
  try { host = new URL(job.link || '').hostname.replace(/^www\./, ''); } catch (e) {}
  const title = badge.title + (host ? ` · ${host}` : '');
  return `<span class="meta-pill source-badge src-${escHtml(key)}" title="${escHtml(title)}">◆ ${escHtml(badge.label)}</span>`;
}
let currentPage = 1;
let debounceTimer = null;
let dateDebounceTimer = null;
const PAGE_SIZE = 20;
let watchlistOnly = false;  // Filter to show only "Can Apply" jobs
let currentLayout = localStorage.getItem('jobLayout') || 'compact';

/* ── Layout toggle ───────────────────────────────────────────────── */
function setLayout(mode) {
  currentLayout = mode;
  localStorage.setItem('jobLayout', mode);
  const grid = document.getElementById('jobGrid');
  grid.classList.toggle('layout-compact', mode === 'compact');
  grid.classList.toggle('layout-cards', mode === 'cards');
  document.querySelectorAll('#layoutToggle button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.layout === mode);
  });
  if (mode === 'compact') {
    grid.querySelectorAll('.card.expanded').forEach(c => c.classList.remove('expanded'));
  }
}

function toggleCardExpand(rowEl) {
  if (currentLayout !== 'compact') return;
  const card = rowEl.closest('.card');
  if (card) card.classList.toggle('expanded');
}

/* ── Toast ───────────────────────────────────────────────────────── */
function showToast(msg, type = 'ok') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${type} show`;
  setTimeout(() => { t.className = 'toast'; }, 2500);
}

/* ── Watchlist filter toggle ─────────────────────────────────────── */
function toggleWatchlistFilter() {
  watchlistOnly = !watchlistOnly;
  const chip = document.getElementById('watchlistChip');
  chip.classList.toggle('active', watchlistOnly);
  goPage(1);
}

/* ── Update unmatched job user_status ────────────────────────────── */
async function updateUnmatchedStatus(select) {
  const id         = select.dataset.id;
  const userStatus = select.value;

  select.className = `status-select${userStatus === 'watchlist' ? ' s-watchlist' : ''}`;

  // Sync compact-row status pill
  const card = select.closest('.card');
  const pill = card && card.querySelector('[data-status-pill]');
  if (pill) {
    if (userStatus === 'watchlist') {
      pill.className = 'status-pill s-watchlist';
      pill.textContent = 'Can Apply';
    } else {
      pill.className = 'status-pill s-unmatched';
      pill.textContent = 'Unmatched';
    }
  }

  try {
    const res = await fetch(`/api/unmatched-jobs/${id}/status`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ user_status: userStatus }),
    });
    if (!res.ok) throw new Error('Request failed');
    const data = await res.json();

    if (userStatus === 'watchlist') {
      if (data.copy_error) {
        showToast(`Marked but failed to copy to Matched: ${data.copy_error}`, 'err');
      } else {
        showToast('Marked as "Can Apply" — added to Matched Jobs');
      }
    } else {
      showToast('Mark removed');
    }

    // If in watchlist-only mode, reload to reflect removal
    if (watchlistOnly && userStatus !== 'watchlist') loadJobs();
    else loadWatchlistCount();
  } catch (err) {
    showToast('Mark failed', 'err');
  }
}

/* ── Load watchlist count ────────────────────────────────────────── */
async function loadWatchlistCount() {
  try {
    const res = await fetch('/api/unmatched-jobs?page_size=1&user_status=watchlist');
    const data = await res.json();
    const el = document.getElementById('watchlistCount');
    if (el) el.textContent = data.total > 0 ? `(${data.total})` : '';
  } catch (_) {}
}

function debounceLoad() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => goPage(1), 600);
}

function goPage(page) {
  currentPage = Math.max(1, page);
  loadJobs();
}

// Employer's publish date (API sources: ATS boards, Bundesagentur …)
function postedPillHtml(job) {
  const raw = String(job.posted_at || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return '';
  const [y, m, d] = raw.split('-').map(Number);
  const posted = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today - posted) / 86400000);
  const rel = days <= 0 ? 'today'
    : days === 1 ? 'yesterday'
    : days < 30 ? `${days}d ago`
    : days < 365 ? `${Math.floor(days / 30)}mo ago`
    : `${Math.floor(days / 365)}y ago`;
  return `<span class="meta-pill" title="Published by the employer on ${escHtml(raw)}">Posted ${rel}</span>`;
}

function formatDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function onTimePresetChange() {
  const preset = document.getElementById('timePreset').value;
  document.getElementById('customDateGroup').classList.toggle('hidden-filter', preset !== 'custom');
  if (preset !== 'custom') {
    goPage(1);  // Reload immediately for built-in presets
  }
  // For custom, reload is triggered only when user actually picks a date
}

/* ── Group jobs by date ──────────────────────────────────────────── */
function formatGroupDate(dateStr) {
  if (!dateStr) return 'Unknown Date';
  const today = formatDate(new Date());
  const yd = new Date(); yd.setDate(yd.getDate() - 1);
  const yesterday = formatDate(yd);
  if (dateStr === today) return `Today · ${dateStr}`;
  if (dateStr === yesterday) return `Yesterday · ${dateStr}`;
  return dateStr;
}

function groupJobsByDate(jobs) {
  const groups = new Map();
  jobs.forEach(job => {
    const dateStr = (job.matched_at || '').split(' ')[0] || 'Unknown Date';
    if (!groups.has(dateStr)) groups.set(dateStr, []);
    groups.get(dateStr).push(job);
  });
  return [...groups.entries()];
}

function onScorePresetChange() {
  const preset = document.getElementById('scorePreset').value;
  document.getElementById('customScoreGroup').classList.toggle('hidden-filter', preset !== 'custom');
  goPage(1);
}

function currentDateRange() {
  const preset = document.getElementById('timePreset').value;
  if (preset === 'all') return { from: '', to: '' };
  if (preset === 'custom') {
    return {
      from: document.getElementById('dateFrom').value,
      to: document.getElementById('dateTo').value,
    };
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const from = new Date(today);
  if (preset === 'today') {
    return { from: formatDate(today), to: formatDate(today) };
  }
  from.setDate(today.getDate() - (Number(preset) - 1));
  return { from: formatDate(from), to: formatDate(today) };
}

function currentScoreRange() {
  const preset = document.getElementById('scorePreset').value;
  if (preset === 'all') return { min: '', max: '' };
  if (preset === '0-3') return { min: '0', max: '3' };
  if (preset === '4-6') return { min: '4', max: '6' };
  return {
    min: document.getElementById('scoreMin').value,
    max: document.getElementById('scoreMax').value,
  };
}

function scoreCls(score) {
  if (score >= 8) return 'score-hi';
  if (score >= 6) return 'score-mid';
  return 'score-lo';
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderBreakdown(title, section) {
  if (!section) return '';
  const matched = Array.isArray(section.matched) ? section.matched : [];
  const unmatched = Array.isArray(section.unmatched) ? section.unmatched : [];
  if (!matched.length && !unmatched.length) return '';
  const total = matched.length + unmatched.length;
  const items = [
    ...matched.map(t => `<li class="bd-ok">✓ ${escHtml(t)}</li>`),
    ...unmatched.map(t => `<li class="bd-no">✗ ${escHtml(t)}</li>`),
  ].join('');
  return `
    <details class="breakdown">
      <summary><span>${title}</span><span class="bd-count">${matched.length}/${total} matched</span></summary>
      <ul class="bd-list">${items}</ul>
    </details>`;
}

function renderList(label, items, cls) {
  if (!Array.isArray(items) || !items.length) return '';
  return `
    <div>
      <div class="tag-label">${escHtml(label)}</div>
      <ul class="tag-list">
        ${items.slice(0, 6).map(t => `<li class="${cls}">• ${escHtml(t)}</li>`).join('')}
      </ul>
    </div>`;
}

const GATE_PILLS = {
  german: 'German',
  stack: 'Backend',
  not_web_dev: 'Not web dev',
  leadership: 'Leadership',
  backend_heavy: 'Backend-heavy',
  too_many_years: 'Years',
  contract_or_location: 'Contract/location',
  blocked_company: 'Blocked',
  empty_desc: 'No JD',
  incomplete_desc: 'Unusable JD',
  ai_ml: 'AI/ML',
  scored: 'Scored',
};

function gateLabel(gate) {
  return GATE_PILLS[gate] || '';
}

function renderCard(job) {
  const score = Number(job.match_score || 0);
  const reason = (job.disqualification_reason || '').trim();
  const summary = (job.summary || '').trim();
  const gate = job.match_gate || '';
  const hasAnalysis = Boolean(
    reason || summary ||
    (job.what_youll_do && ((job.what_youll_do.matched || []).length || (job.what_youll_do.unmatched || []).length)) ||
    (job.missing_requirements || []).length
  );
  const userStatus = job.user_status || '';
  const statusLabel = userStatus === 'watchlist' ? 'Can Apply' : 'Unmatched';
  const statusCls = userStatus === 'watchlist' ? 's-watchlist' : 's-unmatched';
  const gatePill = gateLabel(gate)
    ? `<span class="meta-pill gate-pill${gate === 'stack' ? ' gate-stack' : ''}">${escHtml(gateLabel(gate))}</span>`
    : '';
  const openLink = job.link
    ? `<a class="row-open" href="${escHtml(job.link)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Open</a>`
    : '';

  const card = document.createElement('div');
  card.className = 'card';
  card.id = `card-${job._id}`;
  card.innerHTML = `
    <div class="card-row" onclick="toggleCardExpand(this)">
      <span class="row-chevron">▸</span>
      <div class="row-main">
        <div class="row-title">${escHtml(job.title || '(No Title)')}</div>
        <div class="row-company">${escHtml(job.company || '')}</div>
      </div>
      <div class="row-meta">
        ${job.location ? `<span class="row-loc" title="${escHtml(job.location)}">${escHtml(job.location)}</span>` : ''}
        ${gatePill}
        ${sourceBadgeHtml(job) || `<span class="meta-pill source-pill">${escHtml(job.source || '')}</span>`}
        <span class="status-pill ${statusCls}" data-status-pill>${statusLabel}</span>
        ${openLink}
        <div class="score-badge ${scoreCls(score)}">${score.toFixed(1)}</div>
      </div>
    </div>

    <div class="card-body">
      <div class="card-header">
        <div class="card-title-block">
          <div class="card-title">${escHtml(job.title || '(No Title)')}</div>
          <div class="card-company">${escHtml(job.company || '')}${job.location ? ' · ' + escHtml(job.location) : ''}</div>
        </div>
        <div class="score-badge ${scoreCls(score)}">${score.toFixed(1)}</div>
      </div>

      <div class="card-meta">
        ${sourceBadgeHtml(job) || `<span class="meta-pill source-pill">${escHtml(job.source || '')}</span>`}
        ${gatePill}
        ${job.matched_at ? `<span class="meta-pill" title="Matched on">Matched ${escHtml(job.matched_at)}</span>` : ''}
        ${postedPillHtml(job)}
        ${job.recommendation ? `<span class="meta-pill">${escHtml(job.recommendation)}</span>` : ''}
      </div>

      ${reason ? `<div class="reason-box"><strong>Why it was rejected</strong>${escHtml(reason)}</div>` : ''}
      ${summary && summary !== reason ? `<div class="card-summary">${escHtml(summary)}</div>` : ''}
      ${!hasAnalysis ? `<div class="card-summary">This match has not saved detailed analysis yet. Run matcher again to write full analysis.</div>` : ''}

      ${renderBreakdown("What you'll do", job.what_youll_do)}
      ${renderBreakdown("What they're looking for", job.what_theyre_looking_for)}
      ${renderList('Missing Requirements', job.missing_requirements, 'bd-no')}
      ${renderList('Red Flags', job.red_flags, 'bd-no')}
      ${renderList('Company & score', job.match_reasons, '')}

      <a class="btn-apply" href="${escHtml(job.link || '#')}" target="_blank" rel="noopener">View Original</a>

      ${renderUnmatchedStatusSelect(job)}
    </div>
  `;
  return card;
}

/* ── Render Can Apply override (any unmatched score) ─────────────── */
function renderUnmatchedStatusSelect(job) {
  const userStatus = job.user_status || '';
  return `
    <div class="status-row">
      <select class="status-select${userStatus === 'watchlist' ? ' s-watchlist' : ''}"
              data-id="${job._id}"
              onchange="updateUnmatchedStatus(this)">
        <option value=""    ${userStatus === ''          ? 'selected' : ''}>Not marked</option>
        <option value="watchlist" ${userStatus === 'watchlist' ? 'selected' : ''}>Can apply</option>
      </select>
    </div>`;
}

function renderPager(data) {
  const pager = document.getElementById('pager');
  const pages = data.pages || 1;
  const page = data.page || 1;
  if (!data.total) {
    pager.innerHTML = '';
    return;
  }

  const buttons = [];
  const start = Math.max(1, page - 2);
  const end = Math.min(pages, page + 2);
  for (let i = start; i <= end; i++) {
    buttons.push(`<button class="page-btn ${i === page ? 'active' : ''}" onclick="goPage(${i})">${i}</button>`);
  }

  pager.innerHTML = `
    <button ${page <= 1 ? 'disabled' : ''} onclick="goPage(${page - 1})">Previous</button>
    ${start > 1 ? `<button class="page-btn" onclick="goPage(1)">1</button><span class="page-info">…</span>` : ''}
    ${buttons.join('')}
    ${end < pages ? `<span class="page-info">…</span><button class="page-btn" onclick="goPage(${pages})">${pages}</button>` : ''}
    <button ${page >= pages ? 'disabled' : ''} onclick="goPage(${page + 1})">Next</button>
    <span class="page-info">Page ${page} / ${pages}</span>
  `;
}

async function loadJobs() {
  const grid = document.getElementById('jobGrid');
  if (window.updateFilterCount) updateFilterCount();
  const search = document.getElementById('searchInput').value.trim();
  const source = document.getElementById('sourceFilter').value;
  const gate = document.getElementById('gateFilter').value;

  grid.innerHTML = '<div class="spinner"></div>';
  grid.classList.toggle('layout-compact', currentLayout === 'compact');
  grid.classList.toggle('layout-cards', currentLayout === 'cards');

  const dates = currentDateRange();
  const scores = currentScoreRange();
  const params = new URLSearchParams();
  params.set('page', String(currentPage));
  params.set('page_size', String(PAGE_SIZE));
  if (source !== 'all') params.set('source', source);
  if (gate && gate !== 'all') params.set('match_gate', gate);
  if (search) params.set('q', search);
  if (dates.from) params.set('date_from', dates.from);
  if (dates.to) params.set('date_to', dates.to);
  if (scores.min !== '') params.set('score_min', scores.min);
  if (scores.max !== '') params.set('score_max', scores.max);
  if (watchlistOnly) params.set('user_status', 'watchlist');

  try {
    const res = await fetch('/api/unmatched-jobs?' + params.toString());
    const data = await res.json();
    currentPage = data.page || currentPage;

    grid.innerHTML = '';
    grid.classList.toggle('layout-compact', currentLayout === 'compact');
    grid.classList.toggle('layout-cards', currentLayout === 'cards');
    document.getElementById('countLabel').textContent =
      `Total ${data.total} · Newest First · Threshold ${Number(data.threshold || 7).toFixed(1)}`;

    if (!data.jobs.length) {
      grid.innerHTML = `
        <div class="empty">
          <h2>No Unmatched Jobs</h2>
          <p>Try a different time or score filter, or wait for the next AI matching round.</p>
        </div>`;
      renderPager(data);
      return;
    }

    // Group by date and render
    const groups = groupJobsByDate(data.jobs);
    groups.forEach(([dateStr, jobs]) => {
      const hdr = document.createElement('div');
      hdr.className = 'date-group-header';
      hdr.innerHTML = `<span>${formatGroupDate(dateStr)}</span><span class="dg-count">${jobs.length} jobs</span>`;
      grid.appendChild(hdr);
      jobs.forEach(job => grid.appendChild(renderCard(job)));
    });
    renderPager(data);
  } catch (err) {
    grid.innerHTML = `<div class="empty"><h2>Loading Failed</h2><p>${escHtml(err.message)}</p></div>`;
    document.getElementById('pager').innerHTML = '';
  }
}

// Date input listeners – debounced so the native picker doesn't fire mid-pick
['dateFrom', 'dateTo'].forEach(id => {
  document.getElementById(id).addEventListener('change', function() {
    if (document.getElementById('timePreset').value === 'custom') {
      clearTimeout(dateDebounceTimer);
      dateDebounceTimer = setTimeout(() => goPage(1), 400);
    }
  });
});

/* ── Deep-link: /unmatched?open=<jobs _id> ─────────────────────────── */
function consumeOpenJobId() {
  const params = new URLSearchParams(location.search);
  const id = (params.get('open') || '').trim();
  if (!id) return '';
  params.delete('open');
  const qs = params.toString();
  history.replaceState({}, '', qs ? `/unmatched?${qs}` : '/unmatched');
  return id;
}

function openUnmatchedCard(card) {
  if (!card) return;
  if (currentLayout === 'compact' && !card.classList.contains('expanded')) {
    const row = card.querySelector('.card-row');
    if (row) toggleCardExpand(row);
  }
  card.classList.add('open-flash');
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => card.classList.remove('open-flash'), 2800);
}

async function focusOpenJobFromQuery() {
  if (!window.__pendingOpenJobId) return;
  const openId = window.__pendingOpenJobId;
  window.__pendingOpenJobId = '';

  const grid = document.getElementById('jobGrid');
  let card = document.getElementById(`card-${openId}`);
  if (card) {
    openUnmatchedCard(card);
    showToast('Opened unmatched job');
    return;
  }

  try {
    const res = await fetch(`/api/unmatched-jobs/${openId}`);
    const job = await res.json();
    if (!res.ok) {
      showToast(job.error || 'Job not found on Unmatched', 'err');
      return;
    }
    const banner = document.createElement('div');
    banner.className = 'open-job-banner';
    banner.innerHTML =
      `Opened from URL (not on this page/filter) — <strong>${escHtml(job.title || '')}</strong>`;
    const injected = renderCard(job);
    injected.classList.add('open-flash');
    if (currentLayout === 'compact') injected.classList.add('expanded');
    const empty = grid.querySelector('.empty');
    if (empty) empty.remove();
    const spinner = grid.querySelector('.spinner');
    if (spinner) spinner.remove();
    grid.insertBefore(banner, grid.firstChild);
    grid.insertBefore(injected, banner.nextSibling);
    injected.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => injected.classList.remove('open-flash'), 2800);
    showToast('Opened unmatched job');
  } catch (err) {
    showToast(`Open failed: ${err.message}`, 'err');
  }
}

window.__pendingOpenJobId = consumeOpenJobId();

setLayout(currentLayout);
loadJobs().then(() => focusOpenJobFromQuery());
loadWatchlistCount();
