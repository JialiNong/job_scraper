/* Overview application-flow boards: Sankey + weekly stacked bars.
   Classic script — loadFlowStats stays global so timeline / batch refresh it. */
let _flowCache = null;
let _flowPick = null;
let _flowSticky = false;
let _weeklyPick = null;
let _weeklySticky = false;

function flowFmt(n) {
  return Number(n || 0).toLocaleString('en-US');
}

function flowPct(n, of) {
  if (!of) return '0%';
  const p = (n / of) * 100;
  return `${p > 0 && p < 10 ? p.toFixed(1) : Math.round(p)}%`;
}

function flowNodeMap(nodes) {
  const map = {};
  (nodes || []).forEach(n => { map[n.id] = n; });
  return map;
}

function flowChartHeight() {
  const root = document.getElementById('flowRoot') || document.getElementById('weeklyRoot');
  if (root) {
    const h = parseFloat(getComputedStyle(root).getPropertyValue('--flow-chart-h'));
    if (h) return h;
  }
  return window.matchMedia('(max-width: 760px)').matches ? 220 : 268;
}

function flowMarkRadius() {
  const host = document.querySelector('.weekly-section, .flow-section');
  const n = host && parseFloat(getComputedStyle(host).getPropertyValue('--flow-mark-r'));
  return Number.isFinite(n) && n > 0 ? n : 3;
}

function layoutSankey(sankey, width, height) {
  const nodesIn = sankey.nodes || [];
  const padT = 10;
  const padB = 10;
  const nodeW = 13;
  const nodeGap = 7;
  const hasCol2 = nodesIn.some(n => n.column === 2);
  const padL = 86;
  const padR = hasCol2 ? 142 : 128;
  const innerW = Math.max(40, width - padL - padR);
  const gapX = hasCol2 ? (innerW - nodeW * 3) / 2 : innerW - nodeW * 2;
  const xs = [padL, padL + nodeW + Math.max(28, gapX)];
  if (hasCol2) xs.push(xs[1] + nodeW + Math.max(28, gapX));

  const byCol = [[], [], []];
  nodesIn.forEach(n => {
    if (byCol[n.column]) byCol[n.column].push({ ...n });
  });

  const avail = Math.max(40, height - padT - padB);
  const col0Sum = byCol[0].reduce((s, n) => s + n.value, 0) || 1;
  const col0Gaps = Math.max(0, byCol[0].length - 1) * nodeGap;
  const px = (avail - col0Gaps) / col0Sum;

  function stack(list, x, y0, scale, minH) {
    list.forEach(n => {
      n.w = nodeW;
      n.x = x;
      n.h = n.value ? Math.max(n.value * scale, minH) : 0;
    });
    const gaps = Math.max(0, list.length - 1) * nodeGap;
    const raw = list.reduce((s, n) => s + n.h, 0) + gaps;
    const fit = raw > avail ? avail / raw : 1;
    let y = y0;
    list.forEach(n => {
      n.h *= fit;
      n.y = y;
      y += n.h + nodeGap * fit;
    });
    return list;
  }

  const col0 = stack(byCol[0], xs[0], padT, px, 6);
  const col1 = stack(byCol[1], xs[1], padT, px, 14);

  let col2 = [];
  if (hasCol2 && byCol[2].length) {
    const interview = col1.find(n => n.id === 'interview');
    const gaps = Math.max(0, byCol[2].length - 1) * nodeGap;
    const sum = byCol[2].reduce((s, n) => s + n.value, 0) || 1;
    const minH = 12;
    const heights = byCol[2].map(n => Math.max((n.value / sum) * Math.max(8, (interview ? interview.h : avail) - gaps), minH));
    const totalH = heights.reduce((a, b) => a + b, 0) + gaps;
    let y = interview ? interview.y + (interview.h - totalH) / 2 : padT;
    y = Math.max(padT, y);
    if (y + totalH > height - padB) y = Math.max(padT, height - padB - totalH);
    col2 = byCol[2].map((n, i) => {
      const node = { ...n, x: xs[2], y, w: nodeW, h: heights[i] };
      y += node.h + nodeGap;
      return node;
    });
  }

  const laid = [...col0, ...col1, ...col2];
  const byId = flowNodeMap(laid);
  const outOff = {};
  const inOff = {};
  laid.forEach(n => { outOff[n.id] = 0; inOff[n.id] = 0; });

  const ordered = (sankey.links || []).slice().sort((a, b) => {
    const ta = byId[a.target];
    const tb = byId[b.target];
    const sa = byId[a.source];
    const sb = byId[b.source];
    const dy = (ta ? ta.y : 0) - (tb ? tb.y : 0);
    if (dy) return dy;
    return (sa ? sa.y : 0) - (sb ? sb.y : 0);
  });

  const links = ordered.map(l => {
    const s = byId[l.source];
    const t = byId[l.target];
    if (!s || !t || !s.value || !t.value) return null;
    const sH = (l.value / s.value) * s.h;
    const tH = (l.value / t.value) * t.h;
    const sy = s.y + outOff[s.id];
    const ty = t.y + inOff[t.id];
    outOff[s.id] += sH;
    inOff[t.id] += tH;
    return {
      source: l.source,
      target: l.target,
      value: l.value,
      sx: s.x + s.w,
      sy0: sy,
      sy1: sy + sH,
      tx: t.x,
      ty0: ty,
      ty1: ty + tH,
      tone: t.tone,
    };
  }).filter(Boolean);

  return { nodes: laid, links, byId };
}

function sankeyRibbon(l) {
  const mid = (l.sx + l.tx) / 2;
  return `M${l.sx},${l.sy0} C${mid},${l.sy0} ${mid},${l.ty0} ${l.tx},${l.ty0} L${l.tx},${l.ty1} C${mid},${l.ty1} ${mid},${l.sy1} ${l.sx},${l.sy1} Z`;
}

function setFlowCaption(text) {
  const el = document.getElementById('flowCaption');
  if (el) el.textContent = text;
}

function setWeeklyCaption(text) {
  const el = document.getElementById('weeklyCaption');
  if (el) el.textContent = text;
}

function flowHint() {
  return TOUCH_UI ? 'Tap a band for the count' : 'Hover a band for the count';
}

function weeklyHint() {
  return TOUCH_UI ? 'Tap a week for the split' : 'Hover a week for the split';
}

function describeFlowPick(pick, sankey) {
  const byId = flowNodeMap(sankey.nodes);
  const total = sankey.total || 0;
  if (pick.kind === 'node') {
    const n = byId[pick.id];
    if (!n) return flowHint();
    const of = n.column === 2 ? (byId.interview ? byId.interview.value : total) : total;
    const unit = n.column === 2 ? 'interview' : 'applied';
    return `${n.label} ${flowFmt(n.value)} · ${flowPct(n.value, of)} of ${unit}`;
  }
  const s = byId[pick.source];
  const t = byId[pick.target];
  if (!s || !t) return flowHint();
  return `${flowFmt(pick.value)} ${s.label} → ${t.label} · ${flowPct(pick.value, s.value)}`;
}

function flowPickEquals(a, b) {
  if (!a || !b) return false;
  if (a.kind !== b.kind) return false;
  if (a.kind === 'node') return a.id === b.id;
  return a.source === b.source && a.target === b.target;
}

function applyFlowPick(root, pick, sankey, sticky) {
  if (sticky) _flowSticky = !!pick;
  else if (_flowSticky && pick) return;
  _flowPick = pick;
  const chart = root.querySelector('.flow-chart');
  if (!chart) return;
  chart.classList.toggle('is-picking', !!pick);
  chart.querySelectorAll('.flow-ribbon, .flow-node').forEach(el => el.classList.remove('is-on'));
  if (!pick) {
    _flowSticky = false;
    setFlowCaption(`${flowFmt(sankey.total)} jobs · ${flowHint()}`);
    return;
  }
  if (pick.kind === 'node') {
    chart.querySelectorAll(`[data-node="${pick.id}"]`).forEach(el => el.classList.add('is-on'));
    chart.querySelectorAll(`.flow-ribbon[data-source="${pick.id}"], .flow-ribbon[data-target="${pick.id}"]`)
      .forEach(el => el.classList.add('is-on'));
  } else {
    const rib = chart.querySelector(`.flow-ribbon[data-source="${pick.source}"][data-target="${pick.target}"]`);
    if (rib) rib.classList.add('is-on');
    chart.querySelectorAll(`[data-node="${pick.source}"], [data-node="${pick.target}"]`)
      .forEach(el => el.classList.add('is-on'));
  }
  setFlowCaption(describeFlowPick(pick, sankey));
}

function bindFlowPointer(el, pick, sankey, root) {
  el.addEventListener('mouseenter', () => { if (!TOUCH_UI) applyFlowPick(root, pick, sankey, false); });
  el.addEventListener('focus', () => applyFlowPick(root, pick, sankey, false));
  el.addEventListener('click', (e) => {
    e.stopPropagation();
    if (_flowSticky && flowPickEquals(_flowPick, pick)) {
      applyFlowPick(root, null, sankey, true);
      return;
    }
    applyFlowPick(root, pick, sankey, true);
  });
}

function renderSankey(sankey) {
  const root = document.getElementById('flowRoot');
  if (!root) return;
  const total = sankey.total || 0;
  if (!total) {
    root.innerHTML = '<div class="flow-empty">No applications recorded yet</div>';
    setFlowCaption('Apply to a job to see the flow');
    return;
  }
  const width = root.clientWidth || 0;
  const height = flowChartHeight();
  if (width < 40) return;
  const drawW = Math.max(280, width);

  const layout = layoutSankey(sankey, drawW, height);
  const ribbons = layout.links.map(l => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', sankeyRibbon(l));
    path.setAttribute('class', `flow-ribbon flow-tone-${l.tone}`);
    path.setAttribute('data-source', l.source);
    path.setAttribute('data-target', l.target);
    path.setAttribute('tabindex', '0');
    path.setAttribute('role', 'button');
    path.setAttribute('aria-label', `${l.value} from ${l.source} to ${l.target}`);
    return { el: path, pick: { kind: 'link', source: l.source, target: l.target, value: l.value } };
  });

  const nodeEls = layout.nodes.map(n => {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'flow-node flow-hit');
    g.setAttribute('data-node', n.id);
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'button');
    g.setAttribute('aria-label', `${n.label} ${n.value}`);
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('class', `flow-node-rect flow-tone-${n.tone}`);
    rect.setAttribute('x', n.x);
    rect.setAttribute('y', n.y);
    rect.setAttribute('width', n.w);
    rect.setAttribute('height', n.h);
    g.appendChild(rect);

    const side = (n.column === 0 || n.id === 'interview') ? 'left' : 'right';
    const tx = side === 'left' ? n.x - 8 : n.x + n.w + 8;
    const ty = n.y + n.h / 2;
    const stacked = n.h >= 22;
    const name = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    name.setAttribute('class', 'flow-label');
    name.setAttribute('x', tx);
    name.setAttribute('y', stacked ? ty - 6 : ty + 3);
    name.setAttribute('text-anchor', side === 'left' ? 'end' : 'start');
    name.textContent = stacked ? n.label : `${n.label} · ${flowFmt(n.value)}`;
    g.appendChild(name);
    if (stacked) {
      const count = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      count.setAttribute('class', 'flow-label-count');
      count.setAttribute('x', tx);
      count.setAttribute('y', ty + 8);
      count.setAttribute('text-anchor', side === 'left' ? 'end' : 'start');
      count.textContent = flowFmt(n.value);
      g.appendChild(count);
    }
    return { el: g, pick: { kind: 'node', id: n.id } };
  });

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'flow-chart');
  svg.setAttribute('viewBox', `0 0 ${drawW} ${height}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Application flow');
  ribbons.forEach(r => svg.appendChild(r.el));
  nodeEls.forEach(n => svg.appendChild(n.el));
  root.innerHTML = '';
  root.appendChild(svg);
  ribbons.forEach(r => bindFlowPointer(r.el, r.pick, sankey, root));
  nodeEls.forEach(n => bindFlowPointer(n.el, n.pick, sankey, root));
  svg.addEventListener('mouseleave', () => {
    if (!TOUCH_UI && !_flowSticky) applyFlowPick(root, null, sankey, false);
  });
  applyFlowPick(root, _flowSticky ? _flowPick : null, sankey, _flowSticky);
}

function formatWeekLabel(iso) {
  const m = String(iso || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${Number(m[3])} ${months[Number(m[2]) - 1]}`;
}

function formatWeekRange(iso) {
  const m = String(iso || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const start = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const a = `${start.getDate()} ${months[start.getMonth()]}`;
  const b = `${end.getDate()} ${months[end.getMonth()]}`;
  return `${a}–${b}`;
}

function describeWeeklyPick(week, totals) {
  if (!week) {
    const t = totals || {};
    return `${flowFmt(t.total)} applications · LinkedIn ${flowFmt(t.linkedin)} · Indeed ${flowFmt(t.indeed)} · Other ${flowFmt(t.other)}`;
  }
  return `${formatWeekRange(week.week_start)} · LinkedIn ${flowFmt(week.linkedin)} · Indeed ${flowFmt(week.indeed)} · Other ${flowFmt(week.other)} · ${flowFmt(week.total)} total`;
}

function applyWeeklyPick(root, week, weekly, sticky) {
  if (sticky) _weeklySticky = !!week;
  else if (_weeklySticky && week) return;
  _weeklyPick = week ? week.week_start : null;
  if (!week) _weeklySticky = false;
  const chart = root.querySelector('.weekly-chart');
  if (chart) {
    chart.classList.toggle('is-picking', !!week);
    chart.querySelectorAll('.weekly-bar').forEach(el => {
      el.classList.toggle('is-on', !!(week && el.dataset.week === week.week_start));
    });
  }
  setWeeklyCaption(week ? describeWeeklyPick(week) : `${describeWeeklyPick(null, weekly.totals)} · ${weeklyHint()}`);
}

function renderWeekly(weekly) {
  const root = document.getElementById('weeklyRoot');
  const legend = document.getElementById('weeklyLegend');
  if (!root) return;
  const weeks = weekly.weeks || [];
  const totals = weekly.totals || {};
  if (legend) legend.hidden = !weeks.length;
  if (!weeks.length || !totals.total) {
    root.innerHTML = '<div class="flow-empty">No applications recorded yet</div>';
    setWeeklyCaption('Apply to a job to see weekly volume');
    return;
  }

  const plotH = flowChartHeight();
  const padT = 22;
  const padB = 28;
  const barGap = 4;
  const minBar = 12;
  const avail = Math.max(root.clientWidth || 0, 160);
  const slot = Math.max(minBar + barGap, Math.floor((avail - 8) / Math.max(weeks.length, 1)));
  const barW = Math.max(minBar, Math.min(22, slot - barGap));
  const innerW = Math.max(avail, weeks.length * (barW + barGap) + 8);
  const max = Math.max(1, ...weeks.map(w => w.total || 0));
  const bodyH = plotH - padT - padB;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'weekly-chart');
  svg.setAttribute('viewBox', `0 0 ${innerW} ${plotH}`);
  svg.setAttribute('width', innerW);
  svg.setAttribute('height', plotH);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Weekly applications by platform');

  const markR = flowMarkRadius();
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  svg.appendChild(defs);

  const axis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  axis.setAttribute('class', 'weekly-axis');
  axis.setAttribute('x1', 0);
  axis.setAttribute('x2', innerW);
  axis.setAttribute('y1', padT + bodyH);
  axis.setAttribute('y2', padT + bodyH);
  svg.appendChild(axis);

  let prevMonth = '';
  weeks.forEach((week, i) => {
    const x = 4 + i * (barW + barGap);
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'weekly-bar');
    g.setAttribute('data-week', week.week_start);
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'button');
    g.setAttribute('aria-label', describeWeeklyPick(week));

    const hit = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    hit.setAttribute('class', 'weekly-hit');
    hit.setAttribute('x', x - 1);
    hit.setAttribute('y', padT);
    hit.setAttribute('width', barW + 2);
    hit.setAttribute('height', bodyH);
    hit.setAttribute('fill', 'transparent');
    g.appendChild(hit);

    const segs = [];
    let y = padT + bodyH;
    ['other', 'indeed', 'linkedin'].forEach(key => {
      const v = week[key] || 0;
      if (!v) return;
      const h = (v / max) * bodyH;
      y -= h;
      segs.push({ key, y, h: Math.max(h, 1) });
    });
    const stack = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    if (segs.length) {
      const top = segs[segs.length - 1].y;
      const clipId = `wk-clip-${i}`;
      const clipPath = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
      clipPath.setAttribute('id', clipId);
      const clipRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      clipRect.setAttribute('x', x);
      clipRect.setAttribute('y', top);
      clipRect.setAttribute('width', barW);
      clipRect.setAttribute('height', Math.max(padT + bodyH - top, 1));
      clipRect.setAttribute('rx', markR);
      clipRect.setAttribute('ry', markR);
      clipPath.appendChild(clipRect);
      defs.appendChild(clipPath);
      stack.setAttribute('clip-path', `url(#${clipId})`);
      segs.forEach(seg => {
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('class', `weekly-seg-${seg.key}`);
        rect.setAttribute('x', x);
        rect.setAttribute('y', seg.y);
        rect.setAttribute('width', barW);
        rect.setAttribute('height', seg.h);
        stack.appendChild(rect);
      });
    } else {
      const stub = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      stub.setAttribute('class', 'weekly-seg-other');
      stub.setAttribute('x', x);
      stub.setAttribute('y', padT + bodyH - 2);
      stub.setAttribute('width', barW);
      stub.setAttribute('height', 2);
      stub.setAttribute('rx', markR);
      stub.setAttribute('ry', markR);
      stub.setAttribute('opacity', '0.45');
      stack.appendChild(stub);
    }
    g.appendChild(stack);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('class', 'weekly-week-label');
    label.setAttribute('x', x + barW / 2);
    label.setAttribute('y', plotH - 8);
    label.setAttribute('text-anchor', 'middle');
    if (slot >= 20) {
      label.textContent = String(Number((week.week_start || '').slice(8)));
      g.appendChild(label);
    }

    const month = formatWeekLabel(week.week_start).split(' ')[1] || '';
    if (month && month !== prevMonth) {
      const mlab = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      mlab.setAttribute('class', 'weekly-month-label');
      mlab.setAttribute('x', x);
      mlab.setAttribute('y', 12);
      mlab.textContent = month;
      svg.appendChild(mlab);
      prevMonth = month;
    }

    g.addEventListener('mouseenter', () => { if (!TOUCH_UI) applyWeeklyPick(root, week, weekly, false); });
    g.addEventListener('focus', () => applyWeeklyPick(root, week, weekly, false));
    g.addEventListener('click', (e) => {
      e.stopPropagation();
      if (_weeklySticky && _weeklyPick === week.week_start) {
        applyWeeklyPick(root, null, weekly, true);
        return;
      }
      applyWeeklyPick(root, week, weekly, true);
    });
    svg.appendChild(g);
  });

  const wrap = document.createElement('div');
  wrap.className = 'weekly-scroll';
  wrap.appendChild(svg);
  root.innerHTML = '';
  root.appendChild(wrap);
  svg.addEventListener('mouseleave', () => {
    if (!TOUCH_UI && !_weeklySticky) applyWeeklyPick(root, null, weekly, false);
  });
  const picked = _weeklySticky && _weeklyPick && weeks.find(w => w.week_start === _weeklyPick);
  applyWeeklyPick(root, picked || null, weekly, _weeklySticky);
}

function renderFlowBoards() {
  if (!_flowCache) return;
  renderSankey(_flowCache.sankey || { total: 0, nodes: [], links: [] });
  renderWeekly(_flowCache.weekly || { weeks: [], totals: { total: 0 } });
}

async function loadFlowStats() {
  const flowRoot = document.getElementById('flowRoot');
  const weeklyRoot = document.getElementById('weeklyRoot');
  if (!flowRoot && !weeklyRoot) return;
  try {
    const res = await fetch('/api/stats/flow');
    _flowCache = await res.json();
    renderFlowBoards();
  } catch (_) {
    if (flowRoot) flowRoot.innerHTML = '<div class="flow-empty">Failed to load flow</div>';
    if (weeklyRoot) weeklyRoot.innerHTML = '<div class="flow-empty">Failed to load weekly applications</div>';
  }
}

(() => {
  const observe = (id) => {
    const el = document.getElementById(id);
    if (!el || !window.ResizeObserver) return;
    let lastW = 0, timer = null;
    new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width;
      if (!width) return;
      if (Math.abs(width - lastW) < 2) return;
      lastW = width;
      clearTimeout(timer);
      timer = setTimeout(renderFlowBoards, 80);
    }).observe(el);
  };
  observe('flowRoot');
  observe('weeklyRoot');
  window.addEventListener('resize', () => {
    clearTimeout(renderFlowBoards._t);
    renderFlowBoards._t = setTimeout(renderFlowBoards, 80);
  });
})();
