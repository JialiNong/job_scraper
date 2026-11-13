/* Tracker application timeline: record / edit / undo steps and side status.
   Classic script — functions stay global. Loaded before index.js. */
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
  loadOverviewData();
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
    loadOverviewData();
  } catch (err) {
    showToast('Status update failed', 'err');
  }
}
