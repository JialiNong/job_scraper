/* Timeouts page (templates/timeouts.html) script. Classic script, not a module:
   inline onclick="..." handlers in the page call these globals. */
function esc(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show';
  setTimeout(() => { t.className = 'toast'; }, 2200);
}
async function loadJobs() {
  const source = document.getElementById('sourceFilter').value;
  const status = document.getElementById('statusFilter').value;
  const params = new URLSearchParams({ source, review_status: status });
  const res = await fetch('/api/timeout-jobs?' + params.toString());
  const data = await res.json();
  document.getElementById('countLabel').textContent = `${data.total} ${data.total === 1 ? 'job' : 'jobs'}`;
  const list = document.getElementById('list');
  if (!data.jobs.length) {
    list.innerHTML = '<div class="empty">No timeout jobs in this filter. New ones appear after the next scrape.</div>';
    return;
  }
  list.innerHTML = data.jobs.map(job => `
    <div class="card" data-id="${esc(job._id)}">
      <div>
        <h3>${esc(job.title)}</h3>
        <div class="meta">
          <span class="pill">${esc((job.source || '').toUpperCase())}</span>
          ${job.company ? `<span>${esc(job.company)}</span>` : ''}
          ${job.location ? `<span>${esc(job.location)}</span>` : ''}
          ${job.keyword ? `<span>q=${esc(job.keyword)}</span>` : ''}
          ${job.created_at ? `<span>${esc(job.created_at)}</span>` : ''}
          <span>Timed out ${esc(job.timeout_count)}×</span>
        </div>
      </div>
      <div class="actions">
        <a class="btn btn-ok" href="${esc(job.link || '#')}" target="_blank" rel="noopener">Open job</a>
        ${job.review_status === 'open' ? `
          <button class="btn" onclick="addMatch('${esc(job._id)}')">Add as Not Applied</button>
          <button class="btn btn-mute" onclick="dismiss('${esc(job._id)}')">Dismiss</button>
        ` : `<span class="pill">${esc(job.review_status)}</span>`}
      </div>
    </div>
  `).join('');
}
async function addMatch(id) {
  const res = await fetch(`/api/timeout-jobs/${id}/match`, { method: 'POST' });
  const data = await res.json();
  if (!res.ok) { toast(data.error || 'Failed'); return; }
  toast(data.already ? 'Already on Tracker' : 'Added to Tracker as Not Applied');
  loadJobs();
}
async function dismiss(id) {
  const res = await fetch(`/api/timeout-jobs/${id}/dismiss`, { method: 'POST' });
  if (!res.ok) { toast('Failed'); return; }
  toast('Dismissed');
  loadJobs();
}
loadJobs();
