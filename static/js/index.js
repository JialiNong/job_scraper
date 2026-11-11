/* Tracker page (templates/index.html) script. Classic script, not a module:
   inline onclick="..." handlers in the page call these globals.
   Shared card chrome: job_list.js. Overview / cards / batch / timeline /
   card-actions load before this file; init stays here. */
/* ── State ───────────────────────────────────────────────────────── */
let currentStatusFilter = 'active';
let highlightsOnly = false;
let companyFilter = ''; // exact company (click a company name on a card)
let appliedOnFilter = ''; // YYYY-MM-DD: jobs applied that day (heatmap → /?applied=…)
let debounceTimer = null;
let dateDebounceTimer = null;
let jobsAbortController = null;
const SEARCH_DEBOUNCE_MS = 600; // wait until typing pauses before searching
// Chips (/api/stats) and Overview (journey / heatmap / locations) wait for
// the first job list, so on a cold server /api/jobs gets Atlas first.
// Never hold them back for more than a few seconds.
let resolveListReady;
const listReady = new Promise(resolve => { resolveListReady = resolve; });
setTimeout(() => resolveListReady(), 4000);
const collapsedGroups = new Set(); // tracks which date groups are collapsed
const jobHighlights = {}; // job_id -> string[]
const HIGHLIGHT_PRESETS = ['Leipzig', 'Chinese+'];

// Pagination state
let currentPage = 1;
let totalPages = 1;
let totalJobs = 0;
const PAGE_SIZE = 20;

const STATUS_LABEL = {
  pending:    'Not applied',
  applied:    'Applied',
  rejected:   'Rejected',
  interview:  'Interview',
  offer:      'Offer',
  unsuitable: 'Unsuitable',
  closed:     'Closed',
  closed_no_reply: 'Closed, no reply',
  prev_rejected: 'Rejected before',
  too_many_questions: 'Skipped, long form',
  repost:     'Repost',
  duplicate:  'Duplicate',
};
const SIDE_STATUSES = ['unsuitable', 'closed', 'closed_no_reply', 'prev_rejected', 'too_many_questions', 'repost', 'duplicate'];
const PROGRESS_STAGES = ['applied', 'rejected', 'interview', 'offer'];
const selectedFetchIds = new Set();
// Selected cards that are not eligible for batch Fetch / Run AI (already
// scored) — only Delete selected acts on them.
const selectedDeleteOnlyIds = new Set();
// "Select jobs" mode: card checkboxes are hidden until it is turned on.
let selectMode = false;
let fetchBatchPollTimer = null;
let fetchBatchRunning = false;
let fetchBatchMode = 'fetch';

const TIMELINE_LABEL = {
  applied:          'Applied',
  reapplied:        'Reapplied',
  interview_invite: 'Invite',
  interview_1:      'Round 1',
  interview_2:      'Round 2',
  interview_3:      'Round 3',
  interview_final:  'Final',
  rejected:         'Rejected',
  offer:            'Offer',
};

const INTERVIEW_STEPS = ['interview_1', 'interview_2', 'interview_3', 'interview_final'];
const GOT_INTERVIEW_FILTER = 'got_interview';
const GOT_INTERVIEW_STEPS = new Set(['interview_invite', ...INTERVIEW_STEPS, 'offer']);
const TERMINAL_STEPS = new Set(['rejected', 'offer']);

/** In-memory timeline cache keyed by job id */
const jobTimelines = {};
/* ── Stats bar + funnel ──────────────────────────────────────────── */
async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    const data = await res.json();

    // Filter chips (existing)
    document.getElementById('cnt-all').textContent = data.total;
    const unmatchedEl = document.getElementById('unmatchedNavCount');
    if (unmatchedEl) {
      unmatchedEl.textContent = typeof data.unmatched === 'number' ? String(data.unmatched) : '';
    }
    const timeoutEl = document.getElementById('timeoutNavCount');
    if (timeoutEl) {
      timeoutEl.textContent = typeof data.timeouts === 'number' ? String(data.timeouts) : '';
    }
    for (const [status, cnt] of Object.entries(data.by_status)) {
      const el = document.getElementById(`cnt-${status}`);
      if (el) el.textContent = cnt;
    }
    ['active','pending','applied','rejected','interview','got_interview','offer','unsuitable','closed','closed_no_reply','prev_rejected','too_many_questions','repost','duplicate'].forEach(s => {
      if (!data.by_status[s]) {
        const el = document.getElementById(`cnt-${s}`);
        if (el) el.textContent = 0;
      }
    });

    if (data.funnel) sizeFunnelRail(data.funnel);
  } catch (_) {}
}
/* ── Jobs / Overview tabs ────────────────────────────────────────── */
function showTrackerView(view) {
  const overview = view === 'overview';
  document.getElementById('jobsView').hidden = overview;
  document.getElementById('overviewView').hidden = !overview;
  const tabJobs = document.getElementById('tabJobs');
  const tabOverview = document.getElementById('tabOverview');
  tabJobs.setAttribute('aria-selected', String(!overview));
  tabOverview.setAttribute('aria-selected', String(overview));
  tabJobs.tabIndex = overview ? -1 : 0;
  tabOverview.tabIndex = overview ? 0 : -1;
  if (overview) {
    // Heatmap and bubbles measure their width; re-measure now they are visible
    requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
  }
}

// Tab list keys: Left / Right / Home / End move between the two views
document.querySelector('.tabs[role="tablist"]')?.addEventListener('keydown', (e) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const toOverview = e.key === 'End'
    || (e.key !== 'Home' && document.getElementById('tabJobs').getAttribute('aria-selected') === 'true');
  showTrackerView(toOverview ? 'overview' : 'jobs');
  document.getElementById(toOverview ? 'tabOverview' : 'tabJobs').focus();
});
/* ── Status filter ───────────────────────────────────────────────── */
function setStatusFilter(status) {
  currentStatusFilter = status;
  currentPage = 1; // Reset to first page when filter changes
  document.querySelectorAll('.stat-chip[data-status]').forEach(el => {
    el.classList.toggle('active', el.dataset.status === status);
  });
  setStatsExpanded(false); // mobile: fold the chip list after picking one
  loadJobs();
}

function setStatsExpanded(expanded) {
  const bar = document.getElementById('statsBar');
  const btn = document.getElementById('statsMoreToggle');
  if (!bar || !btn) return;
  bar.classList.toggle('stats-expanded', expanded);
  btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  btn.textContent = expanded ? 'Show fewer statuses' : 'Show more statuses';
}

function toggleStatsExpanded() {
  setStatsExpanded(!document.getElementById('statsBar').classList.contains('stats-expanded'));
}

/* ── Company filter (click a company name) ───────────────────────── */
function applyCompanyFilterUI() {
  const chip = document.getElementById('companyFilterChip');
  document.getElementById('companyFilterName').textContent = companyFilter;
  chip.hidden = !companyFilter;
}

/** Tracker URL filtered to one company (opened in a new tab from company history). */
function companyPageUrl(name) {
  return `/?company=${encodeURIComponent(String(name || '').trim())}`;
}

/** Keep ?company= / ?applied= in the address bar so a refresh keeps the filter. */
function syncFilterParams() {
  const params = new URLSearchParams(location.search);
  if (companyFilter) params.set('company', companyFilter);
  else params.delete('company');
  if (appliedOnFilter) params.set('applied', appliedOnFilter);
  else params.delete('applied');
  const qs = params.toString();
  history.replaceState({}, '', qs ? `/?${qs}` : '/');
  const parts = [companyFilter, appliedOnFilter && `Applied ${appliedOnFilter}`].filter(Boolean);
  document.title = parts.length ? `${parts.join(' · ')} · Job Tracker` : 'Job Tracker';
}

function applyAppliedFilterUI() {
  document.getElementById('appliedFilterName').textContent = appliedOnFilter;
  document.getElementById('appliedFilterChip').hidden = !appliedOnFilter;
}

/** Reset the toolbar filters so a company / applied-day view is complete. */
function resetToolbarFilters() {
  document.getElementById('searchInput').value = '';
  document.getElementById('sourceFilter').value = 'all';
  document.getElementById('originFilter').value = 'all';
  document.getElementById('countryFilter').value = 'all';
  document.getElementById('timePreset').value = 'all';
  document.getElementById('customDateGroup').classList.add('hidden-filter');
  if (highlightsOnly) {
    highlightsOnly = false;
    document.getElementById('highlightsFilter').classList.remove('active');
  }
}

function filterByCompany(name) {
  const company = String(name || '').trim();
  if (!company) return;
  companyFilter = company;
  syncFilterParams();
  // Show every job ever seen at this company, whatever the other filters were.
  resetToolbarFilters();
  applyCompanyFilterUI();
  showTrackerView('jobs');
  setStatusFilter('all');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function filterByAppliedDay(dateStr) {
  const day = String(dateStr || '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
  appliedOnFilter = day;
  syncFilterParams();
  // Every job applied that day, whatever its status is now
  resetToolbarFilters();
  applyAppliedFilterUI();
  showTrackerView('jobs');
  setStatusFilter('all');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function clearAppliedFilter() {
  appliedOnFilter = '';
  syncFilterParams();
  applyAppliedFilterUI();
  currentPage = 1;
  loadJobs();
}

function clearCompanyFilter() {
  companyFilter = '';
  syncFilterParams();
  applyCompanyFilterUI();
  currentPage = 1;
  loadJobs();
}

function toggleHighlightsFilter() {
  highlightsOnly = !highlightsOnly;
  document.getElementById('highlightsFilter').classList.toggle('active', highlightsOnly);
  loadJobs();
}

/* ── Debounce search ─────────────────────────────────────────────── */
function debounceLoad() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    currentPage = 1; // Reset to first page on search
    loadJobs();
  }, SEARCH_DEBOUNCE_MS);
}

/* ── Pagination ──────────────────────────────────────────────────── */
function updatePagination() {
  const pagination = document.getElementById('pagination');
  const prevBtn = document.getElementById('prevPage');
  const nextBtn = document.getElementById('nextPage');
  const pageInfo = document.getElementById('pageInfo');

  if (totalPages <= 1) {
    pagination.style.display = 'none';
    return;
  }

  pagination.style.display = 'flex';
  pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
  prevBtn.disabled = currentPage <= 1;
  nextBtn.disabled = currentPage >= totalPages;
}

function prevPage() {
  if (currentPage > 1) {
    currentPage--;
    loadJobs();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function nextPage() {
  if (currentPage < totalPages) {
    currentPage++;
    loadJobs();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}
function onTimePresetChange() {
  const preset = document.getElementById('timePreset').value;
  document.getElementById('customDateGroup').classList.toggle('hidden-filter', preset !== 'custom');
  if (preset !== 'custom') {
    loadJobs();
  }
  // For custom, reload is triggered by the date input event listeners
}
/* ── Toggle date group collapse ──────────────────────────────────── */
function toggleGroup(dateStr, headerEl) {
  const nowCollapsed = !collapsedGroups.has(dateStr);
  if (nowCollapsed) {
    collapsedGroups.add(dateStr);
  } else {
    collapsedGroups.delete(dateStr);
  }
  headerEl.classList.toggle('collapsed', nowCollapsed);
  const container = document.querySelector(`.date-group-cards[data-group="${CSS.escape(dateStr)}"]`);
  if (container) container.classList.toggle('hidden', nowCollapsed);
}
/* ── Load jobs ───────────────────────────────────────────────────── */
async function loadJobDescription(detailsEl) {
  if (!detailsEl.open) return;
  const body = detailsEl.querySelector('.job-description-body');
  if (!body || body.dataset.loaded === '1') return;
  const jobId = detailsEl.dataset.jobId;
  if (!jobId) return;
  body.textContent = 'Loading…';
  try {
    const res = await fetch(`/api/jobs/${jobId}/description`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed');
    const text = (data.description || '').trim();
    body.textContent = text || 'No description saved.';
    body.dataset.loaded = '1';
  } catch (err) {
    body.textContent = `Failed to load: ${err.message}`;
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    // Fallback for non-secure contexts / older browsers
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    ta.remove();
    return ok;
  }
}

/** Copy the full original JD (fetched if the panel was never opened). */
async function copyJobDescription(btn) {
  const jobId = btn.dataset.jobId;
  const details = btn.closest('.job-description');
  const body = details && details.querySelector('.job-description-body');
  let text = body && body.dataset.loaded === '1' ? body.textContent : '';
  try {
    if (!text) {
      const res = await fetch(`/api/jobs/${jobId}/description`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Failed to load');
      text = (data.description || '').trim();
      if (text && body) {
        body.textContent = text;
        body.dataset.loaded = '1';
      }
    }
    if (!text || text === 'No description saved.') {
      showToast('No description saved for this job', 'err');
      return;
    }
    if (!(await copyText(text))) throw new Error('Clipboard blocked');
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    showToast(`Copied description (${text.length} chars)`);
  } catch (err) {
    showToast('Copy failed: ' + err.message, 'err');
  }
}

function currentJobListParams() {
  const params = new URLSearchParams();
  const search = document.getElementById('searchInput').value.trim();
  const source = document.getElementById('sourceFilter').value;
  const dates  = currentDateRange();
  const country = document.getElementById('countryFilter').value;
  const origin = document.getElementById('originFilter').value;
  if (origin !== 'all') params.set('origin', origin);
  if (currentStatusFilter !== 'all') params.set('status', currentStatusFilter);
  if (country !== 'all') params.set('country', country);
  if (highlightsOnly) params.set('highlights', 'true');
  if (source !== 'all') params.set('source', source);
  if (search) params.set('q', search);
  if (companyFilter) params.set('company', companyFilter);
  if (appliedOnFilter) params.set('applied_on', appliedOnFilter);
  if (dates.from) params.set('date_from', dates.from);
  if (dates.to)   params.set('date_to',   dates.to);
  return params;
}

async function loadJobs() {
  const grid   = document.getElementById('jobGrid');
  if (window.updateFilterCount) updateFilterCount();

  if (jobsAbortController) jobsAbortController.abort();
  jobsAbortController = new AbortController();
  const signal = jobsAbortController.signal;

  grid.innerHTML = '<div class="spinner"></div>';
  grid.classList.toggle('layout-compact', currentLayout === 'compact');
  grid.classList.toggle('layout-cards', currentLayout === 'cards');

  const params = currentJobListParams();
  params.set('page', currentPage);
  params.set('page_size', PAGE_SIZE);

  try {
    const res  = await fetch('/api/jobs?' + params.toString(), { signal });
    const data = await res.json();

    totalPages = data.pages || 1;
    totalJobs = data.total || 0;
    currentPage = data.page || 1;

    grid.innerHTML = '';
    grid.classList.toggle('layout-compact', currentLayout === 'compact');
    grid.classList.toggle('layout-cards', currentLayout === 'cards');
    document.getElementById('countLabel').textContent = `Page ${currentPage} of ${totalPages} (${totalJobs} total)`;

    if (!data.jobs.length) {
      grid.innerHTML = `
        <div class="empty">
          <h2>No Matched Jobs</h2>
          <p>Try different filters?</p>
        </div>`;
      updatePagination();
      return;
    }

    // Group by date and render with collapsible support
    const groups = groupJobsByDate(data.jobs);
    groups.forEach(([dateStr, jobs]) => {
      const isCollapsed = collapsedGroups.has(dateStr);

      // Header
      const hdr = document.createElement('div');
      hdr.className = 'date-group-header' + (isCollapsed ? ' collapsed' : '');
      hdr.innerHTML = `
        <span class="dg-toggle">▾</span>
        <span>${formatGroupDate(dateStr)}</span>
        <span class="dg-count">${jobs.length} jobs</span>
      `;
      hdr.addEventListener('click', () => toggleGroup(dateStr, hdr));
      grid.appendChild(hdr);

      // Cards container (display:contents so cards stay in the outer grid)
      const container = document.createElement('div');
      container.className = 'date-group-cards' + (isCollapsed ? ' hidden' : '');
      container.dataset.group = dateStr;
      jobs.forEach(job => {
        const card = renderCard(job);
        container.appendChild(card);
      });
      grid.appendChild(container);
    });
    
    updatePagination();
    updateBatchFetchUI();
    await focusOpenJobFromQuery();
  } catch (err) {
    if (err.name === 'AbortError') return;
    grid.innerHTML = `<div class="empty"><h2>Loading Failed</h2><p>${err.message}</p></div>`;
  } finally {
    if (!signal.aborted) resolveListReady();
  }
}

/* ── Deep-link: /?open=<matched_jobs _id> ─────────────────────────── */
function expandDateGroupForCard(card) {
  const container = card.closest('.date-group-cards');
  if (!container || !container.classList.contains('hidden')) return;
  const group = container.dataset.group;
  const hdr = container.previousElementSibling;
  if (hdr && hdr.classList.contains('date-group-header')) {
    toggleGroup(group, hdr);
  } else {
    container.classList.remove('hidden');
    if (group) collapsedGroups.delete(group);
  }
}

function openMatchedCard(card) {
  if (!card) return;
  expandDateGroupForCard(card);
  collapseOtherCards(card);
  setCardOpen(card, true);
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
    openMatchedCard(card);
    showToast('Opened matched job');
    return;
  }

  try {
    const res = await fetch(`/api/jobs/${openId}`);
    const job = await res.json();
    if (!res.ok) {
      showToast(job.error || 'Job not found', 'err');
      return;
    }

    const banner = document.createElement('div');
    banner.className = 'open-job-banner';
    banner.innerHTML = `
      <span>Opened job from URL (not on this page/filter) — <strong>${escHtml(job.title || '')}</strong></span>
      <a href="/">Show all matches</a>
    `;
    const injected = renderCard(job);
    injected.classList.add('open-flash');
    injected.classList.add('expanded');
    syncPanelScrim();

    const empty = grid.querySelector('.empty');
    if (empty) empty.remove();
    const spinner = grid.querySelector('.spinner');
    if (spinner) spinner.remove();

    grid.insertBefore(banner, grid.firstChild);
    grid.insertBefore(injected, banner.nextSibling);
    injected.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => injected.classList.remove('open-flash'), 2800);
    showToast('Opened matched job');
  } catch (err) {
    showToast(`Open failed: ${err.message}`, 'err');
  }
}
/* ── Init ────────────────────────────────────────────────────────── */
function loadOverviewData() {
  loadJourney();
  loadLocationStats();
  loadDailyStats();
}

function loadAfterJobList() {
  loadStats();
  loadOverviewData();
}

function refreshAll() {
  // Job list first; chips + Overview after so they do not race /api/jobs.
  loadJobs().finally(loadAfterJobList);
}

// Auto-refresh the page when templates or static JS/CSS change (no server restart needed).
// Local development only: in production this polled every 1.5 s from every open
// tab (and the server walked the template + static folders for each poll).
(function setupTemplateHotReload() {
  if (!['127.0.0.1', 'localhost'].includes(location.hostname)) return;
  let token = null;
  async function check() {
    if (document.hidden) return;
    try {
      const res = await fetch('/api/dev/reload-token', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (token === null) {
        token = data.token;
        return;
      }
      if (data.token && data.token !== token) {
        token = data.token;
        location.reload();
      }
    } catch (_) {}
  }
  setInterval(check, 1500);
  check();
})();

// Date input listeners – debounced so the native picker doesn't fire mid-pick
['dateFrom', 'dateTo'].forEach(id => {
  document.getElementById(id).addEventListener('change', function() {
    if (document.getElementById('timePreset').value === 'custom') {
      clearTimeout(dateDebounceTimer);
      dateDebounceTimer = setTimeout(loadJobs, 400);
    }
  });
});

window.__pendingOpenJobId = consumeOpenJobId();
const initialParams = new URLSearchParams(location.search);
const initialCompany = (initialParams.get('company') || '').trim();
const initialApplied = (initialParams.get('applied') || '').trim();

setLayout(currentLayout);
// ?company=… / ?applied=YYYY-MM-DD → open straight into that view
if (initialApplied) {
  if (initialCompany) {
    companyFilter = initialCompany;
    applyCompanyFilterUI();
  }
  filterByAppliedDay(initialApplied);
} else if (initialCompany) {
  filterByCompany(initialCompany);
} else {
  loadJobs();
}
// Chips + Overview after the first list (or the 4s safety timeout)
listReady.then(loadAfterJobList);
