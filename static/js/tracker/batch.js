/* Tracker batch bar: select jobs, Fetch details, Run AI, Delete selected.
   Classic script — functions stay global. Loaded before index.js. */
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
