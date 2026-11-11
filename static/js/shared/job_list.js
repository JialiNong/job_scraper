/* Shared by Tracker and Unmatched (classic script, not a module).
   Card chrome lives in static/css/shared/job_list.css: score ring, row
   (title / company / location / via), channel chip, breakdown, toast,
   details sheet. Page scripts load after this file. */

const TOUCH_UI = !!window.matchMedia?.('(hover: none)').matches;
const POINT_VERB = TOUCH_UI ? 'tap' : 'hover';
if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-point-verb]').forEach(el => { el.textContent = POINT_VERB; });
}

let currentLayout = (typeof localStorage !== 'undefined' && localStorage.getItem('jobLayout')) || 'compact';

const SOURCE_BADGE = {
  ats:            { label: 'ATS',           title: 'Company careers page (ATS board)' },
  arbeitsagentur: { label: 'Bundesagentur', title: 'Bundesagentur für Arbeit Jobsuche API' },
  arbeitnow:      { label: 'Arbeitnow API', title: 'Arbeitnow job board API' },
  adzuna:         { label: 'Adzuna API',    title: 'Adzuna job search API' },
};
const BOARD_LABEL = { linkedin: 'LinkedIn', indeed: 'Indeed' };
const RECOMMENDED_BY_LABEL = {
  claude: 'Claude',
  cursor: 'Cursor',
  gemini: 'Gemini',
  gpt: 'GPT',
};
const ORIGIN_PILL = {
  pipeline: { label: 'Scraper', title: 'Found by the LinkedIn / Indeed scraper' },
  manual:   { label: 'Log job', title: 'Logged by hand via Log Job' },
};

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showToast(msg, type = 'ok', ms = 2500) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = `toast ${type} show`;
  setTimeout(() => { t.className = 'toast'; }, ms);
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

function groupJobsByDate(jobs) {
  const groups = new Map();
  jobs.forEach(job => {
    const dateStr = (job.matched_at || '').split(' ')[0] || 'Unknown Date';
    if (!groups.has(dateStr)) groups.set(dateStr, []);
    groups.get(dateStr).push(job);
  });
  return [...groups.entries()];
}

function currentDateRange() {
  const presetEl = document.getElementById('timePreset');
  if (!presetEl) return { from: '', to: '' };
  const preset = presetEl.value;
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

function consumeOpenJobId(path = '/') {
  const params = new URLSearchParams(location.search);
  const id = (params.get('open') || '').trim();
  if (!id) return '';
  params.delete('open');
  const qs = params.toString();
  history.replaceState({}, '', qs ? `${path}?${qs}` : path);
  return id;
}

/* ── Score ring ──────────────────────────────────────────────────── */

function scoreCls(score) {
  if (score == null || score === '') return 'score-pending';
  if (score >= 8)  return 'score-hi';
  if (score >= 6)  return 'score-mid';
  return 'score-lo';
}

function hasAiScore(job) {
  return job && job.match_score != null && job.match_score !== '';
}

function formatScoreBadge(job) {
  if (!hasAiScore(job)) return '—';
  const n = Number(job.match_score);
  return n % 1 === 0 ? String(n) : n.toFixed(1);
}

function scoreRingPct(score) {
  const n = Number(score);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n * 10));
}

/** job object (Tracker) or a raw 0–10 number (Unmatched). */
function scoreRingHtml(jobOrScore, { size = '' } = {}) {
  const fromJob = jobOrScore != null && typeof jobOrScore === 'object';
  const has = fromJob ? hasAiScore(jobOrScore) : Number.isFinite(Number(jobOrScore));
  const raw = has ? Number(fromJob ? jobOrScore.match_score : jobOrScore) : null;
  const pct = has ? scoreRingPct(raw) : 0;
  const label = fromJob ? formatScoreBadge(jobOrScore)
    : (!has ? '—' : (raw % 1 === 0 ? String(raw) : raw.toFixed(1)));
  const cls = scoreCls(has ? raw : null);
  const sizeCls = size ? ` ${size}` : '';
  const title = has
    ? (fromJob
      ? `Match score ${label} of 10, from Run AI on this site`
      : `Match score ${label} of 10`)
    : (fromJob ? 'No AI match score yet' : 'No score');
  return `
    <div class="score-ring ${cls}${sizeCls}" role="img" aria-label="${escHtml(title)}" title="${escHtml(title)}">
      <svg viewBox="0 0 36 36" aria-hidden="true">
        <circle class="score-ring-track" cx="18" cy="18" r="15.5" fill="none" pathLength="100" />
        <circle class="score-ring-fill" cx="18" cy="18" r="15.5" fill="none" pathLength="100"
                stroke-linecap="round" stroke-dasharray="${pct} 100" />
      </svg>
      <span class="score-ring-num">${escHtml(label)}</span>
    </div>`;
}

function renderBreakdown(title, section, { autoOpenGaps = false } = {}) {
  if (!section) return '';
  const matched = Array.isArray(section.matched) ? section.matched : [];
  const unmatched = Array.isArray(section.unmatched) ? section.unmatched : [];
  if (!matched.length && !unmatched.length) return '';
  const total = matched.length + unmatched.length;
  const items = [
    ...matched.map(t => `<li class="bd-ok">✓ ${escHtml(t)}</li>`),
    ...unmatched.map(t => `<li class="bd-no">✗ ${escHtml(t)}</li>`),
  ].join('');
  const openAttr = autoOpenGaps && unmatched.length ? ' open' : '';
  return `
    <details class="breakdown"${openAttr}>
      <summary><span>${title}</span><span class="bd-count">${matched.length}/${total} matched</span></summary>
      <ul class="bd-list">${items}</ul>
    </details>`;
}

/* ── Channel / via / applicants ──────────────────────────────────── */

function jobHost(job) {
  try { return new URL(job.link || '').hostname.replace(/^www\./, ''); } catch (e) { return ''; }
}

function jobChannel(job) {
  const rec = RECOMMENDED_BY_LABEL[String(job.recommended_by || '').toLowerCase()];
  if (rec) return { cls: 'ch-ai', label: rec, title: `Recommended by ${rec}` };
  const src = String(job.source || '').toLowerCase();
  const api = SOURCE_BADGE[src];
  if (api) return { cls: `ch-api ch-${src}`, label: api.label.replace(/ API$/, ''), title: api.title };
  if (job.origin === 'manual' || (src === 'manual' && !job.origin)) {
    return { cls: 'ch-manual', label: ORIGIN_PILL.manual.label, title: ORIGIN_PILL.manual.title };
  }
  if (job.origin === 'pipeline' || BOARD_LABEL[src]) {
    return { cls: 'ch-scraper', label: ORIGIN_PILL.pipeline.label, title: ORIGIN_PILL.pipeline.title };
  }
  return src ? { cls: 'ch-other', label: src, title: '' } : null;
}

function channelChipHtml(job) {
  const ch = jobChannel(job);
  if (!ch) return '';
  return `<span class="channel-chip ${escHtml(ch.cls)}" title="${escHtml(ch.title)}">${escHtml(ch.label)}</span>`;
}

function jobViaText(job) {
  const src = String(job.source || '').toLowerCase();
  if (BOARD_LABEL[src]) return BOARD_LABEL[src];
  const ch = jobChannel(job);
  if (ch && (ch.cls === 'ch-ai' || ch.cls === 'ch-manual')) return jobHost(job);
  return '';
}

function rowViaHtml(job, extraClass) {
  const text = jobViaText(job);
  if (!text) return '';
  const cls = extraClass ? `row-via ${extraClass}` : 'row-via';
  return `<span class="${cls}">via ${escHtml(text)}</span>`;
}

function formatApplicantsLabel(raw) {
  const text = String(raw || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  const first = text.match(
    /be among the first\s+([\d,]+)\s+applicants?|sei unter den ersten\s+([\d,]+)|geh[öo]ren sie zu den ersten\s+([\d,]+)/i
  );
  if (first) return `first ${first[1] || first[2] || first[3]}`;
  if (/be an early applicant/i.test(text)) return 'early applicant';
  const over = text.match(/(?:over|über)\s+([\d,]+)\+?\s+(?:applicants?|bewerber(?:innen)?)/i);
  if (over) return `${over[1]}+ applicants`;
  const n = text.match(/([\d,]+)\+?\s+(?:applicants?|bewerber(?:innen)?)/i);
  if (n) return `${n[1]} applicants`;
  if (/applicant|bewerber/i.test(text) && text.length <= 48) return text;
  return '';
}

function rowApplicantsHtml(job) {
  const pos = job.applicant_position;
  const scraped = formatApplicantsLabel(job.applicants);
  const label = pos ? `#${pos} at apply` : scraped;
  const title = pos
    ? `You were roughly applicant #${pos} when you applied`
    : (scraped ? 'Applicant count when the job was scraped' : '');
  return `<span class="row-applicants" data-row-applicants title="${escHtml(title)}"${label ? '' : ' hidden'}>${escHtml(label)}</span>`;
}

function applicantsMetaPillHtml(job) {
  const label = formatApplicantsLabel(job.applicants);
  if (!label) return '';
  return `<span class="meta-pill" title="Applicant count when the job was scraped">${escHtml(label)}</span>`;
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

/* ── Layout / details sheet ────────────────────────────────────────
   Tracker: one open card, scrim on stacked view, focus .card-title-text.
   Unmatched list expands inline — set data-scrim="cards" data-collapse="cards"
   data-focus-title=".card-title" on #jobGrid. */

function jobGridEl() {
  return document.getElementById('jobGrid');
}

function jobListOpt(name, fallback) {
  const v = jobGridEl()?.dataset[name];
  return (v == null || v === '') ? fallback : v;
}

function stackedDetails() {
  return window.matchMedia('(max-width: 1080px)').matches;
}

function ensurePanelScrim() {
  let el = document.getElementById('panelScrim');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'panelScrim';
  el.className = 'panel-scrim';
  el.hidden = true;
  el.setAttribute('role', 'button');
  el.setAttribute('aria-label', 'Close details');
  el.addEventListener('click', () => {
    const open = document.querySelector('#jobGrid .card.expanded');
    if (open) setCardOpen(open, false, { focus: true });
  });
  document.body.appendChild(el);
  return el;
}

function syncPanelScrim() {
  const scrim = ensurePanelScrim();
  const sel = jobListOpt('scrim', 'all') === 'cards'
    ? '#jobGrid.layout-cards .card.expanded'
    : '#jobGrid .card.expanded';
  const open = document.querySelector(sel);
  scrim.hidden = !(open && stackedDetails());
  if (typeof syncOverlayLock === 'function') syncOverlayLock();
}

function setCardOpen(card, open, { focus = false } = {}) {
  card.classList.toggle('expanded', open);
  const row = card.querySelector('.card-row');
  if (row) row.setAttribute('aria-expanded', String(open));
  syncPanelScrim();
  if (!focus) return;
  const titleSel = jobListOpt('focusTitle', '.card-title-text');
  const target = open ? card.querySelector(titleSel) : row;
  target?.focus({ preventScroll: true });
}

function collapseOtherCards(keep) {
  document.querySelectorAll('#jobGrid .card.expanded').forEach(c => {
    if (c !== keep) setCardOpen(c, false);
  });
}

function closeCardPanel(btn) {
  const card = btn.closest('.card');
  if (card) setCardOpen(card, false, { focus: true });
}

function toggleCardExpand(rowEl) {
  const card = rowEl.closest('.card');
  if (!card) return;
  const open = !card.classList.contains('expanded');
  const collapseMode = jobListOpt('collapse', 'always');
  if (collapseMode === 'always' || currentLayout === 'cards') collapseOtherCards(card);
  const focusOnOpen = collapseMode === 'always' || currentLayout === 'cards';
  setCardOpen(card, open, { focus: open && focusOnOpen });
}

function setLayout(mode) {
  currentLayout = mode;
  localStorage.setItem('jobLayout', mode);
  const grid = jobGridEl();
  if (!grid) return;
  grid.classList.toggle('layout-compact', mode === 'compact');
  grid.classList.toggle('layout-cards', mode === 'cards');
  document.querySelectorAll('#layoutToggle button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.layout === mode);
  });
  grid.querySelectorAll('.card.expanded').forEach(c => setCardOpen(c, false));
  syncPanelScrim();
}

if (typeof document !== 'undefined') {
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (document.querySelector('.tl-modal-backdrop')) return;
    if (typeof filterPopOpen === 'function' && filterPopOpen()) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    const open = document.querySelectorAll('#jobGrid .card.expanded');
    if (!open.length) return;
    open.forEach(card => setCardOpen(card, false, { focus: true }));
  });
  window.matchMedia('(max-width: 1080px)').addEventListener('change', syncPanelScrim);
}
