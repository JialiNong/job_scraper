/* Tracker Overview tab (templates/index.html): funnel rail, journey,
   heatmap, and location bubbles. Classic script — functions stay global.
   Loaded before index.js. */
/* Pipeline rail: one bar, to scale, where the full width is every opened job.
   Parts: not in English | in English, not matched | AI matched
   (Applied inside it, Got interview inside Applied, Offer inside interview).
   Volume slices (through Applied) are a share of opened jobs. Got interview
   and Offer are conversion from Applied, not from opened. Offer stays at 0. */
function sizeFunnelRail(funnel) {
  const opened = Number(funnel.title_clicked) || 0;
  const german = Number(funnel.german_filtered) || 0;
  const matched = Number(funnel.ai_matched) || 0;
  const applied = Number(funnel.applied) || 0;
  const appliedSlice = Math.min(applied, matched);
  const interviewed = Math.min(Number(funnel.interviewed) || 0, applied);
  const offer = Math.min(Number(funnel.offer) || 0, applied);
  // Manually logged / API jobs can push "matched" past the English count:
  // clamp so the parts never go negative, and scale to their sum.
  const englishRest = Math.max(opened - german - matched, 0);
  const total = german + englishRest + matched;

  const fmt = (n) => n.toLocaleString('en-US');
  const pctOf = (n, of, unit) => {
    if (!of) return `0% of ${unit}`;
    const p = (n / of) * 100;
    return `${p > 0 && p < 10 ? p.toFixed(1) : Math.round(p)}% of ${unit}`;
  };
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  set('f-clicked', fmt(opened));
  set('f-german', fmt(german));      set('fp-german', pctOf(german, total, 'opened'));
  set('f-english', fmt(englishRest)); set('fp-english', pctOf(englishRest, total, 'opened'));
  set('f-matched', fmt(matched));    set('fp-matched', pctOf(matched, total, 'opened'));
  set('f-applied', fmt(applied));    set('fp-applied', pctOf(applied, total, 'opened'));
  set('f-interview', fmt(interviewed)); set('fp-interview', pctOf(interviewed, applied, 'applied'));
  set('f-offer', fmt(offer));        set('fp-offer', pctOf(offer, applied, 'applied'));

  const bar = document.getElementById('railBar');
  if (!bar) return;
  const share = (n, of) => (of ? `${(n / of) * 100}%` : '0%');
  const seg = (key) => bar.querySelector(`.seg[data-key="${key}"]`);
  seg('german').style.width = share(german, total);
  seg('english').style.width = share(englishRest, total);
  seg('matched').style.width = share(matched, total);
  // Applied is a slice of matched. Interview fills Applied by interviewed/applied.
  // Offer sits inside interview (subset), but its % label is still of Applied.
  seg('applied').style.width = share(appliedSlice, matched);
  seg('interview').style.width = share(interviewed, appliedSlice || applied);
  seg('offer').style.width = share(offer, interviewed);
  const counts = { german, english: englishRest, matched, applied: appliedSlice, interview: interviewed, offer };
  ['german', 'english', 'matched', 'applied', 'interview', 'offer'].forEach(key => {
    seg(key).hidden = counts[key] === 0;
  });
  requestAnimationFrame(placeRailLabels);
}

function appendRailOutside(seg, outside) {
  const label = seg.querySelector(':scope > .seg-label');
  if (!label) return;
  const item = document.createElement('div');
  item.className = `rail-out rail-out-${seg.dataset.key}`;
  const clone = label.cloneNode(true);
  clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
  item.innerHTML = '<span class="sw"></span>';
  item.append(...clone.childNodes);
  outside.appendChild(item);
}

/* A part too narrow for its text shows the text under the bar instead.
   Offer is always listed, even at 0, so the slot is reserved. */
function placeRailLabels() {
  const bar = document.getElementById('railBar');
  const outside = document.getElementById('railOutside');
  if (!bar || !outside) return;
  outside.innerHTML = '';
  bar.querySelectorAll('.seg').forEach(s => s.classList.remove('narrow'));
  const compact = window.matchMedia('(max-width: 760px)').matches;
  bar.querySelectorAll('.seg').forEach(s => {
    if (s.hidden) return;
    const label = s.querySelector(':scope > .seg-label');
    const inner = s.querySelector(':scope > .seg');
    const room = s.clientWidth - (inner && !inner.hidden ? inner.offsetWidth : 0);
    const need = Math.max(...[...label.children].map(c => c.offsetWidth)) + 24;
    if (!compact && need <= room) return;
    s.classList.add('narrow');
    appendRailOutside(s, outside);
  });
  const offerSeg = bar.querySelector('.seg[data-key="offer"]');
  if (offerSeg && offerSeg.hidden) {
    offerSeg.classList.add('narrow');
    appendRailOutside(offerSeg, outside);
  }
}

// Web fonts change text widths once they arrive
document.fonts?.ready.then(() => placeRailLabels());

window.addEventListener('resize', () => {
  clearTimeout(placeRailLabels._t);
  placeRailLabels._t = setTimeout(placeRailLabels, 120);
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
// Phone: single tap shows the day panel; double-tap opens that day's applications.
let calLastTapAt = 0;
let calLastTapDate = '';
const CAL_DOUBLE_TAP_MS = 400;

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
          const statsTitle = `${cell.date}: ${cell.data.title_clicked} clicked, ${cell.data.german_filtered} german, ${cell.data.ai_matched} matched, ${cell.data.applied} applied`;
          btn.addEventListener('mouseenter', () => showCalDetail(cell.date));
          if (cell.data.applied) btn.classList.add('has-applied');
          if (TOUCH_UI) {
            btn.title = cell.data.applied
              ? `${statsTitle} · tap for details, double-tap for applications`
              : `${statsTitle} · tap for details`;
            btn.addEventListener('click', () => {
              showCalDetail(cell.date);
              const now = Date.now();
              if (
                cell.data.applied
                && calLastTapDate === cell.date
                && now - calLastTapAt < CAL_DOUBLE_TAP_MS
              ) {
                calLastTapAt = 0;
                calLastTapDate = '';
                filterByAppliedDay(cell.date);
                return;
              }
              calLastTapAt = now;
              calLastTapDate = cell.date;
            });
          } else {
            btn.title = cell.data.applied
              ? `${statsTitle} · click to open that day's applications`
              : statsTitle;
            btn.addEventListener('click', () => {
              showCalDetail(cell.date);
              if (cell.data.applied) window.open(appliedDayUrl(cell.date), '_blank', 'noopener');
            });
          }
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

