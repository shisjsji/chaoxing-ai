/* 自动更新、快捷键 */

/* ---------- 自动更新 ---------- */
function fillList(el, items) {
  el.innerHTML = '';
  items.forEach(text => {
    const li = document.createElement('li');
    li.textContent = text;
    el.appendChild(li);
  });
}

async function syncUpdateState() {
  try {
    const d = await fetch('/api/update/progress').then(r => r.json());
    if (d.stage && d.stage !== 'idle') {
      renderUpdateProgress(d);
      if (['downloading', 'verifying', 'preparing', 'restarting'].includes(d.stage)) {
        startUpdatePolling();
      }
    }
  } catch (e) {}
}

function showUpdateDialog(d) {
  pendingUpdate = d;
  const beta = !!d.beta;
  $('update-title').textContent = '发现新版本 ' + d.version + (beta ? '（Beta）' : '');
  $('update-sub').textContent = '当前 ' + d.current + ' → ' + d.version
    + (d.notes ? '　' + d.notes : '');

  const list = (d.highlights && d.highlights.length) ? d.highlights.slice() : [d.notes || '细节修复与优化'];
  fillList($('update-list'), list);
  if (beta) {
    // Beta 必须在更新公告里说清楚，不能让人以为是正式版
    const warn = document.createElement('li');
    warn.className = 'upd-beta';
    warn.textContent = '这是 Beta 版，可能有不可控的 bug。正在跑课或考试的话，建议先点「稍后」，等正式版。';
    $('update-list').insertBefore(warn, $('update-list').firstChild);
  }
  if ($('update-dialog').open) return;
  $('update-dialog').showModal();
  syncUpdateState();
}

let updateTimer = null;

let updateStage = 'idle';

function renderUpdateProgress(d) {
  updateStage = d.stage || 'idle';
  const box = $('upd-progress');
  if (box) box.hidden = false;
  const fill = $('upd-bar-fill');
  if (fill) fill.style.width = Math.max(0, Math.min(100, d.percent || 0)) + '%';
  const stageEl = $('upd-stage');
  if (stageEl) {
    stageEl.textContent = d.message || d.stage || '';
    stageEl.classList.toggle('err', d.stage === 'error');
    if (d.stage === 'error' && d.error) stageEl.textContent = '更新失败：' + d.error;
  }

  // 按钮跟着阶段走：下载完不是自动重启，而是换成「立即重启」等用户点
  const go = $('update-go'), later = $('update-later');
  if (!go || !later) return;
  if (d.stage === 'ready') {
    go.disabled = false; go.textContent = '立即重启';
    later.disabled = false; later.textContent = '稍后';
    stopUpdatePolling();
  } else if (d.stage === 'preparing') {
    go.disabled = true; go.textContent = '正在准备…';
    later.disabled = true;
  } else if (d.stage === 'restarting') {
    go.disabled = true; go.textContent = '正在重启…';
    later.disabled = true;
  } else if (d.stage === 'done') {
    go.disabled = true; go.textContent = '已完成';
    later.disabled = true;
  } else if (d.stage === 'error') {
    go.disabled = false; go.textContent = '重试';
    later.disabled = false; later.textContent = '稍后';
  }
}

function stopUpdatePolling() {
  if (updateTimer) { clearInterval(updateTimer); updateTimer = null; }
}

function startUpdatePolling() {
  stopUpdatePolling();
  updateTimer = setInterval(async () => {
    try {
      const d = await fetch('/api/update/progress').then(r => r.json());
      renderUpdateProgress(d);
      // 这几种阶段不会再变化，停掉轮询
      if (['ready', 'error', 'restarting', 'done'].includes(d.stage)) {
        stopUpdatePolling();
      }
    } catch (e) {}
  }, 600);
}

async function applyUpdate(source) {
  if (!pendingUpdate) return;
  const btn = $(source);
  const label = getBtnText(btn);
  btn.disabled = true;
  setBtnText(btn, '下载中…');
  $('update-later').disabled = true;
  renderUpdateProgress({stage: 'downloading', percent: 0, message: '正在连接下载服务器'});
  try {
    await post('/api/update/apply', {
      url: pendingUpdate.url,
      sha256: pendingUpdate.sha256,
      version: pendingUpdate.version
    });
    startUpdatePolling();
  } catch (e) {
    renderUpdateProgress({stage: 'error', percent: 0, error: e.message});
    btn.disabled = false;
    setBtnText(btn, label);
    $('update-later').disabled = false;
  }
}

/* 界面报错必须看得见：以前回调里 catch 完直接吞掉，
   按钮「点不动」却没有任何提示，只能靠猜 */
function reportClientError(text) {
  try { console.error(text); } catch (e) {}
  if (!$('update-dialog') || !$('update-dialog').open) return;
  const box = $('upd-progress'), el = $('upd-stage');
  if (!box || !el) return;
  box.hidden = false;
  el.classList.add('err');
  el.textContent = '界面出错：' + text;
}

window.addEventListener('error', ev => reportClientError(ev.message || '未知错误'));
window.addEventListener('unhandledrejection', ev => {
  const r = ev.reason;
  reportClientError((r && r.message) ? r.message : String(r));
});

async function restartUpdate(source) {
  const btn = $(source);
  const label = btn ? getBtnText(btn) : '';
  if (btn) { btn.disabled = true; setBtnText(btn, '正在准备…'); }
  // 解压这一步在程序还开着的时候做，进度会实时显示，解完才退出
  renderUpdateProgress({stage: 'preparing', percent: 0,
    message: '正在解压更新包，大约十几秒'});
  startUpdatePolling();
  try {
    await post('/api/update/restart', {});
  } catch (e) {
    renderUpdateProgress({stage: 'error', percent: 0, error: e.message});
    if (btn) { btn.disabled = false; setBtnText(btn, label); }
    stopUpdatePolling();
  }
}

async function checkUpdateSilently() {
  try {
    const r = await fetch('/api/update/check');
    const d = await r.json();

    if (d.current) $('ver-current').textContent = d.current;
    if (d.current_changelog && d.current_changelog.length && !$('ver-changelog').children.length) {
      fillList($('ver-changelog'), d.current_changelog);
    }

    if (d.last_failure) {
      $('update-text').textContent = '上次更新没有完成，已保留当前版本';
      $('update-bar').classList.add('warn');
      $('update-bar').hidden = false;
      $('update-now').hidden = true;
      $('update-close').textContent = '知道了';
      return;
    }
    if (!d.ok || !d.has_update) return;

    pendingUpdate = d;
    $('update-text').textContent = '发现新版本 ' + d.version;
    $('update-bar').hidden = false;

    // 每次打开程序都弹一次，让用户第一时间知道有更新
    showUpdateDialog(d);
  } catch (e) {}
}

$('update-close').onclick = () => { $('update-bar').hidden = true; };
$('update-now').onclick = () => applyUpdate('update-now');
$('update-go').onclick = async () => {
  // 不听前端变量：黑屏或初始化未完成时它可能是旧值，
  // 会导致「已经下载好了却又下了一遍」。直接问后端现在到底什么状态。
  let d;
  try {
    d = await fetch('/api/update/progress').then(r => r.json());
  } catch (e) {
    renderUpdateProgress({stage: 'error', percent: 0, error: '读取更新状态失败：' + e.message});
    return;
  }
  renderUpdateProgress(d);
  if (d.stage === 'ready') { restartUpdate('update-go'); return; }
  if (['downloading', 'verifying', 'preparing', 'restarting'].includes(d.stage)) {
    // 已经在跑了，接着显示进度就行，别重复触发
    startUpdatePolling();
    return;
  }
  applyUpdate('update-go');
};
$('update-later').onclick = () => { $('update-dialog').close(); };
$('update-dialog').addEventListener('cancel', () => { $('update-dialog').close(); });
$('btnCheckUpdate').onclick = async () => {
  $('update-info').textContent = '正在检查更新…';
  try {
    const r = await fetch('/api/update/check');
    const d = await r.json();
    if (d.current) $('ver-current').textContent = d.current;
    if (d.current_changelog) fillList($('ver-changelog'), d.current_changelog);
    if (!d.ok) { $('update-info').textContent = d.error || '检查失败'; return; }
    if (!d.has_update) { $('update-info').textContent = '已是最新版本'; return; }
    $('update-info').textContent = '发现新版本 ' + d.version;
    showUpdateDialog(d);
  } catch (e) {
    $('update-info').textContent = '检查失败：' + e.message;
  }
};

/* ---------- 快捷键 ---------- */
document.addEventListener('keydown', e => {
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key === 'Enter') { e.preventDefault(); if (!$('btnStart').disabled) $('btnStart').click(); }
  if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); $('btnSave').click(); }
  if (ctrl && ['1','2','3','4','5'].includes(e.key)) {
    e.preventDefault();
    switchPage(['overview','exams','library','settings','about'][Number(e.key) - 1]);
  }
  if (e.key === 'Escape') $('app').classList.add('nologs');
});
