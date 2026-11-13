/* Tracker card actions: notes, highlights, Q&A, edit info, replace link, delete.
   Classic script — functions stay global. Loaded before index.js. */

function renderReplaceLinkBlock(job) {
  const id = job._id;
  const link = String(job.link || '');
  return `
    <div class="replace-link-wrap" data-id="${id}" onclick="event.stopPropagation()">
      <div class="panel-heading">Replace posting link</div>
      <div class="rl-row">
        <input type="text" class="replace-link-input" inputmode="url" autocomplete="off" spellcheck="false"
               aria-label="Replacement job URL" value="${escHtml(link)}"
               placeholder="https://company.com/jobs/…"
               onkeydown="if(event.key==='Enter'){event.preventDefault();saveReplaceLink(this);}" />
        <button type="button" class="btn-save" onclick="saveReplaceLink(this)">Save</button>
      </div>
      <span class="rl-hint">If the original posting is closed, paste the live company-page URL, save, then Fetch job details.</span>
    </div>`;
}

async function saveReplaceLink(el) {
  const wrap = el && el.closest ? el.closest('.replace-link-wrap') : null;
  if (!wrap) return;
  const id = wrap.dataset.id;
  const input = wrap.querySelector('.replace-link-input');
  const btn = wrap.querySelector('.btn-save');
  const raw = ((input && input.value) || '').trim();
  if (!raw) {
    showToast('Paste a job URL', 'err');
    if (input) input.focus();
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⏳';
  }
  try {
    const res = await fetch(`/api/jobs/${id}/link`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ link: raw }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Request failed');
    if (btn) {
      btn.textContent = 'Saved ✓';
      setTimeout(() => { if (btn.textContent === 'Saved ✓') btn.textContent = 'Save'; }, 2000);
    }
    showToast(data.unchanged
      ? 'Link is already this URL'
      : 'Link updated — Fetch job details to scrape the new page');
    try {
      const jobRes = await fetch(`/api/jobs/${id}`);
      if (jobRes.ok) {
        const job = await jobRes.json();
        const old = document.getElementById(`card-${id}`);
        const wasExpanded = old && old.classList.contains('expanded');
        const fresh = renderCard(job);
        if (wasExpanded) fresh.classList.add('expanded');
        if (old) old.replaceWith(fresh);
      }
    } catch (_) {}
  } catch (err) {
    showToast('' + (err.message || 'Update failed'), 'err');
    if (btn) btn.textContent = 'Save';
  } finally {
    if (btn) btn.disabled = false;
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
    loadOverviewData();
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
      const rowLocs = card.querySelectorAll('.row-loc');
      if (rowTitle) rowTitle.textContent = payload.title || '(No Title)';
      if (rowCompany) rowCompany.textContent = payload.company || '';
      rowLocs.forEach((rowLoc) => {
        rowLoc.textContent = payload.location || '';
        rowLoc.title = payload.location || '';
      });
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
