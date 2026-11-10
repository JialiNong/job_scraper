/* Log Job page (templates/manual_apply.html) script. Classic script, not a module:
   inline onclick="..." handlers in the page call these globals. */
let _pollTimer = null;
let _knownLogLen = 0;
let _submittedStatus = 'pending';
let _submittedLinkOnly = false;
let _lookupTimer = null;
let _lookupSeq = 0;
let _lastLookupKey = '';

function colorLine(line) {
  const l = line.trim();
  if (l.startsWith('✅') || l.includes('Saved to matched_jobs') || l.startsWith('Success')) return `<span class="ok">${esc(line)}</span>`;
  if (l.includes('Already on Tracker') || l.includes('Already in DB') || l.startsWith('⏭️')) return `<span class="info">${esc(line)}</span>`;
  if (l.startsWith('❌') || l.startsWith('✗') || l.includes('failed') || l.includes('Failed')) return `<span class="err">${esc(line)}</span>`;
  if (l.startsWith('===') || l.startsWith('[') && l.includes('/')) return `<span class="head">${esc(line)}</span>`;
  if (l.startsWith('🤖') || l.startsWith('✨') || l.startsWith('→') || l.startsWith('  →')) return `<span class="info">${esc(line)}</span>`;
  return esc(line);
}
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function normalizePastedUrl(raw) {
  let u = String(raw || '').trim();
  if (!u || u.startsWith('#')) return '';
  u = u.replace(/^<+|>+$/g, '').trim();
  if ((u.startsWith('"') && u.endsWith('"')) || (u.startsWith("'") && u.endsWith("'"))) {
    u = u.slice(1, -1).trim();
  }
  u = u.replace(/[.,;:)\]"']+$/g, '');
  if (!u) return '';
  if (u.startsWith('//')) u = 'https:' + u;
  else if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(u)) u = 'https://' + u;
  return u;
}

function parseUrlLines(raw) {
  return String(raw || '')
    .split('\n')
    .map(normalizePastedUrl)
    .filter(u => u && /^https?:\/\//i.test(u));
}

function stageLabel(h) {
  const stage = h.stage || '';
  if (stage === 'tracker' || h.on_tracker || h.collection === 'matched_jobs') {
    return 'Already on Tracker';
  }
  if (stage === 'awaiting_match') {
    return 'Scraped, awaiting AI — not on Tracker (use Scrape & Log to add)';
  }
  if (stage === 'unmatched' || h.collection === 'jobs') {
    const score = h.match_score != null && h.match_score !== ''
      ? ` · score ${Number(h.match_score).toFixed(1)}`
      : '';
    return `Scored Unmatched${score} — not on Tracker (use Scrape & Log to add)`;
  }
  if (stage === 'timeout' || h.collection === 'timeout_jobs') {
    return 'Timeout review — not on Tracker yet';
  }
  return h.collection || 'DB';
}

function renderExistRows(hits) {
  return `<div class="exist-list">${hits.map(h => {
    const title = esc(h.title || h.job_id || 'Job');
    const company = h.company ? ` · ${esc(h.company)}` : '';
    const where = esc(stageLabel(h));
    const open = h.open_path
      ? `<a class="btn-open" href="${esc(h.open_path)}">Open →</a>`
      : '';
    return `<div class="exist-row"><div><div>${title}${company}</div><div class="exist-meta">${where}</div></div>${open}</div>`;
  }).join('')}</div>`;
}

async function lookupUrls(urls, { autoOpen = true } = {}) {
  if (!urls.length) return null;
  const key = urls.join('\n');
  if (key === _lastLookupKey && !autoOpen) return null;
  const seq = ++_lookupSeq;
  setBanner('running', `<div class="spinner"></div> Checking if URL already exists…`);

  try {
    const res = await fetch('/api/manual-apply/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    });
    const data = await res.json();
    if (seq !== _lookupSeq) return null;
    if (!res.ok) {
      setBanner('error', '' + (data.error || 'Lookup failed'));
      return null;
    }

    const results = data.results || [];
    const hits = results.filter(r => r.found);
    const misses = results.filter(r => !r.found);
    const trackerHits = hits.filter(h => h.on_tracker);
    const promotableHits = hits.filter(h => !h.on_tracker);
    _lastLookupKey = key;

    if (!hits.length) {
      setBanner('info', `New URL(s) — not in DB yet. Use <strong>Scrape &amp; Log</strong> or <strong>Save Link Only</strong>.`);
      return { hits, misses, results, trackerHits, promotableHits };
    }

    // Single Tracker URL → open that card immediately (paste only; submit
    // must still run so Applied can update an existing card).
    // Link-only / AI-pending cards stay here so Scrape & Log can refresh them.
    if (autoOpen && urls.length === 1 && trackerHits.length === 1 && misses.length === 0 && promotableHits.length === 0) {
      const hit = trackerHits[0];
      if (hit.needs_scrape || hit.details_missing || hit.ai_pending) {
        const open = hit.open_path
          ? ` <a class="btn-open" href="${esc(hit.open_path)}">Open card →</a>`
          : '';
        setBanner('info',
          `Already on Tracker without details: <strong>${esc(hit.title || 'job')}</strong>` +
          (hit.company ? ` · ${esc(hit.company)}` : '') +
          ` — use <strong>Scrape &amp; Log</strong> to fetch + AI, or Run AI on the card.${open}`);
        return { hits, misses, results, trackerHits, promotableHits };
      }
      setBanner('success',
        `Already in Tracker: <strong>${esc(hit.title || 'job')}</strong>` +
        (hit.company ? ` · ${esc(hit.company)}` : '') +
        ` — opening…`);
      if (hit.open_path) {
        setTimeout(() => { window.location.href = hit.open_path; }, 350);
      }
      return { hits, misses, results, trackerHits, promotableHits };
    }

    const parts = [];
    if (trackerHits.length) {
      parts.push(`${trackerHits.length} already on Tracker`);
    }
    if (promotableHits.length) {
      parts.push(
        `${promotableHits.length} already scraped but <strong>not on Tracker</strong> — use Scrape &amp; Log or Save Link Only to add`
      );
    }
    if (misses.length) {
      parts.push(`${misses.length} new`);
    }
    setBanner('info', `${parts.join('. ')}.` + renderExistRows(hits));
    return { hits, misses, results, trackerHits, promotableHits };
  } catch (e) {
    if (seq !== _lookupSeq) return null;
    setBanner('error', 'Lookup error: ' + e.message);
    return null;
  }
}

function scheduleLookup(autoOpen = false) {
  clearTimeout(_lookupTimer);
  _lookupTimer = setTimeout(() => {
    const urls = parseUrlLines(document.getElementById('urlBox').value);
    if (!urls.length) {
      const banner = document.getElementById('statusBanner');
      if (banner.classList.contains('info') || banner.classList.contains('running')) {
        banner.className = 'status-banner';
        banner.innerHTML = '';
      }
      return;
    }
    lookupUrls(urls, { autoOpen });
  }, autoOpen ? 80 : 450);
}

function onStatusChange() {
  const status = document.getElementById('jobStatus').value;
  const dateGroup = document.getElementById('appliedDateGroup');
  if (status === 'applied') {
    dateGroup.classList.add('visible');
  } else {
    dateGroup.classList.remove('visible');
  }
}

function todayIsoDate() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm   = String(d.getMonth() + 1).padStart(2, '0');
  const dd   = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function clearForm() {
  clearTimeout(_lookupTimer);
  _lookupTimer = null;
  _lookupSeq += 1;
  _lastLookupKey = '';
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }

  document.getElementById('urlBox').value = '';
  document.getElementById('jobTitle').value = '';
  document.getElementById('jobCompany').value = '';
  document.getElementById('jobLocation').value = '';
  document.getElementById('recommendedBy').value = '';
  document.getElementById('jobStatus').value = 'pending';
  document.getElementById('appliedDate').value = todayIsoDate();
  onStatusChange();

  const banner = document.getElementById('statusBanner');
  banner.className = 'status-banner';
  banner.innerHTML = '';

  document.getElementById('logWrap').classList.remove('visible');
  document.getElementById('logBody').innerHTML = '';
  document.getElementById('logCounter').textContent = '';
  _knownLogLen = 0;
  _submittedStatus = 'pending';
  _submittedLinkOnly = false;

  document.getElementById('submitBtn').disabled = false;
  document.getElementById('linkOnlyBtn').disabled = false;
}

// Set today's date as default on page load (used only when Applied is selected)
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('appliedDate').value = todayIsoDate();
  onStatusChange();

  const box = document.getElementById('urlBox');
  box.addEventListener('paste', () => scheduleLookup(true));
  box.addEventListener('input', () => scheduleLookup(false));
});

async function submitUrls({ linkOnly = false } = {}) {
  const raw = document.getElementById('urlBox').value.trim();
  if (!raw) { alert('Please enter at least one URL.'); return; }

  const urls = parseUrlLines(raw);

  if (urls.length === 0) { alert('No valid URLs found.'); return; }

  // Do not auto-open on submit — Applied must still update an existing card.
  const looked = await lookupUrls(urls, { autoOpen: false });
  if (looked) {
    const statusNow = document.getElementById('jobStatus').value || 'pending';
    const trackerToApply = statusNow === 'applied'
      ? (looked.trackerHits || []).map(h => h.url)
      : [];
    const trackerToRefresh = !linkOnly
      ? (looked.trackerHits || [])
          .filter(h => h.needs_scrape || h.details_missing || h.ai_pending)
          .map(h => h.url)
      : [];
    const actionable = [...new Set([
      ...looked.misses.map(m => m.url),
      ...(looked.promotableHits || []).map(h => h.url),
      ...trackerToApply,
      ...trackerToRefresh,
    ])];
    if (!actionable.length) {
      // All already on Tracker and not marking applied
      return;
    }
    urls.length = 0;
    actionable.forEach(u => urls.push(u));
  }

  const status = document.getElementById('jobStatus').value || 'pending';
  const appliedDate = status === 'applied'
    ? (document.getElementById('appliedDate').value || '')
    : '';
  const recommendedBy = document.getElementById('recommendedBy').value || '';
  const title = (document.getElementById('jobTitle').value || '').trim();
  const company = (document.getElementById('jobCompany').value || '').trim();
  const location = (document.getElementById('jobLocation').value || '').trim();
  _submittedStatus = status;
  _submittedLinkOnly = linkOnly;

  const statusLabel = status === 'applied' ? 'Applied' : 'Not Applied';
  const actionLabel = linkOnly ? 'Saving link(s)' : 'Scraping';
  setBanner('running', `<div class="spinner"></div> ${actionLabel} ${urls.length} URL(s) as ${statusLabel}…`);
  document.getElementById('submitBtn').disabled = true;
  document.getElementById('linkOnlyBtn').disabled = true;
  document.getElementById('logWrap').classList.add('visible');
  document.getElementById('logBody').innerHTML = '';
  _knownLogLen = 0;

  try {
    const res = await fetch('/api/manual-apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        urls,
        status,
        applied_date: appliedDate,
        mode: linkOnly ? 'link_only' : 'scrape',
        recommended_by: recommendedBy,
        title,
        company,
        location,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      setBanner('error', '' + (data.error || 'Failed to start'));
      document.getElementById('submitBtn').disabled = false;
      document.getElementById('linkOnlyBtn').disabled = false;
      return;
    }
    // Start polling
    _pollTimer = setInterval(pollStatus, 1500);
  } catch(e) {
    setBanner('error', 'Network error: ' + e.message);
    document.getElementById('submitBtn').disabled = false;
    document.getElementById('linkOnlyBtn').disabled = false;
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/manual-apply/status');
    const data = await res.json();

    // Append new log lines
    const logBody = document.getElementById('logBody');
    const lines = data.log || [];
    if (lines.length > _knownLogLen) {
      const newLines = lines.slice(_knownLogLen);
      newLines.forEach(line => {
        const div = document.createElement('div');
        div.innerHTML = colorLine(line);
        logBody.appendChild(div);
      });
      _knownLogLen = lines.length;
      logBody.scrollTop = logBody.scrollHeight;
    }

    // Update counter
    const skipCount = data.skip_count || 0;
    document.getElementById('logCounter').textContent =
      `${data.done_count} saved, ${skipCount} skipped, ${data.fail_count} failed`;

    if (!data.running) {
      clearInterval(_pollTimer);
      document.getElementById('submitBtn').disabled = false;
      document.getElementById('linkOnlyBtn').disabled = false;
      const statusLabel = _submittedStatus === 'applied' ? 'Applied' : 'Not Applied';
      const modeHint = _submittedLinkOnly ? ' (link-only, AI pending)' : '';
      const logText = (data.log || []).join('\n');
      const hasFatal = /Fatal error|Cannot open Chrome/.test(logText);
      const savedPartial = (data.log || []).some(l =>
        /saving with URL|saving link only|AI pending|No user profile|Cannot open Chrome/i.test(l)
      );
      const parts = [];
      if (data.done_count > 0) parts.push(`${data.done_count} saved as ${statusLabel}${modeHint}`);
      if (skipCount > 0) parts.push(`${skipCount} already on Tracker (skipped)`);
      if (data.fail_count > 0) parts.push(`${data.fail_count} failed`);
      if (data.done_count === 0 && (data.fail_count > 0 || hasFatal) && skipCount === 0) {
        const fatalLine = (data.log || []).find(l => /Fatal error|Cannot open Chrome/.test(l));
        setBanner('error', `${fatalLine || `All ${data.fail_count || 1} URL(s) failed. Check the log above.`}`);
      } else if (data.done_count === 0 && skipCount > 0 && data.fail_count === 0) {
        setBanner('error', `All ${skipCount} URL(s) already on Tracker — nothing saved.`);
      } else if (data.done_count === 0 && !parts.length) {
        setBanner('error', `Nothing was saved. Check the log above — scrape/AI may have aborted.`);
      } else if (parts.length && savedPartial && data.fail_count === 0) {
        const link = ` <a href="/" style="color:var(--accent)">View Matched →</a>`;
        setBanner('info', `Done — ${parts.join(', ')}. Details were incomplete (link saved; use Run AI later).${link}`);
      } else if (parts.length) {
        const link = data.done_count > 0
          ? ` <a href="/" style="color:var(--accent)">View Matched →</a>`
          : '';
        setBanner('success', `Done — ${parts.join(', ')}.${link}`);
      }
    }
  } catch(e) {
    // ignore transient network errors
  }
}

function setBanner(type, html) {
  const el = document.getElementById('statusBanner');
  el.className = 'status-banner ' + type;
  el.innerHTML = html;
}
