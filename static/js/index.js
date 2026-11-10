/* Tracker page (templates/index.html) script. Classic script, not a module:
   inline onclick="..." handlers in the page call these globals. */
/* ── State ───────────────────────────────────────────────────────── */
let currentStatusFilter = 'active';
let highlightsOnly = false;
let companyFilter = ''; // exact company (click a company name on a card)
let appliedOnFilter = ''; // YYYY-MM-DD: jobs applied that day (heatmap → /?applied=…)
let debounceTimer = null;
let dateDebounceTimer = null;
let jobsAbortController = null;
const SEARCH_DEBOUNCE_MS = 600; // wait until typing pauses before searching
// Touch screens cannot hover: copy says "tap" there
const POINT_VERB = window.matchMedia?.('(hover: none)').matches ? 'tap' : 'hover';
document.querySelectorAll('[data-point-verb]').forEach(el => { el.textContent = POINT_VERB; });
// Chips (/api/stats) and Overview (journey / heatmap / locations) wait for
// the first job list, so on a cold server /api/jobs gets Atlas first.
// Never hold them back for more than a few seconds.
let resolveListReady;
const listReady = new Promise(resolve => { resolveListReady = resolve; });
setTimeout(() => resolveListReady(), 4000);
const collapsedGroups = new Set(); // tracks which date groups are collapsed
let currentLayout = localStorage.getItem('jobLayout') || 'compact';
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
const RECOMMENDED_BY_LABEL = {
  claude: 'Claude',
  cursor: 'Cursor',
  gemini: 'Gemini',
  gpt: 'GPT',
};
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
const TERMINAL_STEPS = new Set(['rejected', 'offer']);

/** In-memory timeline cache keyed by job id */
const jobTimelines = {};

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
  // Collapse all expanded cards when switching to compact
  if (mode === 'compact') {
    grid.querySelectorAll('.card.expanded').forEach(c => c.classList.remove('expanded'));
  }
}

/* Open or close one card's details panel. With focus: opening moves focus to
   the panel title (screen readers announce the job), closing returns it to the row. */
function setCardOpen(card, open, { focus = false } = {}) {
  card.classList.toggle('expanded', open);
  const row = card.querySelector('.card-row');
  if (row) row.setAttribute('aria-expanded', String(open));
  if (!focus) return;
  const target = open ? card.querySelector('.card-title-text') : row;
  target?.focus({ preventScroll: true });
}

function toggleCardExpand(rowEl) {
  if (currentLayout !== 'compact') return;
  const card = rowEl.closest('.card');
  if (!card) return;
  const open = !card.classList.contains('expanded');
  collapseOtherCards(card);
  setCardOpen(card, open, { focus: open });
}

/* List layout shows the open card's body as a details panel: keep one open */
function collapseOtherCards(keep) {
  document.querySelectorAll('#jobGrid .card.expanded').forEach(c => {
    if (c !== keep) setCardOpen(c, false);
  });
}

function closeCardPanel(btn) {
  const card = btn.closest('.card');
  if (card) setCardOpen(card, false, { focus: true });
}

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || currentLayout !== 'compact') return;
  if (document.querySelector('.tl-modal-backdrop')) return;
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  const open = document.querySelector('#jobGrid .card.expanded');
  if (open) setCardOpen(open, false, { focus: true });
});

/* ── Toast ───────────────────────────────────────────────────────── */
function showToast(msg, type = 'ok', ms = 2500) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${type} show`;
  setTimeout(() => { t.className = 'toast'; }, ms);
}

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
    ['active','pending','applied','rejected','interview','offer','unsuitable','closed','closed_no_reply','prev_rejected','too_many_questions','repost','duplicate'].forEach(s => {
      if (!data.by_status[s]) {
        const el = document.getElementById(`cnt-${s}`);
        if (el) el.textContent = 0;
      }
    });

    // Funnel (new)
    if (data.funnel) {
      document.getElementById('f-clicked').textContent = data.funnel.title_clicked;
      document.getElementById('f-german').textContent  = data.funnel.german_filtered;
      document.getElementById('f-matched').textContent = data.funnel.ai_matched;
      document.getElementById('f-applied').textContent = data.funnel.applied;
      sizeFunnelRail(data.funnel);
    }
  } catch (_) {}
}

/* Pipeline rail: one bar, to scale, where the full width is every opened job.
   Parts: not in English | in English, not matched | AI matched (Applied inside it).
   Every percentage is a share of all opened jobs, same as the widths. */
function sizeFunnelRail(funnel) {
  const opened = Number(funnel.title_clicked) || 0;
  const german = Number(funnel.german_filtered) || 0;
  const matched = Number(funnel.ai_matched) || 0;
  const applied = Math.min(Number(funnel.applied) || 0, matched);
  // Manually logged / API jobs can push "matched" past the English count:
  // clamp so the parts never go negative, and scale to their sum.
  const englishRest = Math.max(opened - german - matched, 0);
  const total = german + englishRest + matched;

  const fmt = (n) => n.toLocaleString('en-US');
  const pct = (n) => {
    if (!total) return '';
    const p = (n / total) * 100;
    return `${p > 0 && p < 10 ? p.toFixed(1) : Math.round(p)}% of opened`;
  };
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  set('f-clicked', fmt(opened));
  set('f-german', fmt(german));      set('fp-german', pct(german));
  set('f-english', fmt(englishRest)); set('fp-english', pct(englishRest));
  set('f-matched', fmt(matched));    set('fp-matched', pct(matched));
  set('f-applied', fmt(applied));    set('fp-applied', pct(applied));

  const bar = document.getElementById('railBar');
  if (!bar) return;
  const share = (n, of) => (of ? `${(n / of) * 100}%` : '0%');
  const seg = (key) => bar.querySelector(`.seg[data-key="${key}"]`);
  seg('german').style.width = share(german, total);
  seg('english').style.width = share(englishRest, total);
  seg('matched').style.width = share(matched, total);
  // Applied is drawn inside the matched part, so its width is relative to matched
  seg('applied').style.width = share(applied, matched);
  ['german', 'english', 'matched', 'applied'].forEach(key => {
    const n = { german, english: englishRest, matched, applied }[key];
    seg(key).hidden = n === 0;
  });
  requestAnimationFrame(placeRailLabels);
}

/* A part too narrow for its text shows the text under the bar instead */
function placeRailLabels() {
  const bar = document.getElementById('railBar');
  const outside = document.getElementById('railOutside');
  if (!bar || !outside) return;
  outside.innerHTML = '';
  bar.querySelectorAll('.seg').forEach(s => s.classList.remove('narrow'));
  bar.querySelectorAll('.seg').forEach(s => {
    if (s.hidden) return;
    const label = s.querySelector(':scope > .seg-label');
    // Room for the label: the part's width, minus the Applied block nested in it
    const inner = s.querySelector(':scope > .seg');
    const room = s.clientWidth - (inner && !inner.hidden ? inner.offsetWidth : 0);
    const need = Math.max(...[...label.children].map(c => c.offsetWidth)) + 24; // + label padding
    // Phone: bar is a thin strip; labels always sit underneath.
    const compact = window.matchMedia('(max-width: 760px)').matches;
    if (!compact && need <= room) return;
    s.classList.add('narrow');
    const item = document.createElement('div');
    item.className = `rail-out rail-out-${s.dataset.key}`;
    const clone = label.cloneNode(true);
    clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    item.innerHTML = '<span class="sw"></span>';
    item.append(...clone.childNodes);
    outside.appendChild(item);
  });
}

// Web fonts change text widths once they arrive
document.fonts?.ready.then(() => placeRailLabels());

window.addEventListener('resize', () => {
  clearTimeout(placeRailLabels._t);
  placeRailLabels._t = setTimeout(placeRailLabels, 120);
});

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

/* ── Application Journey ─────────────────────────────────────────── */
function formatJourneyDate(iso) {
  if (!iso) return '';
  const m = String(iso).match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}`;
}

function journeyNodeClass(step) {
  if (step === 'applied') return 'step-applied';
  if (step === 'reapplied') return 'step-reapplied';
  if (step === 'rejected') return 'step-rejected';
  if (step === 'offer') return 'step-offer';
  if (String(step).startsWith('interview_')) return 'step-interview';
  return '';
}

function parseJourneyDay(iso) {
  const m = String(iso || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function journeyPct(day, start, end) {
  if (!day || !start || !end) return 0;
  const t0 = start.getTime();
  const t1 = end.getTime();
  if (t1 <= t0) return 0;
  const p = ((day.getTime() - t0) / (t1 - t0)) * 100;
  return Math.max(0, Math.min(100, p));
}

function renderCompanyTrack(track, start, end) {
  const steps = (track.steps || [])
    .map(s => ({ ...s, _day: parseJourneyDay(s.date || s.at) }))
    .filter(s => s._day)
    .sort((a, b) => a._day - b._day);

  const segs = [];
  for (let i = 0; i < steps.length - 1; i++) {
    const a = journeyPct(steps[i]._day, start, end);
    const b = journeyPct(steps[i + 1]._day, start, end);
    const left = Math.min(a, b);
    const width = Math.max(0, Math.abs(b - a));
    let cls = 'jt-seg';
    if (steps[i + 1].step === 'rejected') cls += ' end-rejected';
    else if (steps[i + 1].step === 'offer') cls += ' end-offer';
    segs.push(`<div class="${cls}" style="left:${left}%;width:${width}%"></div>`);
  }

  const nodes = steps.map(s => {
    const label = s.label || s.step;
    const when = formatJourneyDate(s.date || s.at);
    const tip = when ? `${label} · ${when}` : label;
    const left = journeyPct(s._day, start, end);
    return `
      <button type="button" class="jt-node ${journeyNodeClass(s.step)}"
              style="left:${left}%"
              data-tip="${escHtml(tip)}"
              onmouseenter="showJourneyTip(this)"
              onmouseleave="hideJourneyTip()"
              onfocus="showJourneyTip(this)"
              onblur="hideJourneyTip()"
              aria-label="${escHtml(tip)}">
        <span class="jt-dot"></span>
      </button>`;
  }).join('');

  const company = track.company || 'Unknown';
  const title = track.title || '';

  return `
    <div class="journey-track">
      <div class="journey-plot">
        <div class="journey-scale">${segs.join('')}${nodes}</div>
      </div>
      <div class="journey-side">
        <button type="button" class="jt-info"
                data-company="${escHtml(company)}"
                data-title="${escHtml(title)}"
                title="Company info"
                onclick="toggleCompanyPop(this)"
                aria-label="Company info">i</button>
      </div>
    </div>
  `;
}

function toggleCompanyPop(btn) {
  const key = `${btn.dataset.company || ''}||${btn.dataset.title || ''}`;
  const existing = document.getElementById('jtCompanyPop');
  if (existing && existing.dataset.anchorId === key) {
    hideCompanyPop();
    return;
  }
  hideCompanyPop();

  btn.classList.add('active');

  const pop = document.createElement('div');
  pop.id = 'jtCompanyPop';
  pop.className = 'jt-company-pop';
  pop.dataset.anchorId = key;
  pop.innerHTML = `
    <div class="jcp-company">${escHtml(btn.dataset.company || 'Unknown')}</div>
    ${btn.dataset.title ? `<div class="jcp-title">${escHtml(btn.dataset.title)}</div>` : ''}
  `;
  document.body.appendChild(pop);

  const r = btn.getBoundingClientRect();
  const pad = 8;
  const pw = pop.offsetWidth;
  const ph = pop.offsetHeight;
  let left = r.right - pw;
  let top = r.bottom + 6;
  if (left < pad) left = pad;
  if (left + pw > window.innerWidth - pad) left = window.innerWidth - pad - pw;
  if (top + ph > window.innerHeight - pad) top = r.top - ph - 6;
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
  requestAnimationFrame(() => pop.classList.add('show'));
}

function hideCompanyPop() {
  document.querySelectorAll('.jt-info.active').forEach(el => el.classList.remove('active'));
  const pop = document.getElementById('jtCompanyPop');
  if (pop) pop.remove();
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.jt-info') || e.target.closest('#jtCompanyPop')) return;
  hideCompanyPop();
});

function showJourneyTip(el) {
  hideCompanyPop();
  const text = el.getAttribute('data-tip') || '';
  if (!text) return;
  let tip = document.getElementById('jtFloatingTip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'jtFloatingTip';
    tip.className = 'jt-floating-tip';
    document.body.appendChild(tip);
  }
  tip.textContent = text;
  const r = el.getBoundingClientRect();
  tip.style.left = `${r.left + r.width / 2}px`;
  tip.style.top = `${r.top}px`;
  tip.classList.add('show');
}

function hideJourneyTip() {
  const tip = document.getElementById('jtFloatingTip');
  if (tip) tip.classList.remove('show');
}

async function loadJourney() {
  const root = document.getElementById('journeyRoot');
  if (!root) return;
  hideCompanyPop();
  try {
    const res = await fetch('/api/journey');
    const data = await res.json();
    const total = data.total_applied || 0;

    if (!total) {
      root.innerHTML = '<div class="journey-empty">No applications recorded yet</div>';
      return;
    }

    const start = parseJourneyDay(data.start_date);
    const end = parseJourneyDay(data.end_date);
    const today = parseJourneyDay(data.today || data.end_date);
    const todayPct = journeyPct(today, start, end);
    const showToday = today && start && end && today > start && today < end;

    const tracks = data.tracks || [];
    const tracksHtml = tracks.length
      ? `<div class="journey-tracks">${tracks.map(t => renderCompanyTrack(t, start, end)).join('')}</div>`
      : `<div class="journey-empty">No interview timelines yet</div>`;

    root.innerHTML = `
      <div class="journey-panel">
        <div class="journey-overview">
          <div class="journey-axis">
            <div class="journey-scale">
              <div class="journey-axis-rail"></div>
              ${showToday ? `<div class="journey-axis-today" style="left:${todayPct}%"></div>` : ''}
              <span class="jo-endpoint start"></span>
              <span class="jo-endpoint end"></span>
              <span class="journey-axis-label start">${formatJourneyDate(data.start_date)}</span>
              <span class="journey-axis-label end">${formatJourneyDate(data.end_date)}</span>
            </div>
          </div>
          <div class="journey-overview-total">
            <div class="jot-num">${total}</div>
            <div class="jot-label">applied</div>
          </div>
        </div>
        ${tracksHtml}
      </div>
    `;
  } catch (_) {
    root.innerHTML = '<div class="journey-empty">Failed to load journey</div>';
  }
}

/* ── Applied location bubbles ────────────────────────────────────── */
/* Same hues as the funnel / daily-activity numbers, washed to glass. */
const LOC_BUBBLE_COLORS = [
  // Theme tokens from base.css, so the bubbles follow light / dark mode
  { fill: 'color-mix(in srgb, var(--accent) 80%, transparent)',    ink: 'var(--surface)' },
  { fill: 'color-mix(in srgb, var(--applied) 80%, transparent)',   ink: 'var(--surface)' },
  { fill: 'color-mix(in srgb, var(--interview) 82%, transparent)', ink: 'var(--surface)' },
  { fill: 'color-mix(in srgb, var(--hl) 92%, transparent)',        ink: 'var(--hl-ink)' },
  { fill: 'color-mix(in srgb, var(--offer) 80%, transparent)',     ink: 'var(--surface)' },
];

function packLocationBubbles(items, width, height) {
  // Greedy circle pack: largest first, nest tightly around the center.
  // inset = stay inside the rounded clip; gap = space between circles.
  // Never overlaps: items with no free spot are returned in `skipped`.
  const cx = width / 2;
  const cy = height / 2;
  const placed = [];
  const skipped = [];
  const inset = 8;
  const gap = 2;

  const fits = (x, y, r) => {
    if (x - r < inset || x + r > width - inset || y - r < inset || y + r > height - inset) {
      return false;
    }
    for (const p of placed) {
      if (Math.hypot(x - p.x, y - p.y) < p.r + r + gap - 0.5) return false;
    }
    return true;
  };

  for (const item of items) {
    if (!placed.length) {
      if (fits(cx, cy, item.r)) placed.push({ ...item, x: cx, y: cy });
      else skipped.push(item);
      continue;
    }

    let best = null;
    for (const other of placed) {
      const ring = other.r + item.r + gap;
      for (let a = 0; a < 360; a += 5) {
        const rad = (a * Math.PI) / 180;
        const x = other.x + Math.cos(rad) * ring;
        const y = other.y + Math.sin(rad) * ring;
        if (!fits(x, y, item.r)) continue;
        const score = Math.hypot(x - cx, y - cy);
        if (!best || score < best.score) best = { x, y, score };
      }
    }

    if (best) placed.push({ ...item, x: best.x, y: best.y });
    else skipped.push(item);
  }
  return { placed, skipped };
}

function renderLocationBubbles(locations, total) {
  const root = document.getElementById('locBubbles');
  if (!root) return;

  const width = Math.max(root.clientWidth || 640, 320);
  const height = Math.max(root.clientHeight || 280, 200);
  const maxCount = Math.max(...locations.map(l => l.count || 0), 1);

  // Smaller bubbles on mobile so the packed cloud stays inside the canvas
  const isMobile = window.innerWidth <= 768;
  const baseMinR = isMobile ? 14 : 22;
  const baseMaxR = isMobile ? Math.min(32, Math.floor(Math.min(width, height) * 0.15)) : Math.min(58, Math.floor(Math.min(width, height) * 0.22));

  const buildItems = (scale) => {
    const minR = Math.max(12, baseMinR * scale);
    const maxR = Math.max(minR, baseMaxR * scale);
    return locations.map((loc, i) => {
      const count = loc.count || 0;
      const t = Math.sqrt(count / maxCount);
      const palette = LOC_BUBBLE_COLORS[i % LOC_BUBBLE_COLORS.length];
      return {
        name: loc.name || 'Remote',
        count,
        r: Math.round(minR + (maxR - minR) * t),
        color: palette.fill,
        ink: palette.ink,
      };
    });
  };

  // Shrink all bubbles until every one fits without overlap; if even the
  // smallest scale can't fit them all, drop the leftovers (smallest counts).
  let packed = null;
  for (let scale = 1; scale >= 0.5; scale -= 0.08) {
    packed = packLocationBubbles(buildItems(scale), width, height);
    if (!packed.skipped.length) break;
  }

  root.classList.remove('has-open');
  root.innerHTML = packed.placed.map(b => {
    const size = b.r * 2;
    return `
      <div class="loc-bubble"
           style="--size:${size}px; --bubble:${b.color}; --bubble-ink:${b.ink}; left:${b.x}px; top:${b.y}px"
           role="button"
           tabindex="0"
           aria-label="${escHtml(b.name)}: ${b.count}"
           onclick="toggleLocBubble(this)">
        <span class="lb-name">${escHtml(b.name)}</span>
        <span class="lb-count">${b.count}</span>
      </div>`;
  }).join('');
}

let _locStatsCache = null;
let _locResizeTimer = null;

function toggleLocBubble(el) {
  const root = document.getElementById('locBubbles');
  const wasOpen = el.classList.contains('is-open');
  document.querySelectorAll('.loc-bubble.is-open').forEach(b => b.classList.remove('is-open'));
  if (!wasOpen) {
    el.classList.add('is-open');
    if (root) root.classList.add('has-open');
  } else if (root) {
    root.classList.remove('has-open');
  }
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.loc-bubble')) return;
  document.querySelectorAll('.loc-bubble.is-open').forEach(b => b.classList.remove('is-open'));
  document.getElementById('locBubbles')?.classList.remove('has-open');
});

async function loadLocationStats() {
  const root = document.getElementById('locBubbles');
  const sub = document.getElementById('locSubtitle');
  if (!root) return;
  try {
    const res = await fetch('/api/stats/locations');
    const data = await res.json();
    const total = data.total || 0;
    // Single-job cities add clutter; only chart places with 2+ applications
    const locations = (data.locations || [])
      .filter(l => (l.count || 0) >= 2)
      .sort((a, b) => (b.count || 0) - (a.count || 0));
    _locStatsCache = { locations, total };

    if (sub) {
      sub.textContent = total
        ? `${total} applied · ${POINT_VERB} a bubble for count`
        : 'Applied · rejected · interview · offer';
    }
    if (!locations.length) {
      root.innerHTML = `<div class="loc-empty">${total ? 'No city with 2+ applications yet' : 'No applied jobs yet'}</div>`;
      return;
    }
    // Wait a frame so clientWidth is correct after layout
    requestAnimationFrame(() => renderLocationBubbles(locations, total));
  } catch (_) {
    root.innerHTML = '<div class="loc-empty">Failed to load locations</div>';
  }
}

window.addEventListener('resize', () => {
  clearTimeout(_locResizeTimer);
  _locResizeTimer = setTimeout(sizeHeatmapToWidth, 120);
});

// The canvas also changes size without a window resize: it stretches to the
// Journey panel's height once that loads, and is 0×0 while Overview is hidden.
// Re-pack the bubbles whenever its real size changes.
(() => {
  const root = document.getElementById('locBubbles');
  if (!root || !window.ResizeObserver) return;
  let lastW = 0, lastH = 0, timer = null;
  new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect;
    if (!width || !height) return; // hidden tab
    if (Math.abs(width - lastW) < 2 && Math.abs(height - lastH) < 2) return;
    lastW = width; lastH = height;
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (_locStatsCache?.locations?.length) {
        renderLocationBubbles(_locStatsCache.locations, _locStatsCache.total);
      }
    }, 120);
  }).observe(root);
})();

/* ── Activity calendar heatmap ───────────────────────────────────── */
let calDaysByDate = {};
let calSelectedDate = null;

function calActivityScore(d) {
  return (d.title_clicked || 0) + (d.ai_matched || 0) + (d.applied || 0);
}

function calLevel(score, maxScore) {
  if (!score || maxScore <= 0) return 0;
  const r = score / maxScore;
  if (r <= 0.25) return 1;
  if (r <= 0.5)  return 2;
  if (r <= 0.75) return 3;
  return 4;
}

function showCalDetail(dateStr) {
  const d = calDaysByDate[dateStr];
  if (!d) return;
  calSelectedDate = dateStr;
  document.querySelectorAll('.cal-cell.selected').forEach(el => el.classList.remove('selected'));
  const cell = document.querySelector(`.cal-cell[data-date="${dateStr}"]`);
  if (cell) cell.classList.add('selected');

  const today = formatDate(new Date());
  let label = dateStr;
  if (dateStr === today) label = `Today · ${dateStr}`;
  else {
    const yd = new Date(); yd.setDate(yd.getDate() - 1);
    if (dateStr === formatDate(yd)) label = `Yesterday · ${dateStr}`;
  }
  document.getElementById('calDetailDate').textContent = label;
  document.getElementById('calDetailDate').classList.remove('muted');
  document.getElementById('cd-clicked').textContent = d.title_clicked;
  document.getElementById('cd-german').textContent  = d.german_filtered;
  document.getElementById('cd-matched').textContent = d.ai_matched;
  document.getElementById('cd-applied').textContent = d.applied;
  const link = document.getElementById('calDetailLink');
  link.hidden = !d.applied;
  link.href = appliedDayUrl(dateStr);
}

/** Tracker URL listing the jobs applied (or reapplied) on one day. */
function appliedDayUrl(dateStr) {
  return `/?applied=${encodeURIComponent(dateStr)}`;
}

async function loadDailyStats() {
  const container = document.getElementById('calMonths');
  if (!container) return;

  try {
    // ~3 months (13–14 week columns)
    const res = await fetch('/api/stats/daily?days=92');
    const data = await res.json();
    const days = data.days || [];
    calDaysByDate = {};
    days.forEach(d => { calDaysByDate[d.date] = d; });

    const maxScore = Math.max(0, ...days.map(calActivityScore));

    // Build week columns starting from the Sunday of the first day
    if (!days.length) {
      container.innerHTML = '';
      return;
    }

    const first = new Date(days[0].date + 'T12:00:00');
    const last  = new Date(days[days.length - 1].date + 'T12:00:00');
    // Align start to Sunday
    const gridStart = new Date(first);
    gridStart.setDate(gridStart.getDate() - gridStart.getDay());

    const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const weeks = [];
    let cursor = new Date(gridStart);
    while (cursor <= last || cursor.getDay() !== 0 || weeks.length === 0) {
      const week = [];
      for (let i = 0; i < 7; i++) {
        const ds = formatDate(cursor);
        const inRange = calDaysByDate[ds];
        week.push(inRange ? { date: ds, data: inRange } : { date: ds, data: null });
        cursor.setDate(cursor.getDate() + 1);
      }
      weeks.push(week);
      if (cursor > last && cursor.getDay() === 0) break;
      // safety
      if (weeks.length > 60) break;
    }

    container.innerHTML = '';
    let prevMonth = -1;
    weeks.forEach(week => {
      const col = document.createElement('div');
      col.className = 'cal-month-col';

      // Month label on the first week that contains the 1st, or first week of a new month
      const mid = week.find(c => c.data) || week[0];
      const midDate = new Date(mid.date + 'T12:00:00');
      const month = midDate.getMonth();
      const label = document.createElement('div');
      label.className = 'cal-month-label';
      if (month !== prevMonth) {
        label.textContent = monthNames[month];
        prevMonth = month;
      }
      col.appendChild(label);

      const weekEl = document.createElement('div');
      weekEl.className = 'cal-week';
      week.forEach(cell => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'cal-cell';
        if (!cell.data) {
          btn.classList.add('empty');
          btn.disabled = true;
          btn.tabIndex = -1;
        } else {
          const score = calActivityScore(cell.data);
          btn.classList.add('l' + calLevel(score, maxScore));
          btn.dataset.date = cell.date;
          btn.title = `${cell.date}: ${cell.data.title_clicked} clicked, ${cell.data.german_filtered} german, ${cell.data.ai_matched} matched, ${cell.data.applied} applied`;
          btn.addEventListener('mouseenter', () => showCalDetail(cell.date));
          if (cell.data.applied) {
            btn.classList.add('has-applied');
            btn.title += ' · click to open that day\'s applications';
          }
          btn.addEventListener('click', () => {
            showCalDetail(cell.date);
            // Days with applications open them in a new tab
            if (cell.data.applied) window.open(appliedDayUrl(cell.date), '_blank', 'noopener');
          });
        }
        weekEl.appendChild(btn);
      });
      col.appendChild(weekEl);
      container.appendChild(col);
    });

    // Default detail: today (or last day with activity)
    const today = formatDate(new Date());
    if (calDaysByDate[today]) {
      showCalDetail(today);
    } else {
      const withActivity = [...days].reverse().find(d => calActivityScore(d) > 0 || d.german_filtered > 0);
      if (withActivity) showCalDetail(withActivity.date);
    }

    requestAnimationFrame(sizeHeatmapToWidth);
  } catch (_) {
    container.innerHTML = '';
  }
}

function sizeHeatmapToWidth() {
  const section = document.getElementById('calSection');
  const body = section && section.querySelector('.cal-body');
  const wrap = section && section.querySelector('.cal-wrap');
  const detail = section && section.querySelector('.cal-detail');
  const months = document.getElementById('calMonths');
  if (!section || !body || !wrap || !months) return;

  const weekCount = months.querySelectorAll('.cal-month-col').length;
  if (!weekCount) return;

  const dow = wrap.querySelector('.cal-dow');
  const gap = parseFloat(getComputedStyle(section).getPropertyValue('--cal-gap')) || 2;
  const wrapGap = parseFloat(getComputedStyle(wrap).gap) || 8;
  const bodyGap = parseFloat(getComputedStyle(body).gap) || 10;
  const dowWidth = dow ? dow.getBoundingClientRect().width : 16;
  // On phones the details sit under the grid (column layout): full width for cells
  const stacked = getComputedStyle(body).flexDirection === 'column';
  const detailW = detail && !stacked ? detail.getBoundingClientRect().width : 0;
  const available = body.clientWidth - detailW - (stacked ? 0 : bodyGap) - dowWidth - wrapGap;
  const cell = Math.floor((available - (weekCount - 1) * gap) / weekCount);
  // Desktop keeps the full 26px cells when there is room; shrink to fit otherwise
  const maxCell = window.innerWidth > 900 ? 26 : (stacked ? 24 : (window.innerWidth <= 768 ? 16 : 18));
  if (cell >= 8) {
    section.style.setProperty('--cal-cell', `${Math.min(cell, maxCell)}px`);
  }
}

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

/* ── Date helpers ────────────────────────────────────────────────── */
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
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function formatGroupDate(dateStr) {
  if (!dateStr) return 'Unknown Date';
  const today = formatDate(new Date());
  const yd = new Date(); yd.setDate(yd.getDate() - 1);
  const yesterday = formatDate(yd);
  if (dateStr === today) return `Today · ${dateStr}`;
  if (dateStr === yesterday) return `Yesterday · ${dateStr}`;
  return dateStr;
}

function onTimePresetChange() {
  const preset = document.getElementById('timePreset').value;
  document.getElementById('customDateGroup').classList.toggle('hidden-filter', preset !== 'custom');
  if (preset !== 'custom') {
    loadJobs();
  }
  // For custom, reload is triggered by the date input event listeners
}

function currentDateRange() {
  const preset = document.getElementById('timePreset').value;
  if (preset === 'all') return { from: '', to: '' };
  if (preset === 'custom') {
    return {
      from: document.getElementById('dateFrom').value,
      to:   document.getElementById('dateTo').value,
    };
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (preset === 'today') return { from: formatDate(today), to: formatDate(today) };
  const from = new Date(today);
  from.setDate(today.getDate() - (Number(preset) - 1));
  return { from: formatDate(from), to: formatDate(today) };
}

/* ── Group jobs by date ──────────────────────────────────────────── */
function groupJobsByDate(jobs) {
  const groups = new Map();
  jobs.forEach(job => {
    const dateStr = (job.matched_at || '').split(' ')[0] || 'Unknown Date';
    if (!groups.has(dateStr)) groups.set(dateStr, []);
    groups.get(dateStr).push(job);
  });
  return [...groups.entries()];
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

/* ── Score badge class ───────────────────────────────────────────── */
function scoreCls(score) {
  if (score == null || score === '') return 'score-pending';
  if (score >= 8)  return 'score-hi';
  if (score >= 6)  return 'score-mid';
  return 'score-lo';
}

function hasAiScore(job) {
  return job.match_score != null && job.match_score !== '';
}

function needsAiHint(job) {
  return Boolean(job.ai_pending) || !hasAiScore(job) || !job.summary;
}

function isMissingDetails(job) {
  if (!job) return false;
  if (job.details_missing) return true;
  const title = String(job.title || '');
  if (title.startsWith('(Link only)')) return true;
  if (job.ai_pending && !job.details_fetched_at) return true;
  return false;
}

function canFetchDetails(job) {
  const recKey = String((job && job.recommended_by) || '').toLowerCase();
  return isMissingDetails(job)
    || Boolean(job && job.can_fetch_details)
    || Boolean(RECOMMENDED_BY_LABEL[recKey]);
}

function formatScoreBadge(job) {
  if (!hasAiScore(job)) return '—';
  return Number(job.match_score).toFixed(1);
}

/* ── Job Wizard breakdown ────────────────────────────────────────── */
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
  // Auto-open sections that have gaps so mismatches are visible when the list card expands
  const openAttr = unmatched.length ? ' open' : '';
  return `
    <details class="breakdown"${openAttr}>
      <summary><span>${title}</span><span class="bd-count">${matched.length}/${total} matched</span></summary>
      <ul class="bd-list">${items}</ul>
    </details>`;
}

function normalizeHighlights(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const item of list) {
    const tag = String(item || '').trim().slice(0, 40);
    if (!tag) continue;
    const key = tag.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
    if (out.length >= 8) break;
  }
  return out;
}

function highlightBadgesHtml(highlights) {
  if (!highlights.length) return '';
  return highlights.map(tag =>
    `<span class="highlight-badge" title="${escHtml(tag)}">★ ${escHtml(tag)}</span>`
  ).join('');
}

/* source "manual" just means "not LinkedIn / Indeed"; for agent pushes
   show the job site's domain instead so it does not read as Log Job. */
function countryPillHtml(job) {
  const key = String(job.country || '');
  const label = String(job.country_label || '');
  if (!key || !label) return '';
  return `<span class="country-pill country-${escHtml(key)}">${escHtml(label)}</span>`;
}

function sourcePillHtml(job) {
  const source = String(job.source || '');
  // LinkedIn / Indeed are the board, shown as small "via …" on the second
  // line. They are not a channel next to ATS, Log job, or an agent.
  if (BOARD_LABEL[source.toLowerCase()]) return '';
  if (source === 'manual' && RECOMMENDED_BY_LABEL[String(job.recommended_by || '').toLowerCase()]) {
    let host = '';
    try { host = new URL(job.link || '').hostname.replace(/^www\./, ''); } catch (e) {}
    return host
      ? `<span class="meta-pill source-pill site-pill" title="${escHtml(job.link || '')}">${escHtml(host)}</span>`
      : '';
  }
  const badge = sourceBadgeHtml(job);
  if (badge) return badge;
  return `<span class="meta-pill source-pill">${escHtml(source)}</span>`;
}

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

/* Agent rows already show the ✦ agent badge and API rows the ◆ source badge,
   so only pipeline / manual get a pill. */
const ORIGIN_PILL = {
  pipeline: { label: 'Scraper', title: 'Found by the LinkedIn / Indeed scraper' },
  manual:   { label: 'Log Job', title: 'Logged by hand via Log Job' },
};

function originPillHtml(job) {
  const pill = ORIGIN_PILL[String(job.origin || '')];
  if (!pill) return '';
  return `<span class="meta-pill origin-pill origin-${escHtml(job.origin)}" title="${escHtml(pill.title)}">${escHtml(pill.label)}</span>`;
}

function recommendedByBadge(job) {
  const key = String(job.recommended_by || '').toLowerCase();
  const label = RECOMMENDED_BY_LABEL[key];
  if (!label) return '';
  const reason = String(job.recommend_reason || '').trim();
  const title = `Recommended by ${label}` + (reason ? ` — ${reason}` : '');
  return `<span class="meta-pill recommended-pill" title="${escHtml(title)}">✦ ${escHtml(label)}</span>`;
}

/* ── List row: one channel chip + a quiet sub line ──────────────────
   Channel = how the job reached the Tracker, one of four: ATS / API board,
   AI agent (Claude, Cursor…), Log job (by hand), or Scraper. The job board
   it is posted on (LinkedIn, Indeed) goes in the sub line as "via …". */
const BOARD_LABEL = { linkedin: 'LinkedIn', indeed: 'Indeed' };

function jobHost(job) {
  try { return new URL(job.link || '').hostname.replace(/^www\./, ''); } catch (e) { return ''; }
}

function jobChannel(job) {
  const rec = RECOMMENDED_BY_LABEL[String(job.recommended_by || '').toLowerCase()];
  if (rec) return { cls: 'ch-ai', label: `✦ ${rec}`, title: `Recommended by ${rec}` };
  const src = String(job.source || '').toLowerCase();
  const api = SOURCE_BADGE[src];
  if (api) return { cls: `ch-api ch-${src}`, label: `◆ ${api.label.replace(/ API$/, '')}`, title: api.title };
  if (job.origin === 'manual') return { cls: 'ch-manual', label: 'Log job', title: ORIGIN_PILL.manual.title };
  if (job.origin === 'pipeline' || BOARD_LABEL[src]) {
    return { cls: 'ch-scraper', label: ORIGIN_PILL.pipeline.label, title: ORIGIN_PILL.pipeline.title };
  }
  return src ? { cls: 'ch-other', label: src, title: '' } : null;
}

function channelChipHtml(job) {
  const ch = jobChannel(job);
  if (!ch) return '<span class="row-channel"></span>';
  return `<span class="row-channel"><span class="channel-chip ${escHtml(ch.cls)}" title="${escHtml(ch.title)}">${escHtml(ch.label)}</span></span>`;
}

/* Board (LinkedIn, Indeed) or, for agent / Log job rows, the site host.
   API / ATS rows have no "via". */
function jobViaText(job) {
  const src = String(job.source || '').toLowerCase();
  if (BOARD_LABEL[src]) return BOARD_LABEL[src];
  const ch = jobChannel(job);
  if (ch && (ch.cls === 'ch-ai' || ch.cls === 'ch-manual')) return jobHost(job);
  return '';
}

function rowSubHtml(job) {
  const viaText = jobViaText(job);
  const history = Array.isArray(job.company_history) ? job.company_history : [];
  let seen = '';
  if (history.length) {
    const total = job.company_history_total || history.length;
    const { applied, rejected } = companyHistoryFlags(history);
    const cls = rejected ? ' seen-rejected' : (applied ? ' seen-applied' : '');
    seen = `<span class="row-seen${cls}" title="${escHtml(`Seen ${total} other job(s) at this company: ${companyHistorySummary(history)}`)}">seen ${total}× before</span>`;
  }
  return `
        <div class="row-sub">
          <span class="row-company">${escHtml(job.company || '')}</span>
          <span class="row-loc" title="${escHtml(job.location || '')}">${escHtml(job.location || '')}</span>
          ${viaText ? `<span class="row-via">via ${escHtml(viaText)}</span>` : ''}
          ${seen}
        </div>`;
}

function renderHighlightChips(highlights) {
  return highlights.map(tag => `
    <span class="hl-chip">
      ★ ${escHtml(tag)}
      <button type="button" title="Remove" data-tag="${escHtml(tag)}" onclick="removeHighlight(this)">×</button>
    </span>
  `).join('');
}

function renderHighlightsBlock(job) {
  const id = job._id;
  const highlights = normalizeHighlights(job.highlights);
  jobHighlights[id] = highlights.slice();
  const presets = HIGHLIGHT_PRESETS.map(tag => {
    const active = highlights.some(h => h.toLowerCase() === tag.toLowerCase());
    return `<button type="button" class="hl-preset${active ? ' active' : ''}"
              data-preset="${escHtml(tag)}"
              onclick="addHighlightPreset('${id}', '${escHtml(tag)}')">${escHtml(tag)}</button>`;
  }).join('');
  return `
    <div class="highlights-wrap" data-id="${id}" id="highlights-${id}" onclick="event.stopPropagation()">
      <div class="highlights-label">Highlights</div>
      <div class="highlights-chips" data-chips>${renderHighlightChips(highlights)}</div>
      <div class="highlights-add-row">
        ${presets}
        <input class="hl-input" type="text" maxlength="40" placeholder="Custom tag…" aria-label="Custom highlight tag"
               onkeydown="if(event.key==='Enter'){event.preventDefault();addHighlightFromInput('${id}');}" />
        <button type="button" class="hl-add-btn" onclick="addHighlightFromInput('${id}')">+ Add</button>
      </div>
    </div>
  `;
}

/* ── Company history (other Tracker jobs at the same company) ────── */
function companyHistoryStatus(item) {
  const progress = item.progress || '';
  if (progress === 'rejected') return { label: 'Rejected', cls: 'st-rejected' };
  if (progress === 'interview') return { label: 'Interview', cls: 'st-interview' };
  if (progress === 'offer') return { label: 'Offer', cls: 'st-offer' };
  if (progress === 'applied') {
    if (item.status === 'closed_no_reply') return { label: 'Applied · closed, no reply', cls: 'st-applied' };
    return { label: 'Applied · no reply yet', cls: 'st-applied' };
  }
  return { label: STATUS_LABEL[item.status] || 'Not applied', cls: '' };
}

function companyHistorySummary(history) {
  const counts = {};
  history.forEach(item => {
    const { label } = companyHistoryStatus(item);
    const key = label.startsWith('Applied') ? 'Applied (no reply)' : label;
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.entries(counts).map(([k, n]) => `${n} ${k}`).join(' · ');
}

function companyHistoryFlags(history) {
  const applied = history.some(h => h.progress);
  const rejected = history.some(h => h.progress === 'rejected' || h.status === 'prev_rejected');
  return { applied, rejected };
}

function renderCompanyHistory(job) {
  const history = Array.isArray(job.company_history) ? job.company_history : [];
  if (!history.length) return '';
  const total = job.company_history_total || history.length;
  const { rejected } = companyHistoryFlags(history);
  const rows = history.slice(0, 5).map(item => {
    const st = companyHistoryStatus(item);
    const dates = [];
    if (item.applied_at) dates.push(`applied ${item.applied_at}`);
    if (item.progress && item.progress !== 'applied' && item.last_step_at) {
      dates.push(`${st.label.toLowerCase()} ${item.last_step_at}`);
    }
    if (!dates.length && item.matched_at) dates.push(`seen ${item.matched_at}`);
    if (item.applicant_position) dates.push(`#${item.applicant_position} applicant`);
    return `
      <li>
        <span class="ch-status ${st.cls}">${escHtml(st.label)}</span>
        <span class="ch-title">${escHtml(item.title || '(No Title)')}</span>
        <span class="ch-dates">${escHtml(dates.join(' · '))}</span>
      </li>`;
  }).join('');
  return `
    <div class="company-history${rejected ? ' seen-rejected' : ''}" onclick="event.stopPropagation()">
      <div class="ch-head">You've seen this company before — ${total} other job${total === 1 ? '' : 's'}
        <span class="ch-summary">(${escHtml(companyHistorySummary(history))})</span>
      </div>
      <ul>${rows}</ul>
      <a class="ch-all" href="${escHtml(companyPageUrl(job.company))}" target="_blank" rel="noopener"
         title="Open in a new tab">Show all jobs at ${escHtml(job.company || '')} ↗</a>
    </div>`;
}

/** Company name (click → filter to that company) + location. */
function cardCompanyHtml(company, location) {
  const name = String(company || '');
  const link = name
    ? `<button type="button" class="company-link" title="Show every Tracker job at this company"
               onclick="event.stopPropagation(); filterByCompany(${escHtml(JSON.stringify(name))})">${escHtml(name)}</button>`
    : '';
  return link + (location ? ' · ' + escHtml(location) : '');
}

/* ── Applicant position at apply time (LinkedIn) ─────────────────── */
function applicantPosPillHtml(job) {
  const pos = job.applicant_position;
  return `<span class="meta-pill applicant-pos-pill" data-applicant-pos-pill
                title="You were roughly applicant #${pos || ''} when you applied"
                ${pos ? '' : 'hidden'}>#${escHtml(pos || '')} at apply</span>`;
}

function renderApplicantPositionBlock(job) {
  if (String(job.source || '').toLowerCase() !== 'linkedin') return '';
  const id = job._id;
  const scraped = job.applicants ? `When scraped: ${job.applicants}. ` : '';
  return `
    <div class="applicant-pos-wrap" data-id="${id}" onclick="event.stopPropagation()">
      <span class="ap-label">Applicant # when I applied</span>
      <input type="number" min="1" step="1" inputmode="numeric" class="applicant-pos-input" aria-label="Applicant number when I applied"
             value="${escHtml(job.applicant_position || '')}" placeholder="e.g. 25"
             onkeydown="if(event.key==='Enter'){event.preventDefault();saveApplicantPosition('${id}');}" />
      <button type="button" class="btn-save" onclick="saveApplicantPosition('${id}', this)">Save</button>
      <span class="ap-hint">${escHtml(scraped)}Read the LinkedIn applicant count right after you apply.</span>
    </div>`;
}

function setApplicantPositionUI(id, value) {
  const card = document.getElementById(`card-${id}`);
  if (!card) return;
  const input = card.querySelector('.applicant-pos-input');
  if (input) input.value = value || '';
  const pill = card.querySelector('[data-applicant-pos-pill]');
  if (pill) {
    pill.hidden = !value;
    pill.textContent = value ? `#${value} at apply` : '';
    pill.title = value ? `You were roughly applicant #${value} when you applied` : '';
  }
}

async function saveApplicantPosition(id, btn) {
  const card = document.getElementById(`card-${id}`);
  const input = card && card.querySelector('.applicant-pos-input');
  if (!input) return;
  const raw = input.value.trim();
  try {
    const res = await fetch(`/api/jobs/${id}/applicant-position`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ applicant_position: raw }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Request failed');
    setApplicantPositionUI(id, data.applicant_position);
    if (btn) {
      btn.textContent = 'Saved ✓';
      setTimeout(() => { btn.textContent = 'Save'; }, 2000);
    }
    showToast(data.applicant_position ? `Saved: applicant #${data.applicant_position}` : 'Applicant # cleared');
  } catch (err) {
    showToast('' + err.message, 'err');
  }
}

/* ── Render card ─────────────────────────────────────────────────── */
function renderCard(job) {
  const score  = formatScoreBadge(job);
  const status = job.status || 'pending';
  const aiHint = needsAiHint(job);
  const doSection = renderBreakdown("What you'll do", job.what_youll_do);
  const lookingSection = renderBreakdown("What they're looking for", job.what_theyre_looking_for);
  const hasBreakdown = Boolean(doSection || lookingSection);
  const matchReasons = hasBreakdown
    ? ''
    : (job.match_reasons || []).slice(0, 3).map(r => `<li>${escHtml(r)}</li>`).join('');
  const highlights = normalizeHighlights(job.highlights);
  const highlightBadges = highlightBadgesHtml(highlights);
  const runAiCls = aiHint ? 'btn-run-ai ai-pending-btn' : 'btn-run-ai';
  const runAiLabel = aiHint ? 'Run AI' : 'Re-run AI';
  const recBadge = recommendedByBadge(job);
  const recKey = String(job.recommended_by || '').toLowerCase();
  const missing = isMissingDetails(job);
  const canFetch = canFetchDetails(job);
  // Every card can be selected for Delete selected; batch Fetch / Run AI
  // only take cards where AI has not run yet (Re-run AI stays per card).
  const fetchable = aiHint;
  const isSelected = selectedFetchIds.has(job._id) || selectedDeleteOnlyIds.has(job._id);
  const fetchBtn = canFetch ? `
        <button type="button" class="btn-fetch-details" data-id="${job._id}"
                onclick="event.stopPropagation(); fetchJobDetails('${job._id}', this)"
                title="Scrape title, company, location, and description (no AI matching)">
          Fetch job details
        </button>` : '';
  const selectBox = `
    <label class="card-select-wrap" onclick="event.stopPropagation()" title="${fetchable ? 'Select for batch Fetch details / Run AI / Delete' : 'Select for batch Delete'}">
      <input type="checkbox" class="card-select" data-id="${job._id}"
             data-missing="${missing ? '1' : '0'}"
             data-fetchable="${fetchable ? '1' : '0'}"
             ${isSelected ? 'checked' : ''}
             ${fetchBatchRunning ? 'disabled' : ''}
             onchange="onCardFetchSelect(this)" />
    </label>`;

  const card = document.createElement('div');
  card.className = 'card'
    + (highlights.length ? ' has-highlights' : '')
    + ' has-fetch-select'
    + (isSelected ? ' fetch-selected' : '');
  card.id = `card-${job._id}`;

  card.innerHTML = `
    ${selectBox}
    <div class="card-row" role="button" tabindex="0" aria-expanded="false"
         aria-controls="body-${job._id}" onclick="toggleCardExpand(this)">
      <span class="row-chevron" aria-hidden="true">▸</span>
      <div class="row-main">
        <div class="row-title-line">
          <span class="row-title">${escHtml(job.title || '(No Title)')}</span>
          <span class="row-highlights" data-row-highlights>${highlightBadges}</span>
        </div>
        ${rowSubHtml(job)}
      </div>
      <div class="row-meta">
        ${channelChipHtml(job)}
        ${statusPillHtml(status, job.application_timeline)}
        <div class="score-badge ${scoreCls(hasAiScore(job) ? job.match_score : null)}">${score}</div>
      </div>
    </div>

    <div class="card-body" id="body-${job._id}" role="region" aria-label="Job details">
     <button type="button" class="panel-close" onclick="event.stopPropagation(); closeCardPanel(this)" aria-label="Close details" title="Close (Esc)">×</button>
     <div class="card-split">
      <div class="card-split-match">
      <div class="card-header">
        <div class="card-title-block" data-id="${job._id}">
          <!-- View mode -->
          <div class="info-view">
            <div class="card-title">
              <span class="card-title-text" tabindex="-1">${escHtml(job.title || '(No Title)')}</span>
              <button class="edit-info-btn" onclick="event.stopPropagation(); openEditInfo(this)" title="Edit title / company / location">Edit</button>
            </div>
            <div class="card-company">${cardCompanyHtml(job.company, job.location)}${jobViaText(job) ? `<span class="card-via">via ${escHtml(jobViaText(job))}</span>` : ''}</div>
          </div>
          <!-- Edit mode -->
          <div class="info-edit">
            <input class="info-input" data-field="title"    value="${escHtml(job.title    || '')}" placeholder="Job Title" aria-label="Job title" />
            <div class="info-edit-row">
              <input class="info-input" data-field="company"  value="${escHtml(job.company  || '')}" placeholder="Company" aria-label="Company" />
              <input class="info-input" data-field="location" value="${escHtml(job.location || '')}" placeholder="Location" aria-label="Location" />
            </div>
            <div class="info-action-row">
              <button class="info-save-btn"   onclick="saveJobInfo(this)">✓ Save</button>
              <button class="info-cancel-btn" onclick="cancelEditInfo(this)">✕ Cancel</button>
            </div>
          </div>
        </div>
        <div class="score-badge ${scoreCls(hasAiScore(job) ? job.match_score : null)}">${score}</div>
      </div>

      <div class="card-meta">
        <span class="body-highlights" data-body-highlights>${highlightBadges}</span>
        ${sourcePillHtml(job)}
        ${originPillHtml(job)}
        ${recBadge}
        ${countryPillHtml(job)}
        ${job.matched_at ? `<span class="meta-pill" title="Matched on">Matched ${job.matched_at}</span>` : ''}
        ${postedPillHtml(job)}
        ${job.applicants ? `<span class="meta-pill" title="Applicant count when the job was scraped">Applicants: ${escHtml(job.applicants)}</span>` : ''}
        ${applicantPosPillHtml(job)}
      </div>

      <div class="progress-panel">
      ${renderProgressBlock(job)}
      ${renderNotesBlock(job)}
      </div>

      <div class="card-actions-row">
        <a class="btn-apply ${isAppliedLike(status, job.application_timeline) ? 'btn-applied' : ''}"
           href="${escHtml(job.link || '#')}" target="_blank" rel="noopener">
          ${isAppliedLike(status, job.application_timeline) ? 'View posting' : 'Apply'}
        </a>
        <button type="button" class="${runAiCls}" data-id="${job._id}"
                onclick="event.stopPropagation(); runAiForJob('${job._id}', this)"
                title="Run AI matching — uses the stored JD when it is complete, otherwise scrapes the job page in Chrome first">
          ${runAiLabel}
        </button>
        ${fetchBtn}
      </div>

      ${renderCompanyHistory(job)}

      ${job.recommend_reason ? `<div class="recommend-reason"><span class="recommend-reason-label">Why AI recommends this</span>${escHtml(job.recommend_reason)}</div>` : ''}
      ${job.summary ? `<div class="card-summary">${escHtml(job.summary)}</div>` : ''}

      <details class="job-description" data-job-id="${job._id}" ontoggle="loadJobDescription(this)">
        <summary>
          <span>Original description</span>
          <button type="button" class="jd-copy-btn" data-job-id="${job._id}"
                  onclick="event.preventDefault(); event.stopPropagation(); copyJobDescription(this)"
                  title="Copy the full original description">Copy</button>
        </summary>
        <div class="job-description-body" data-loaded="0">Expand to load…</div>
      </details>

          ${doSection}
          ${lookingSection}
          ${matchReasons ? `<ul style="font-size:.78rem;color:var(--muted);padding-left:16px;line-height:1.6;">${matchReasons}</ul>` : ''}
      </div>
        <div class="card-split-track">
          ${renderApplicantPositionBlock(job)}
          ${renderApplicationQa(job)}
          ${renderHighlightsBlock(job)}
          <div class="card-danger-row">
            <button type="button" class="delete-job-btn" onclick="event.stopPropagation(); deleteMatchedJob('${job._id}', this)" title="Delete this job permanently">Delete job</button>
          </div>
        </div>
     </div>
    </div>
  `;
  return card;
}

function renderQaItemHtml(item = {}) {
  const q = escHtml(item.question || '');
  const a = escHtml(item.answer || '');
  return `
    <div class="qa-item">
      <div class="qa-item-header">
        <input class="qa-question" type="text" placeholder="Question asked in the application…" aria-label="Question" value="${q}">
        <button type="button" class="qa-remove" title="Remove" aria-label="Remove question" onclick="removeQaItem(this)">×</button>
      </div>
      <textarea class="qa-answer" rows="2" placeholder="Your answer…" aria-label="Answer">${a}</textarea>
    </div>
  `;
}

function renderApplicationQa(job) {
  const items = Array.isArray(job.application_qa) && job.application_qa.length
    ? job.application_qa
    : [{}];
  const listHtml = items.map(renderQaItemHtml).join('');
  return `
    <div class="qa-wrap" data-id="${job._id}">
      <div class="qa-label">Application Q&amp;A</div>
      <div class="qa-list">${listHtml}</div>
      <div class="qa-actions">
        <button type="button" class="btn-add-qa" data-id="${job._id}" onclick="addQaItem(this)">+ Add question</button>
        <button type="button" class="btn-save" data-id="${job._id}" onclick="saveApplicationQa(this)">Save Q&amp;A</button>
      </div>
    </div>
  `;
}

function timelineStepClass(step) {
  if (step === 'applied') return 'step-applied';
  if (step === 'reapplied') return 'step-reapplied';
  if (step === 'rejected') return 'step-rejected';
  if (step === 'offer') return 'step-offer';
  if (String(step).startsWith('interview_')) return 'step-interview';
  return '';
}

function formatNodeTime(at) {
  if (!at) return '—';
  // "YYYY-MM-DD HH:MM" → "MM-DD\nHH:MM"
  const m = String(at).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return escHtml(at);
  return `${m[2]}-${m[3]}<br>${m[4]}:${m[5]}`;
}

function nextTimelineActions(timeline) {
  if (!timeline.length) {
    return [{ step: 'applied', label: '+ Applied', cls: 'btn-apply-step' }];
  }
  const last = timeline[timeline.length - 1].step;
  const reapply = { step: 'reapplied', label: '+ Reapplied', cls: 'btn-apply-step' };
  // Rejected / no reply → apply again (e.g. a repost); the track restarts.
  if (last === 'rejected') return [reapply];
  if (TERMINAL_STEPS.has(last)) return [];

  const actions = [];
  if (last === 'applied' || last === 'reapplied') {
    actions.push({ step: 'interview_invite', label: '+ Invite', cls: 'btn-interview-step' });
    actions.push({ step: 'rejected', label: '+ Rejected', cls: 'btn-reject-step' });
    actions.push(reapply);
    return actions;
  }
  if (last === 'interview_invite') {
    actions.push({ step: 'interview_1', label: '+ Round 1', cls: 'btn-interview-step' });
    actions.push({ step: 'rejected', label: '+ Rejected', cls: 'btn-reject-step' });
    return actions;
  }
  const idx = INTERVIEW_STEPS.indexOf(last);
  if (idx >= 0) {
    if (idx < INTERVIEW_STEPS.length - 1) {
      const next = INTERVIEW_STEPS[idx + 1];
      actions.push({ step: next, label: `+ ${TIMELINE_LABEL[next]}`, cls: 'btn-interview-step' });
    }
    actions.push({ step: 'rejected', label: '+ Rejected', cls: 'btn-reject-step' });
    actions.push({ step: 'offer', label: '+ Offer', cls: 'btn-offer-step' });
  }
  return actions;
}

function statusFromTimeline(timeline) {
  if (!Array.isArray(timeline) || !timeline.length) return '';
  const last = timeline[timeline.length - 1].step;
  if (last === 'applied' || last === 'reapplied') return 'applied';
  if (String(last).startsWith('interview_')) return 'interview';
  if (last === 'rejected') return 'rejected';
  if (last === 'offer') return 'offer';
  return '';
}

function progressStage(status, timeline, appliedAt) {
  if (Array.isArray(timeline) && timeline.length) {
    return statusFromTimeline(timeline) || '';
  }
  if (status === 'pending') return '';
  if (PROGRESS_STAGES.includes(status)) return status;
  if (appliedAt) return 'applied';
  return '';
}

function jobMatchesStatusFilter(job, filter) {
  if (!filter || filter === 'all') return true;
  if (filter === 'active') return !SIDE_STATUSES.includes(job.status || 'pending');
  if (PROGRESS_STAGES.includes(filter)) {
    return progressStage(job.status, job.application_timeline, job.applied_at) === filter;
  }
  return (job.status || 'pending') === filter;
}

/** Card pill: "Reapplied" while the latest progress step is a reapply. */
function cardStatusView(status, timeline) {
  const tl = Array.isArray(timeline) ? timeline : [];
  const last = tl.length ? tl[tl.length - 1].step : '';
  if (status === 'applied' && last === 'reapplied') {
    return { cls: 'reapplied', label: 'Reapplied' };
  }
  return { cls: status, label: STATUS_LABEL[status] || status };
}

function statusPillHtml(status, timeline) {
  const view = cardStatusView(status, timeline);
  return `<span class="status-pill s-${escHtml(view.cls)}" data-status-pill>${escHtml(view.label)}</span>`;
}

function isAppliedLike(status, timeline) {
  if (['applied', 'interview', 'rejected', 'offer'].includes(status)) return true;
  return Array.isArray(timeline) && timeline.length > 0;
}

function renderProgressBlock(job) {
  const id = job._id;
  const timeline = Array.isArray(job.application_timeline) ? job.application_timeline.slice() : [];
  jobTimelines[id] = timeline;
  const status = job.status || 'pending';
  const sideVal = SIDE_STATUSES.includes(status) ? status : '';

  let trackHtml = '';
  if (!timeline.length) {
    trackHtml = `<div class="progress-empty">No application yet — record when you applied below</div>`;
  } else {
    trackHtml = timeline.map((ev, i) => {
      const label = TIMELINE_LABEL[ev.step] || ev.label || ev.step;
      const lineCls = i < timeline.length - 1
        ? `progress-line done${timeline[i + 1].step === 'rejected' ? ' end-rejected' : timeline[i + 1].step === 'offer' ? ' end-offer' : ''}`
        : '';
      return `
        <button type="button" class="progress-node ${timelineStepClass(ev.step)}"
                title="Click to edit time"
                onclick="editTimelineStep('${id}', ${i})">
          <div class="node-label">${escHtml(label)}</div>
          <div class="node-dot"></div>
          <div class="node-time">${formatNodeTime(ev.at)}</div>
        </button>
        ${lineCls ? `<div class="${lineCls}"></div>` : ''}
      `;
    }).join('');
  }

  const actions = nextTimelineActions(timeline);
  const actionBtns = actions.map(a =>
    `<button type="button" class="progress-btn ${a.cls}" onclick="addTimelineStep('${id}', '${a.step}')">${a.label}</button>`
  ).join('');
  const undoBtn = timeline.length
    ? `<button type="button" class="progress-btn btn-undo" onclick="undoTimelineStep('${id}')">↩ Undo</button>`
    : '';

  return `
    <div class="progress-wrap" data-id="${id}" id="progress-${id}" onclick="event.stopPropagation()">
      <div class="progress-title">Application progress</div>
      <div class="progress-track">${trackHtml}</div>
      <div class="progress-actions">
        ${actionBtns}
        ${undoBtn}
      </div>
      <div class="progress-side">
        <label class="progress-side-label" for="side-${id}">Other</label>
        <select class="status-side-select" id="side-${id}" data-id="${id}" onchange="updateSideStatus(this)">
          <option value="" ${!sideVal ? 'selected' : ''}>—</option>
          <option value="unsuitable" ${sideVal==='unsuitable' ? 'selected':''}>Unsuitable</option>
          <option value="closed" ${sideVal==='closed' ? 'selected':''}>Closed (before applying)</option>
          <option value="closed_no_reply" ${sideVal==='closed_no_reply' ? 'selected':''}>Closed after applying, no reply</option>
          <option value="prev_rejected" ${sideVal==='prev_rejected' ? 'selected':''}>Rejected before, skip this time</option>
          <option value="too_many_questions" ${sideVal==='too_many_questions' ? 'selected':''}>Good fit, too many required questions</option>
          <option value="repost" ${sideVal==='repost' ? 'selected':''}>Repost</option>
          <option value="duplicate" ${sideVal==='duplicate' ? 'selected':''}>Duplicate</option>
          <option value="pending">Reset Not Applied</option>
        </select>
      </div>
    </div>
  `;
}

function renderNotesBlock(job) {
  return `
    <div class="notes-wrap" data-id="${job._id}" onclick="event.stopPropagation()">
      <div class="panel-heading">Notes</div>
      <textarea
        class="notes-textarea"
        aria-label="Notes"
        data-id="${job._id}"
        placeholder="Rejected after interview, salary, or why you skipped it…"
        rows="2"
      >${escHtml(job.notes || '')}</textarea>
      <button type="button" class="btn-save" data-id="${job._id}" onclick="saveNotes(this)">Save notes</button>
    </div>`;
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
function consumeOpenJobId() {
  const params = new URLSearchParams(location.search);
  const id = (params.get('open') || '').trim();
  if (!id) return '';
  params.delete('open');
  const qs = params.toString();
  history.replaceState({}, '', qs ? `/?${qs}` : '/');
  return id;
}

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
  if (currentLayout === 'compact') {
    collapseOtherCards(card);
    setCardOpen(card, true);
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
    if (currentLayout === 'compact') injected.classList.add('expanded');

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

/* ── Application timeline ────────────────────────────────────────── */
function nowLocalInputValue() {
  const d = new Date();
  d.setSeconds(0, 0);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toLocalInputValue(at) {
  if (!at) return nowLocalInputValue();
  const m = String(at).match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return nowLocalInputValue();
  return `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}`;
}

function fromLocalInputValue(v) {
  if (!v) return '';
  return v.replace('T', ' ');
}

function askTimelineTime({ title, hint, initial }) {
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = 'tl-modal-backdrop';
    backdrop.innerHTML = `
      <div class="tl-modal" role="dialog" aria-modal="true">
        <h3>${escHtml(title)}</h3>
        <p>${escHtml(hint || 'Pick the date and time for this step')}</p>
        <input type="datetime-local" id="tlDatetimeInput" value="${initial || nowLocalInputValue()}" />
        <div class="tl-modal-actions">
          <button type="button" class="tl-cancel">Cancel</button>
          <button type="button" class="tl-confirm">Save</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);
    const input = backdrop.querySelector('#tlDatetimeInput');
    const close = (val) => { backdrop.remove(); resolve(val); };
    backdrop.querySelector('.tl-cancel').onclick = () => close(null);
    backdrop.querySelector('.tl-confirm').onclick = () => close(fromLocalInputValue(input.value));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(null); });
    input.focus();
  });
}

function askConfirm({ title, hint, detail, confirmLabel = 'Confirm', danger = false }) {
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = 'tl-modal-backdrop';
    backdrop.innerHTML = `
      <div class="tl-modal" role="dialog" aria-modal="true">
        <h3>${escHtml(title)}</h3>
        ${hint ? `<p>${escHtml(hint)}</p>` : ''}
        ${detail ? `<div class="tl-job-name">${escHtml(detail)}</div>` : ''}
        <div class="tl-modal-actions">
          <button type="button" class="tl-cancel">Cancel</button>
          <button type="button" class="tl-confirm${danger ? ' tl-danger' : ''}">${escHtml(confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);
    const close = (val) => { backdrop.remove(); resolve(val); };
    backdrop.querySelector('.tl-cancel').onclick = () => close(false);
    backdrop.querySelector('.tl-confirm').onclick = () => close(true);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(false); });
    backdrop.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') close(false);
    });
    backdrop.querySelector('.tl-confirm').focus();
  });
}

function refreshProgressUI(jobId, timeline, status) {
  jobTimelines[jobId] = timeline;
  const card = document.getElementById(`card-${jobId}`);
  if (!card) return;

  const wrap = card.querySelector(`#progress-${jobId}`);
  if (wrap) {
    const fakeJob = { _id: jobId, application_timeline: timeline, status };
    wrap.outerHTML = renderProgressBlock(fakeJob);
  }
  // Back to Not Applied: the server cleared the applicant # with the record
  if (status === 'pending' && !(timeline || []).length) setApplicantPositionUI(jobId, null);

  const pill = card.querySelector('[data-status-pill]');
  if (pill) {
    const view = cardStatusView(status, timeline);
    pill.className = `status-pill s-${view.cls}`;
    pill.textContent = view.label;
  }

  const btn = card.querySelector('.btn-apply');
  if (btn) {
    const appliedLike = isAppliedLike(status, timeline);
    btn.classList.toggle('btn-applied', appliedLike);
    btn.innerHTML = appliedLike ? 'View posting' : 'Apply';
  }
}

async function saveTimeline(jobId, timeline) {
  const res = await fetch(`/api/jobs/${jobId}/timeline`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ timeline }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Request failed');
  }
  const data = await res.json();
  const saved = data.application_timeline || [];
  if (saved.length < timeline.length) {
    throw new Error('The server did not keep every step — restart the web server and try again');
  }
  refreshProgressUI(jobId, data.application_timeline || timeline, data.status);
  if (!jobMatchesStatusFilter({
    status: data.status,
    application_timeline: data.application_timeline || timeline,
    applied_at: data.applied_at,
  }, currentStatusFilter)) {
    removeMatchedCardSilently(document.getElementById(`card-${jobId}`));
  }
  loadStats();
  loadJourney();
  loadLocationStats();
  loadDailyStats();
  return data;
}

async function addTimelineStep(jobId, step) {
  const label = TIMELINE_LABEL[step] || step;
  const at = await askTimelineTime({
    title: `Record: ${label}`,
    hint: step === 'applied'
      ? 'When did you apply for this job?'
      : step === 'reapplied'
        ? 'When did you apply again (e.g. to the repost)?'
      : step === 'interview_invite'
        ? 'When did you receive the interview invitation?'
        : `When did you get the “${label}” update?`,
    initial: nowLocalInputValue(),
  });
  if (!at) return;

  const timeline = (jobTimelines[jobId] || []).slice();
  timeline.push({ step, at });
  try {
    await saveTimeline(jobId, timeline);
    showToast(`Recorded ${label} · ${at}`);
  } catch (err) {
    showToast('Failed to update progress: ' + (err.message || 'unknown error'), 'err');
  }
}

async function editTimelineStep(jobId, index) {
  const timeline = (jobTimelines[jobId] || []).slice();
  const ev = timeline[index];
  if (!ev) return;
  const label = TIMELINE_LABEL[ev.step] || ev.step;
  const at = await askTimelineTime({
    title: `Edit time: ${label}`,
    hint: 'Update the date and time for this step',
    initial: toLocalInputValue(ev.at),
  });
  if (!at) return;

  timeline[index] = { ...ev, at };
  try {
    await saveTimeline(jobId, timeline);
    showToast(`Updated ${label} · ${at}`);
  } catch (err) {
    showToast('Failed to update time', 'err');
  }
}

async function undoTimelineStep(jobId) {
  const timeline = (jobTimelines[jobId] || []).slice();
  if (!timeline.length) return;
  const wrap = document.getElementById(`progress-${jobId}`);
  const sideVal = wrap && wrap.querySelector('.status-side-select')
    ? wrap.querySelector('.status-side-select').value
    : '';
  // Side-status jobs keep the Applied record; undo later steps only
  if (SIDE_STATUSES.includes(sideVal) && timeline.length === 1 && timeline[0].step === 'applied') {
    showToast('Application record is kept for this status');
    return;
  }
  const removed = timeline.pop();
  try {
    await saveTimeline(jobId, timeline);
    showToast(`↩ Undid ${TIMELINE_LABEL[removed.step] || removed.step}`);
  } catch (err) {
    showToast('Undo failed', 'err');
  }
}

async function updateSideStatus(select) {
  const id = select.dataset.id;
  let status = select.value;
  const currentTimeline = jobTimelines[id] || [];
  if (!status) {
    // "—" restores the status derived from application progress
    status = statusFromTimeline(currentTimeline);
    if (!status) {
      select.value = '';
      return;
    }
  }

  try {
    const res = await fetch(`/api/jobs/${id}/status`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ status }),
    });
    if (!res.ok) throw new Error('Request failed');
    const timeline = status === 'pending' ? [] : currentTimeline;
    refreshProgressUI(id, timeline, status);
    if (!jobMatchesStatusFilter({ status, application_timeline: timeline }, currentStatusFilter)) {
      // Fade this card out in place — no list reload, scroll position stays
      removeMatchedCardSilently(document.getElementById(`card-${id}`));
    }
    showToast(`Status updated to "${STATUS_LABEL[status]}"`);
    loadStats();
    loadJourney();
    loadLocationStats();
    loadDailyStats();
  } catch (err) {
    showToast('Status update failed', 'err');
  }
}

/* ── Fetch job details (scrape only, no AI — agent-recommended cards) ─ */
function onCardFetchSelect(input) {
  const id = input.dataset.id;
  if (!id) return;
  const target = input.dataset.fetchable === '1' ? selectedFetchIds : selectedDeleteOnlyIds;
  if (input.checked) target.add(id);
  else target.delete(id);
  const card = document.getElementById(`card-${id}`);
  if (card) card.classList.toggle('fetch-selected', input.checked);
  updateBatchFetchUI();
}

function updateBatchFetchUI() {
  const meta = document.getElementById('batchFetchMeta');
  const fetchBtn = document.getElementById('fetchSelectedBtn');
  const runAiBtn = document.getElementById('runAiSelectedBtn');
  const deleteBtn = document.getElementById('deleteSelectedBtn');
  const count = selectedFetchIds.size;
  const total = count + selectedDeleteOnlyIds.size;
  const verb = fetchBatchMode === 'run_ai' ? 'running AI…' : 'fetching…';
  if (meta) {
    const label = selectedDeleteOnlyIds.size
      ? `${total} selected (${count} for Fetch / AI)`
      : `${total} selected`;
    meta.textContent = fetchBatchRunning ? `${label} · ${verb}` : label;
  }
  if (fetchBtn) fetchBtn.disabled = fetchBatchRunning || count === 0;
  if (runAiBtn) runAiBtn.disabled = fetchBatchRunning || count === 0;
  if (deleteBtn) deleteBtn.disabled = fetchBatchRunning || total === 0;
  document.querySelectorAll('.batch-fetch-btn').forEach(btn => {
    btn.disabled = fetchBatchRunning;
  });
  document.querySelectorAll('.card-select, .btn-fetch-details, .btn-run-ai').forEach(el => {
    if (el.id === 'fetchSelectedBtn' || el.id === 'runAiSelectedBtn') return;
    el.disabled = fetchBatchRunning;
  });
}

function setSelectMode(on) {
  selectMode = on;
  document.getElementById('jobGrid')?.classList.toggle('select-mode', on);
  document.getElementById('batchFetchBar')?.classList.toggle('select-mode', on);
  const btn = document.getElementById('selectModeBtn');
  if (btn) {
    btn.classList.toggle('active', on);
    btn.textContent = on ? 'Done selecting' : 'Select jobs';
  }
  if (!on) clearFetchSelection();
}

function toggleSelectMode() {
  setSelectMode(!selectMode);
}

function selectAllVisible() {
  document.querySelectorAll('.card-select').forEach(input => {
    input.checked = true;
    onCardFetchSelect(input);
  });
}

function selectVisibleRecommended() {
  setSelectMode(true);
  document.querySelectorAll('.card-select[data-fetchable="1"]').forEach(input => {
    input.checked = true;
    selectedFetchIds.add(input.dataset.id);
    const card = document.getElementById(`card-${input.dataset.id}`);
    if (card) card.classList.add('fetch-selected');
  });
  updateBatchFetchUI();
}

async function selectMissingDetails() {
  await selectMissingAcrossPages({
    cardSelector: '.card-select[data-missing="1"][data-fetchable="1"]',
    param: 'missing',
    noun: 'missing-detail',
    icon: '📥',
  });
}

async function selectMissingAi() {
  await selectMissingAcrossPages({
    cardSelector: '.card-select[data-fetchable="1"]',
    param: 'missing_ai',
    noun: 'missing-AI',
    icon: '🤖',
  });
}

/** Select matching cards on this page, then on every page via /api/fetch-details/ids. */
async function selectMissingAcrossPages({ cardSelector, param, noun, icon }) {
  setSelectMode(true);
  document.querySelectorAll(cardSelector).forEach(input => {
    input.checked = true;
    selectedFetchIds.add(input.dataset.id);
    const card = document.getElementById(`card-${input.dataset.id}`);
    if (card) card.classList.add('fetch-selected');
  });
  updateBatchFetchUI();
  try {
    const params = currentJobListParams();
    params.set(param, '1');
    const res = await fetch('/api/fetch-details/ids?' + params.toString());
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast('' + (data.error || `Could not list ${noun} jobs`), 'err');
      return;
    }
    (data.ids || []).forEach(id => selectedFetchIds.add(id));
    document.querySelectorAll('.card-select').forEach(input => {
      if (selectedFetchIds.has(input.dataset.id)) {
        input.checked = true;
        const card = document.getElementById(`card-${input.dataset.id}`);
        if (card) card.classList.add('fetch-selected');
      }
    });
    updateBatchFetchUI();
    const extra = selectedFetchIds.size - document.querySelectorAll('.card-select:checked').length;
    if (extra > 0) {
      showToast(`${selectedFetchIds.size} ${noun} job(s) selected (${extra} on other pages)`);
    }
  } catch (err) {
    showToast('' + (err.message || `Could not list ${noun} jobs`), 'err');
  }
}

function clearFetchSelection() {
  selectedFetchIds.clear();
  selectedDeleteOnlyIds.clear();
  document.querySelectorAll('.card-select').forEach(input => { input.checked = false; });
  document.querySelectorAll('.card.fetch-selected').forEach(c => c.classList.remove('fetch-selected'));
  updateBatchFetchUI();
}

function hideBatchBanner() {
  const el = document.getElementById('batchFetchBanner');
  if (el) { el.hidden = true; el.innerHTML = ''; }
}

function showBatchBanner(msg, type = 'ok') {
  const el = document.getElementById('batchFetchBanner');
  if (!el) return;
  el.hidden = false;
  el.className = `batch-fetch-banner ${type}`;
  el.innerHTML = `<span>${escHtml(msg)}</span><button type="button" onclick="hideBatchBanner()">Dismiss</button>`;
}

function batchModeLabel(mode) {
  return mode === 'run_ai' ? 'Run AI' : 'Fetch details';
}

function renderBatchProgress(data) {
  const el = document.getElementById('batchFetchProgress');
  if (!el) return;
  const total = data.total || 0;
  const current = data.current || 0;
  const done = data.done_count || 0;
  const fail = data.fail_count || 0;
  const running = Boolean(data.running);
  const mode = data.mode || fetchBatchMode || 'fetch';
  const rows = (data.results || []).map(r => {
    const cls = r.ok ? 'bf-ok' : 'bf-err';
    const mark = r.ok ? '✓' : '✗';
    const score = r.match_score != null && r.match_score !== ''
      ? ` · ${escHtml(String(r.match_score))}/10`
      : '';
    const err = r.error ? ` — ${escHtml(r.error)}` : '';
    const gate = r.ok && r.match_gate && r.match_gate !== 'scored'
      ? ` — stopped at ${escHtml(GATE_LABELS[r.match_gate] || r.match_gate)}, not scored`
      : '';
    const via = r.ok && r.used_stored_jd ? ' · stored JD' : '';
    return `<div class="${cls}">${mark} ${escHtml(r.title || r.id)}${score}${via}${gate}${err}</div>`;
  }).join('');
  const label = batchModeLabel(mode);
  const head = running
    ? `${label} ${Math.min(current, total)}/${total}`
    : `Batch done — ${done} saved, ${fail} failed`;
  el.hidden = false;
  el.innerHTML = `<div class="bf-head"><span>${head}</span><span>${done} ok · ${fail} failed</span></div><div class="bf-list">${rows || 'Starting…'}</div>`;
}

function notifyFetchBatchDone(body, type, mode) {
  showToast(body, type, 8000);
  showBatchBanner(body, type);
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(batchModeLabel(mode || fetchBatchMode), { body });
    }
  } catch (_) {}
}

async function startSelectedChromeBatch(mode) {
  if (fetchBatchRunning || selectedFetchIds.size === 0) return;
  const ids = Array.from(selectedFetchIds);
  const isAi = mode === 'run_ai';
  hideBatchBanner();
  fetchBatchMode = mode;
  fetchBatchRunning = true;
  updateBatchFetchUI();
  showToast(
    isAi
      ? 'Run AI for selected jobs — stored JDs go straight to AI; missing ones are scraped in debug Chrome'
      : 'Fetching details for selected jobs — needs debug Chrome'
  );
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  } catch (_) {}

  const url = isAi ? '/api/run-ai/batch' : '/api/fetch-details/batch';
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      const err = data.error || `Failed to start batch ${batchModeLabel(mode)}`;
      notifyFetchBatchDone(err, 'err', mode);
      fetchBatchRunning = false;
      updateBatchFetchUI();
      return;
    }
    renderBatchProgress({
      running: true,
      mode,
      total: data.total || ids.length,
      current: 0,
      done_count: 0,
      fail_count: 0,
      results: [],
    });
    pollFetchBatchStatus();
  } catch (err) {
    notifyFetchBatchDone(err.message || 'Network error', 'err', mode);
    fetchBatchRunning = false;
    updateBatchFetchUI();
  }
}

async function fetchDetailsForSelected() {
  return startSelectedChromeBatch('fetch');
}

async function runAiForSelected() {
  return startSelectedChromeBatch('run_ai');
}

async function pollFetchBatchStatus() {
  if (fetchBatchPollTimer) clearTimeout(fetchBatchPollTimer);
  try {
    const res = await fetch('/api/fetch-details/batch/status');
    const data = await res.json();
    if (data.mode) fetchBatchMode = data.mode;
    renderBatchProgress(data);
    if (data.running) {
      fetchBatchPollTimer = setTimeout(pollFetchBatchStatus, 1200);
      return;
    }
    fetchBatchRunning = false;
    const done = data.done_count || 0;
    const fail = data.fail_count || 0;
    const skip = data.skip_count || 0;
    const parts = [`${done} updated`];
    if (fail) parts.push(`${fail} failed`);
    if (skip) parts.push(`${skip} skipped`);
    const type = fail || data.error ? 'err' : 'ok';
    const extra = data.error ? ` — ${data.error}` : '';
    const label = batchModeLabel(data.mode || fetchBatchMode);
    notifyFetchBatchDone(`${label} finished — ${parts.join(', ')}${extra}`, type, data.mode);
    selectedFetchIds.clear();
    updateBatchFetchUI();
    await loadJobs();
  } catch (err) {
    fetchBatchPollTimer = setTimeout(pollFetchBatchStatus, 2000);
  }
}

async function fetchJobDetails(jobId, btn) {
  if (!jobId || !btn) return;
  if (fetchBatchRunning) {
    showToast('Wait for the batch Fetch details / Run AI to finish', 'err');
    return;
  }
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Fetching…';
  showToast('Fetching job details — needs debug Chrome');

  try {
    const res = await fetch(`/api/jobs/${jobId}/fetch-details`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      showToast('' + (data.error || 'Failed to fetch job details'), 'err');
      return;
    }
    showToast('Job details updated');
    try {
      const jobRes = await fetch(`/api/jobs/${jobId}`);
      if (jobRes.ok) {
        const job = await jobRes.json();
        const old = document.getElementById(`card-${jobId}`);
        const wasExpanded = old && old.classList.contains('expanded');
        const fresh = renderCard(job);
        if (wasExpanded) fresh.classList.add('expanded');
        if (old) old.replaceWith(fresh);
      }
    } catch (_) {}
  } catch (err) {
    showToast('' + (err.message || 'Network error'), 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

/* ── Run AI (scrape link + match on existing Tracker card) ───────── */
const GATE_LABELS = {
  german: 'German required',
  stack: 'Non-JS backend required',
  leadership: 'Leadership role',
  not_web_dev: 'Not web development',
  backend_heavy: 'Backend-heavy',
  too_many_years: 'Too many years required',
  contract_or_location: 'Contract type / location',
  blocked_company: 'Blocked company',
  empty_desc: 'Empty JD',
  incomplete_desc: 'Unusable JD',
  ai_ml: 'AI/ML role',
};

async function runAiForJob(jobId, btn) {
  if (!jobId || !btn) return;
  if (fetchBatchRunning) {
    showToast('Wait for the batch Fetch details / Run AI to finish', 'err');
    return;
  }
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Running…';

  try {
    const startRes = await fetch(`/api/jobs/${jobId}/run-ai`, { method: 'POST' });
    const startData = await startRes.json().catch(() => ({}));
    if (!startRes.ok) {
      showToast('' + (startData.error || 'Failed to start Run AI'), 'err');
      btn.disabled = false;
      btn.textContent = originalLabel;
      return;
    }
    showToast(startData.uses_stored_jd
      ? 'Run AI started — using the stored JD (no Chrome)'
      : 'Run AI started — scraping the JD, needs debug Chrome');

    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}/run-ai/status`);
        const data = await res.json();
        if (data.running) {
          setTimeout(poll, 1500);
          return;
        }
        if (data.ok) {
          const gate = data.match_gate || '';
          if (gate && gate !== 'scored') {
            showToast(`Hard fail: ${GATE_LABELS[gate] || gate} — see the card for the quoted reason.`, 'err');
          } else {
            const score = data.match_score != null ? ` score ${data.match_score}/10` : '';
            showToast(`AI done${score}`);
          }
          // Refresh this card from API so score / summary update in place
          try {
            const jobRes = await fetch(`/api/jobs/${jobId}`);
            if (jobRes.ok) {
              const job = await jobRes.json();
              const old = document.getElementById(`card-${jobId}`);
              const wasExpanded = old && old.classList.contains('expanded');
              const fresh = renderCard(job);
              if (wasExpanded) fresh.classList.add('expanded');
              if (old) old.replaceWith(fresh);
            } else {
              loadJobs();
            }
          } catch (_) {
            loadJobs();
          }
        } else {
          showToast('Run AI failed: ' + (data.error || 'unknown'), 'err');
          btn.disabled = false;
          btn.textContent = originalLabel;
        }
      } catch (e) {
        showToast('Run AI status error', 'err');
        btn.disabled = false;
        btn.textContent = originalLabel;
      }
    };
    setTimeout(poll, 1200);
  } catch (e) {
    showToast('Network error starting Run AI', 'err');
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

/* ── Save notes ──────────────────────────────────────────────────── */
async function saveNotes(btn) {
  const id    = btn.dataset.id;
  const card  = document.getElementById(`card-${id}`);
  const notes = card.querySelector('.notes-textarea').value;

  try {
    const res = await fetch(`/api/jobs/${id}/notes`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({notes}),
    });
    if (!res.ok) throw new Error('Request failed');
    btn.textContent = 'Saved ✓';
    btn.classList.add('saved');
    setTimeout(() => { btn.textContent = 'Save notes'; btn.classList.remove('saved'); }, 2000);
    showToast('Notes saved');
  } catch (err) {
    showToast('Notes save failed', 'err');
  }
}

/* ── Manual highlights ───────────────────────────────────────────── */
function refreshHighlightsUI(id) {
  const highlights = normalizeHighlights(jobHighlights[id] || []);
  jobHighlights[id] = highlights;
  const card = document.getElementById(`card-${id}`);
  if (!card) return;

  card.classList.toggle('has-highlights', highlights.length > 0);
  const badges = highlightBadgesHtml(highlights);
  card.querySelectorAll('[data-row-highlights], [data-body-highlights]').forEach(el => {
    el.innerHTML = badges;
  });

  const wrap = document.getElementById(`highlights-${id}`);
  if (!wrap) return;
  const chips = wrap.querySelector('[data-chips]');
  if (chips) chips.innerHTML = renderHighlightChips(highlights);
  wrap.querySelectorAll('.hl-preset').forEach(btn => {
    const preset = btn.dataset.preset || '';
    const active = highlights.some(h => h.toLowerCase() === preset.toLowerCase());
    btn.classList.toggle('active', active);
  });
}

async function persistHighlights(id) {
  const highlights = normalizeHighlights(jobHighlights[id] || []);
  const res = await fetch(`/api/jobs/${id}/highlights`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ highlights }),
  });
  if (!res.ok) throw new Error('Request failed');
  const data = await res.json();
  jobHighlights[id] = normalizeHighlights(data.highlights || highlights);
  refreshHighlightsUI(id);
}

async function addHighlightTag(id, tag) {
  const next = String(tag || '').trim().slice(0, 40);
  if (!next) return;
  const current = normalizeHighlights(jobHighlights[id] || []);
  if (current.some(h => h.toLowerCase() === next.toLowerCase())) {
    showToast('Already highlighted', 'err');
    return;
  }
  if (current.length >= 8) {
    showToast('At most 8 highlights', 'err');
    return;
  }
  jobHighlights[id] = [...current, next];
  refreshHighlightsUI(id);
  try {
    await persistHighlights(id);
    showToast(`★ ${next}`);
  } catch (err) {
    jobHighlights[id] = current;
    refreshHighlightsUI(id);
    showToast('Highlight save failed', 'err');
  }
}

function addHighlightPreset(id, tag) {
  addHighlightTag(id, tag);
}

function addHighlightFromInput(id) {
  const wrap = document.getElementById(`highlights-${id}`);
  if (!wrap) return;
  const input = wrap.querySelector('.hl-input');
  const value = (input && input.value) || '';
  if (input) input.value = '';
  addHighlightTag(id, value);
}

async function removeHighlight(btn) {
  const wrap = btn.closest('.highlights-wrap');
  if (!wrap) return;
  const id = wrap.dataset.id;
  const tag = btn.dataset.tag || '';
  const current = normalizeHighlights(jobHighlights[id] || []);
  const next = current.filter(h => h.toLowerCase() !== String(tag).toLowerCase());
  jobHighlights[id] = next;
  refreshHighlightsUI(id);
  try {
    await persistHighlights(id);
    showToast('Highlight removed');
  } catch (err) {
    jobHighlights[id] = current;
    refreshHighlightsUI(id);
    showToast('Highlight remove failed', 'err');
  }
}

/* ── Application Q&A ─────────────────────────────────────────────── */
function addQaItem(btn) {
  const wrap = btn.closest('.qa-wrap');
  const list = wrap.querySelector('.qa-list');
  list.insertAdjacentHTML('beforeend', renderQaItemHtml());
  const last = list.lastElementChild;
  last.querySelector('.qa-question').focus();
}

function removeQaItem(btn) {
  const list = btn.closest('.qa-list');
  const item = btn.closest('.qa-item');
  item.remove();
  if (!list.querySelector('.qa-item')) {
    list.insertAdjacentHTML('beforeend', renderQaItemHtml());
  }
}

async function saveApplicationQa(btn) {
  const id   = btn.dataset.id;
  const wrap = btn.closest('.qa-wrap');
  const application_qa = [...wrap.querySelectorAll('.qa-item')].map(item => ({
    question: item.querySelector('.qa-question').value.trim(),
    answer:   item.querySelector('.qa-answer').value.trim(),
  })).filter(pair => pair.question || pair.answer);

  try {
    const res = await fetch(`/api/jobs/${id}/application-qa`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({application_qa}),
    });
    if (!res.ok) throw new Error('Request failed');
    btn.textContent = 'Saved ✓';
    btn.classList.add('saved');
    setTimeout(() => { btn.textContent = 'Save Q&A'; btn.classList.remove('saved'); }, 2000);
    showToast('Application Q&A saved');
  } catch (err) {
    showToast('Q&A save failed', 'err');
  }
}

/* ── Edit job info (title / company / location) ──────────────────── */
function openEditInfo(btn) {
  const block = btn.closest('.card-title-block');
  block.querySelector('.info-view').style.display = 'none';
  const edit = block.querySelector('.info-edit');
  edit.classList.add('active');
  edit.querySelector('[data-field="title"]').select();
}

async function deleteSelectedJobs() {
  if (fetchBatchRunning) return;
  const ids = [...selectedFetchIds, ...selectedDeleteOnlyIds];
  if (!ids.length) return;

  const ok = await askConfirm({
    title: `Delete ${ids.length} selected job(s)?`,
    hint: 'Removes them from matched_jobs and jobs. They will not come back unless scraped or pushed again.',
    detail: ids.length > 1 ? `${ids.length} jobs` : 'this job',
    confirmLabel: 'Delete',
    danger: true,
  });
  if (!ok) return;

  const btn = document.getElementById('deleteSelectedBtn');
  if (btn) btn.disabled = true;
  let deleted = 0;
  try {
    // Server caps one request at FETCH_DETAILS_BATCH_MAX (80) ids.
    for (let i = 0; i < ids.length; i += 80) {
      const res = await fetch('/api/jobs/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: ids.slice(i, i + 80) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `Delete failed (HTTP ${res.status}) — restart the web server if it was started before this update`);
      }
      deleted += data.deleted || 0;
      (data.ids || []).forEach(id => {
        delete jobHighlights[id];
        delete jobTimelines[id];
      });
    }
    showToast(`${deleted} job(s) deleted`);
  } catch (err) {
    showToast(`${err.message || 'Delete failed'}${deleted ? ` (${deleted} deleted before the error)` : ''}`, 'err');
  }
  clearFetchSelection();
  await loadJobs();
  loadStats();
  loadJourney();
  loadLocationStats();
  loadDailyStats();
}

async function deleteMatchedJob(jobId, btn) {
  const card = btn.closest('.card') || document.getElementById(`card-${jobId}`);
  const title = card?.querySelector('.card-title-text')?.textContent?.trim()
    || card?.querySelector('.row-title')?.textContent?.trim()
    || 'this job';

  const ok = await askConfirm({
    title: 'Delete this job?',
    hint: 'Removes it from matched_jobs and jobs. It will not come back unless scraped again.',
    detail: title,
    confirmLabel: 'Delete',
    danger: true,
  });
  if (!ok) return;

  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || 'Delete failed');
    }
    delete jobHighlights[jobId];
    delete jobTimelines[jobId];
    removeMatchedCardSilently(card);
    // Background stats only — do not reload the job list (no spinner / flash)
    loadStats();
    loadJourney();
    loadLocationStats();
    loadDailyStats();
    showToast('Job deleted');
  } catch (err) {
    if (btn) btn.disabled = false;
    showToast(`${err.message || 'Delete failed'}`, 'err');
  }
}

function removeMatchedCardSilently(card) {
  if (!card) return;

  const group = card.closest('.date-group-cards');
  const header = group?.previousElementSibling?.classList?.contains('date-group-header')
    ? group.previousElementSibling
    : null;

  const finish = () => {
    card.remove();

    // Update date-group count or remove empty group
    if (group) {
      const remaining = group.querySelectorAll('.card').length;
      if (remaining === 0) {
        header?.remove();
        group.remove();
      } else if (header) {
        const countEl = header.querySelector('.dg-count');
        if (countEl) countEl.textContent = `${remaining} jobs`;
      }
    }

    // Bump local total label without refetch
    totalJobs = Math.max(0, totalJobs - 1);
    const label = document.getElementById('countLabel');
    if (label) label.textContent = `Page ${currentPage} of ${totalPages} (${totalJobs} total)`;

    // Empty state if grid has no cards left
    const grid = document.getElementById('jobGrid');
    if (grid && !grid.querySelector('.card')) {
      grid.innerHTML = `
        <div class="empty">
          <h2>No Matched Jobs</h2>
          <p>Try different filters?</p>
        </div>`;
    }
  };

  card.style.transition = 'opacity .18s ease, transform .18s ease';
  card.style.opacity = '0';
  card.style.transform = 'translateY(-4px) scale(0.98)';
  setTimeout(finish, 180);
}

function cancelEditInfo(btn) {
  const block = btn.closest('.card-title-block');
  block.querySelector('.info-view').style.display = '';
  block.querySelector('.info-edit').classList.remove('active');
}

async function saveJobInfo(btn) {
  const block   = btn.closest('.card-title-block');
  const id      = block.dataset.id;
  const inputs  = block.querySelectorAll('.info-input');
  const payload = {};
  inputs.forEach(inp => { payload[inp.dataset.field] = inp.value; });

  btn.disabled    = true;
  btn.textContent = '⏳';

  try {
    const res = await fetch(`/api/jobs/${id}/info`, {
      method:  'PATCH',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Request failed');

    // Update view mode text
    const view = block.querySelector('.info-view');
    const titleSpan = view.querySelector('.card-title-text');
    if (titleSpan) titleSpan.textContent = payload.title || '(No Title)';
    const companyEl = view.querySelector('.card-company');
    const via = companyEl.querySelector('.card-via');
    companyEl.innerHTML = cardCompanyHtml(payload.company, payload.location);
    if (via) companyEl.appendChild(via);

    // Sync compact-row fields
    const card = block.closest('.card');
    if (card) {
      const rowTitle = card.querySelector('.row-title');
      const rowCompany = card.querySelector('.row-company');
      const rowLoc = card.querySelector('.row-loc');
      if (rowTitle) rowTitle.textContent = payload.title || '(No Title)';
      if (rowCompany) rowCompany.textContent = payload.company || '';
      if (rowLoc) {
        // Always rendered in the sub line; empty hides itself (CSS :empty)
        rowLoc.textContent = payload.location || '';
        rowLoc.title = payload.location || '';
      }
    }

    // Switch back to view mode
    view.style.display = '';
    block.querySelector('.info-edit').classList.remove('active');
    showToast('Job info updated');
  } catch (err) {
    showToast('Update failed', 'err');
  } finally {
    btn.disabled    = false;
    btn.textContent = '✓ Save';
  }
}

/* ── Escape HTML helper ──────────────────────────────────────────── */
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
