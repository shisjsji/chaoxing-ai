/* 日志面板、章节列表、任务轮询 */

/* ---------- 日志面板 ---------- */
function logLine(item) {
  const div = document.createElement('div');
  const lv = LEVEL_LABEL[item.lv] || '';
  div.textContent = '[' + item.t + ']' + (lv ? ' ' + lv + '：' : ' ') + item.m;
  if (item.lv === 'ERROR') div.className = 'err';
  else if (item.lv === 'WARNING') div.className = 'warn';
  else if (/(完成|成功|正确)/.test(item.m)) div.className = 'ok';
  return div;
}

function trimLines(box) {
  while (box.childElementCount > MAX_LINES) box.removeChild(box.firstChild);
}

function appendLog(item) {
  if (sysOnly && item.ch) return;
  const box = $('logs');
  const lv = LEVEL_LABEL[item.lv] || '';
  const text = '[' + item.t + ']' + (lv ? ' ' + lv + '：' : ' ') + item.m;
  const last = box.lastElementChild;
  if (item.kind === 'progress' && last && last.dataset.kind === 'progress') {
    last.textContent = text;
    return;
  }
  const el = logLine(item);
  if (item.kind === 'progress') el.dataset.kind = 'progress';
  box.appendChild(el);
  trimLines(box);
  box.scrollTop = box.scrollHeight;
}

// 窗口不够宽时日志面板会把中间挤没，所以窄屏默认收起。
// 但只在「变窄」时自动收，用户自己点开的不会被反复抢走。
const LOGS_BREAKPOINT = 1180;
let logsAutoClosed = false;

function autoFitLogs() {
  const app = $('app');
  const narrow = window.innerWidth <= LOGS_BREAKPOINT;
  const collapsed = app.classList.contains('nologs');
  if (narrow && !collapsed) {
    app.classList.add('nologs');
    logsAutoClosed = true;
    syncLogsIcon();
  } else if (!narrow && logsAutoClosed) {
    // 拉宽回去时把因为变窄才收起的重新放出来
    app.classList.remove('nologs');
    logsAutoClosed = false;
    syncLogsIcon();
  }
}

function syncLogsIcon() {
  window.setIcon('ico-logs', $('app').classList.contains('nologs') ? 'PanelRight' : 'PanelLeft');
}
$('btnLogs').onclick = () => {
  $('app').classList.toggle('nologs');
  logsAutoClosed = false;   // 手动操作过就别再自动接管
  syncLogsIcon();
  queueExamSync(120);
};
autoFitLogs();
window.addEventListener('resize', autoFitLogs);
// 设置页里按 Enter 等同于保存（输入框内），符合桌面表单习惯
document.querySelectorAll('#page-settings input').forEach(el => {
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('btnSave').click(); }
  });
});

$('btnLogFeedback').onclick = () => {
  $('feedback-logs').checked = true;
  $('feedback-dialog').showModal();
};

$('btnLogClear').onclick = () => {
  if (!$('logs').childElementCount) return;
  if (!confirm('清空当前日志？清空后无法恢复。')) return;
  $('logs').innerHTML = '';
};
$('btnSysOnly').onclick = () => {
  sysOnly = !sysOnly;
  $('btnSysOnly').textContent = sysOnly ? '显示全部' : '只看系统';
  $('logs').innerHTML = '';
  since = 0;
};

/* ---------- 章节列表 ---------- */
function splitKey(key) {
  const i = key.indexOf(SEP);
  return i < 0 ? {course: '', name: key} : {course: key.slice(0, i), name: key.slice(i + 1)};
}

function ensureChap(key, name, course) {
  if (chapMap.has(key)) {
    const rec = chapMap.get(key);
    if (name) { rec.nameEl.textContent = name; rec.courseEl.textContent = course || ''; }
    if (!rec.row.isConnected) $('chapters').appendChild(rec.row);
    return rec;
  }
  const row = document.createElement('div');
  row.className = 'list-row';
  row.innerHTML =
    '<span class="name"><small class="course"></small><span class="title"></span></span>' +
    '<span class="bar"><i></i></span>' +
    '<span class="meta"></span>';
  const rec = {
    row,
    nameEl: row.querySelector('.title'),
    courseEl: row.querySelector('.course'),
    bar: row.querySelector('.bar i'),
    meta: row.querySelector('.meta'),
    baseOrder: chapSeq++
  };
  rec.nameEl.textContent = name || key;
  rec.courseEl.textContent = (course || '') + (course ? ' / ' : '');
  row.dataset.base = String(rec.baseOrder);
  row.dataset.state = 'pending';
  $('chapters').appendChild(row);
  chapMap.set(key, rec);
  return rec;
}

function ensureList() {
  let box = $('chapters').querySelector('.list');
  if (!box) {
    $('chapters').innerHTML = '';
    box = document.createElement('div');
    box.className = 'list';
    box.style.display = 'flex';
    box.style.flexDirection = 'column';
    $('chapters').appendChild(box);
  }
  return box;
}

function updateChap(c) {
  const key = c.key || c.name;
  ensureList();
  const rec = ensureChap(key, c.name, c.course);
  const state = c.state || 'pending';
  ['done', 'error', 'notopen'].forEach(k => rec.row.classList.toggle(k, state === k));
  const total = c.total || 0, done = c.done || 0;
  let pct = c.pct || 0;
  if (total > 0 && c.video_pct && done < total && (state === 'running' || state === 'confirming')) {
    pct = Math.round(((done + c.video_pct / 100) / total) * 100);
  }
  rec.bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
  let text = STATE_LABEL[state] || state;
  if (total) text += ' ' + done + '/' + total;
  if (c.video_total && state !== 'done' && state !== 'error') text += ' · ' + (c.video_pct || 0) + '%';
  rec.meta.textContent = text;
  // 正在跑的置顶；多个任务同时跑时，最近有动静的排最前
  const active = state === 'running' || state === 'confirming';
  rec.row.dataset.state = state;
  reorderChapters();
}

function reorderChapters() {
  const box = chapters.querySelector('.list');
  if (!box) return;
  const rows = Array.from(box.children);
  const want = rows.slice().sort((a, b) => {
    const aa = a.dataset.state === 'running' || a.dataset.state === 'confirming';
    const ba = b.dataset.state === 'running' || b.dataset.state === 'confirming';
    if (aa !== ba) return aa ? -1 : 1;
    return Number(a.dataset.base || 0) - Number(b.dataset.base || 0);
  });
  // 顺序没变就不动 DOM，避免每秒重排造成闪烁
  if (want.every((r, i) => r === rows[i])) return;
  want.forEach(r => box.appendChild(r));
}

function syncNavStatus() {
  const dot = $('status-dot');
  const navDot = $('nav-dot');
  if (dot && navDot) navDot.className = dot.className;
  const text = $('status-text'), navText = $('nav-status-text');
  if (text && navText) navText.textContent = text.textContent;
  const copy = (from, to) => {
    const a = $(from), b = $(to);
    if (a && b && b.textContent !== a.textContent) b.textContent = a.textContent;
  };
  copy('st-jobs', 'nav-jobs');
  copy('st-running', 'nav-running');
  copy('st-done', 'nav-done');
}

function setStat(id, value) {
  const el = $(id);
  const text = String(value);
  if (el.textContent === text) return;
  // 纯数字滚动一下；像「3 / 12」这种带分隔符的直接写
  if (/^\d+$/.test(text)) window.countTo(el, text);
  else el.textContent = text;
}

function updateStats(chapters) {
  let running = 0, done = 0, error = 0, jobsDone = 0, jobsTotal = 0;
  chapters.forEach(c => {
    if (c.state === 'running' || c.state === 'confirming') running++;
    else if (c.state === 'done') done++;
    else if (c.state === 'error') error++;
    jobsDone += c.done || 0;
    jobsTotal += c.total || 0;
  });
  setStat('st-running', running);
  setStat('st-done', done);
  setStat('st-error', error);
  setStat('st-jobs', jobsDone + ' / ' + jobsTotal);

  if (!chapters.length) {
    const box = $('chapters');
    if (!box.querySelector('.state')) {
      box.innerHTML = '';
      // 行被清掉了，映射表也得清 —— 否则下一次数据回来时
      // ensureChap 还以为是老行，结果永远不再渲染
      chapMap.clear();
      chapSeq = 0;
      box.appendChild(emptyState('还没有任务数据', '到「设置」填好账号和模型，再点右上角「开始运行」。'));
    }
  }
}

function resetView(keepSince) {
  if (!keepSince) since = 0;
  chapMap.clear();
  chapSeq = 0;
  $('chapters').innerHTML = '';
  $('logs').innerHTML = '';
}

/* ---------- 任务轮询 ---------- */
async function poll() {
  try {
    const r = await fetch('/api/logs?since=' + since);
    const d = await r.json();
    (d.chapters || []).forEach(updateChap);
    (d.items || []).forEach(appendLog);
    if (d.items && d.items.length) since = d.items[d.items.length - 1].n;
    updateStats(d.chapters || []);

    const dot = $('status-dot'), txt = $('status-text'), stop = $('btnStop');
    dot.className = 'dot';
    if (d.status === 'running') {
      dot.classList.add('run'); txt.textContent = '运行中';
      stop.disabled = false; stop.title = ''; $('btnStart').disabled = true;
    } else if (d.status === 'finished') {
      txt.textContent = '已完成'; stop.disabled = true; stop.title = '没有正在运行的任务';
      $('btnStart').disabled = false;
    } else if (d.status === 'stopped') {
      txt.textContent = '已停止'; stop.disabled = true; stop.title = '没有正在运行的任务';
      $('btnStart').disabled = false;
    } else if (d.status === 'error') {
      dot.classList.add('err'); txt.textContent = '异常退出 (' + d.exit_code + ')';
      stop.disabled = true; stop.title = '没有正在运行的任务'; $('btnStart').disabled = false;
    } else {
      txt.textContent = '空闲'; stop.disabled = true; stop.title = '没有正在运行的任务';
      $('btnStart').disabled = false;
    }
    window.setIcon('ico-start', d.status === 'running' ? 'Pause' : 'Play');
    syncNavStatus();
    pollFails = 0;
  } catch (e) {
    pollFails++;
    if (pollFails >= 3) {
      $('status-dot').className = 'dot err';
      $('status-text').textContent = '连接中断';
    }
  }
}
