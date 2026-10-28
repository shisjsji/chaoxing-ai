/* 通用工具、图标挂载、高级设置抽屉 */

/* ---------- 通用 ---------- */
function setMsg(text, kind) {
  const el = $('msg');
  el.textContent = text || '';
  el.className = kind || '';
}

function emptyState(text, hint, retryFn) {
  const box = document.createElement('div');
  box.className = 'state';
  box.textContent = text;
  if (hint) {
    const h = document.createElement('span');
    h.className = 'hint';
    h.textContent = hint;
    box.appendChild(h);
  }
  if (retryFn) {
    const b = document.createElement('button');
    b.className = 'btn retry';
    b.textContent = '重试';
    b.onclick = retryFn;
    box.appendChild(b);
  }
  return box;
}

function errorState(text, retryFn) {
  const box = emptyState(text, '', retryFn);
  box.className = 'state error';
  return box;
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

function courseListForSave(checked, ids) {
  // 课程列表还没渲染时（没点「获取课程」），勾选状态无从得知，
  // 这时沿用手上已有的课程，别把用户选好的课悄悄清空。
  const rendered = document.querySelectorAll('#courses input').length > 0;
  const base = rendered ? checked : savedCourseIds;
  return [...new Set([...base, ...ids])];
}

function formData() {
  const ids = $('manual_ids').value.split(/[,，\s]+/).filter(Boolean);
  const checked = [...document.querySelectorAll('#courses input:checked')].map(x => x.value);
  return {
    username: $('username').value.trim(),
    password: $('password').value,
    api_key: $('api_key').value.trim(),
    endpoint: $('endpoint').value.trim(),
    model: $('model').value,
    vision_model: $('vision_model').value,
    reasoning_effort: $('reasoning_effort').value,
    speed: parseFloat($('speed').value) || 1,
    jobs: parseInt($('jobs').value, 10) || 2,
    min_interval: parseInt($('min_interval').value, 10) || 0,
    notopen_action: $('notopen_action').value,
    submit: $('submit').checked,
    work_redo_enabled: $('work_redo_enabled').checked,
    target_accuracy: parseInt($('target_accuracy').value, 10),
    exam_answer_gap: parseFloat($('exam_answer_gap').value) || 0,
    exam_min_minutes: parseFloat($('exam_min_minutes').value) || 0,
    exam_hold_seconds: parseFloat($('exam_hold_seconds').value) || 0,
    search_url: $('search_url').value.trim(),
    search_key: $('search_key').value.trim(),
    search_enabled: $('search_enabled').checked,
    agent_url: $('agent_url').value.trim(),
    agent_model: $('agent_model').value.trim(),
    agent_key: $('agent_key').value.trim(),
    course_list: courseListForSave(checked, ids)
  };
}


/* ---------- 操作日志：每次点击都进日志面板 ---------- */
let lastClickLabel = '';
let lastClickAt = 0;

function currentPageLabel() {
  const active = document.querySelector('.nav-item[aria-current="page"]');
  return active ? (active.textContent || '').trim() : '';
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('button, a, .nav-item, [data-log]');
  if (!el) return;
  // 只取 id、页面名或按钮文字；输入框里的内容一律不碰
  let label = el.dataset.log || (el.textContent || '').trim() || el.id || el.dataset.page;
  label = String(label).replace(/\s+/g, ' ').slice(0, 40);
  if (!label) return;
  const now = Date.now();
  if (label === lastClickLabel && now - lastClickAt < 400) return;
  lastClickLabel = label;
  lastClickAt = now;
  fetch('/api/client-log', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: label, page: currentPageLabel()})
  }).catch(() => {});
}, true);

/* ---------- 图标：交给 morphicons 做形变 ---------- */
const ICON_SIZE = 19;

window.setIcon = function (id, name) {
  const icons = window.MORPH_ICONS;
  if (!icons || !icons[name]) return;
  const el = document.getElementById(id);
  if (!el || el.tagName.toLowerCase() !== 'morph-icon') return;
  // 状态轮询每秒一次，形状没变就别重新触发形变，否则动画一直被重置
  if (el.dataset.morph === name) return;
  el.dataset.morph = name;
  try { el.morphTo(icons[name], 'snappy'); } catch (e) {}
};

/* 由 /static/app-icons.js（ESM）在模块加载完后调用 */
window.initIcons = function () {
  const icons = window.MORPH_ICONS;
  if (!icons || !window.morphIcon) return;
  document.querySelectorAll('.ni[data-icon]').forEach(slot => {
    const icon = icons[slot.dataset.icon];
    if (!icon) return;
    const el = window.morphIcon(icon, { size: ICON_SIZE });
    if (slot.id) el.id = slot.id;
    el.dataset.morph = slot.dataset.icon;
    el.setAttribute('aria-hidden', 'true');
    slot.replaceWith(el);
  });
  syncLogsIcon();
  window.syncStartIcon();
  // 图标挂完之后把当前页的那一项换成选中态形状
  const current = document.querySelector('.nav-item[aria-current="page"]');
  if (current && window.morphNavIcons) window.morphNavIcons(current.dataset.page);
};

window.syncStartIcon = function () {
  const cls = document.getElementById('status-dot');
  const running = cls && cls.classList.contains('run');
  window.setIcon('ico-start', running ? 'Pause' : 'Play');
};

/* ---------- 按钮里带图标之后，改文字不能再整块覆盖 ---------- */
/* 图标是元素节点，文字是文本节点。直接写 textContent 会把图标一起抹掉，
   所以这里只改文本节点。 */
function btnTextNode(btn) {
  if (!btn) return null;
  const span = btn.querySelector('.btn-label');
  if (span) return span;
  for (let i = btn.childNodes.length - 1; i >= 0; i--) {
    const node = btn.childNodes[i];
    if (node.nodeType === 3 && node.textContent.trim()) return node;
  }
  return null;
}

function setBtnText(btn, text) {
  if (!btn) return;
  const target = btnTextNode(btn);
  if (target) target.textContent = text;
  else btn.textContent = text;
}

function getBtnText(btn) {
  const target = btnTextNode(btn);
  return target ? target.textContent.trim() : '';
}

/* ---------- 通用操作反馈：点下去转圈，完事对勾或叉 ---------- */
const FLASH_ICON = {loader: 'Loader', success: 'Success', failed: 'Failed', warn: 'Warn'};

function iconElOf(target) {
  let el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return null;
  if (el.tagName && el.tagName.toLowerCase() === 'morph-icon') return el;
  return el.querySelector('morph-icon');
}

window.flashIcon = function (target, state, revertMs) {
  const icons = window.MORPH_ICONS;
  if (!icons) return;
  const el = iconElOf(target);
  if (!el) return;
  const name = FLASH_ICON[state] || state;
  if (!icons[name]) return;
  // 首次进来时记下它本来该是什么形状，之后只往这个形状回
  if (!el.dataset.rest) el.dataset.rest = el.dataset.morph || 'Check';
  el.dataset.morph = name;
  try { el.morphTo(icons[name], 'snappy'); } catch (e) {}
  if (revertMs === 0) return;
  setTimeout(() => {
    if (el.dataset.morph !== name) return;   // 期间被别的状态接走了
    const restore = el.dataset.rest;
    el.dataset.morph = restore;
    try { el.morphTo(icons[restore], 'snappy'); } catch (e) {}
  }, revertMs || 1500);
};

window.actWithFeedback = async function (target, fn) {
  window.flashIcon(target, 'loader', 0);
  try {
    const result = await fn();
    window.flashIcon(target, 'success');
    return result;
  } catch (err) {
    window.flashIcon(target, 'failed');
    throw err;
  }
};

/* 运行时生成的按钮也要带图标。initIcons 只在启动时跑一次，
   那之后新建的元素得自己挂。 */
window.iconButton = function (text, icon, cls, size) {
  const btn = document.createElement('button');
  btn.className = cls || 'btn sm';
  const icons = window.MORPH_ICONS;
  if (icons && icons[icon] && window.morphIcon) {
    const el = window.morphIcon(icons[icon], { size: size || 14 });
    el.dataset.morph = icon;
    el.setAttribute('aria-hidden', 'true');
    btn.appendChild(el);
  }
  btn.appendChild(document.createTextNode(text));
  return btn;
};

/* ---------- 高级设置抽屉 ---------- */
function openAdv() {
  $('adv-mask').classList.add('open');
  $('adv-drawer').classList.add('open');
  $('adv-drawer').setAttribute('aria-hidden', 'false');
}
function closeAdv() {
  $('adv-mask').classList.remove('open');
  $('adv-drawer').classList.remove('open');
  $('adv-drawer').setAttribute('aria-hidden', 'true');
}
$('btnAdvOpen').onclick = openAdv;
$('nav-logs').onclick = () => $('btnLogs').click();
$('nav-feedback').onclick = () => $('btnFeedback').click();
$('nav-tour').onclick = () => openTour();
$('qk-adv').onclick = openAdv;
$('adv-close').onclick = closeAdv;
$('adv-mask').onclick = closeAdv;
$('adv-save').onclick = () => { saveConfig(); };
$('adv-reset').onclick = () => {
  if (!confirm('把这几项恢复成默认值？账号和 Key 不受影响。')) return;
  $('speed').value = 1.5;
  $('jobs').value = 2;
  $('min_interval').value = 2;
  $('notopen_action').value = 'retry';
  $('submit').checked = true;
  $('work_redo_enabled').checked = false;
  $('target_accuracy').value = 100;
  $('acc-val').textContent = '100%';
  $('exam_answer_gap').value = 3;
  $('exam_min_minutes').value = 0;
  $('exam_hold_seconds').value = 0;
};
$('target_accuracy').oninput = (e) => {
  const v = parseInt(e.target.value, 10);
  $('acc-val').textContent = v + '%';
};

/* 概览页的快捷入口 */
$('qk-config').onclick = () => switchPage('settings');
$('qk-courses').onclick = () => { switchPage('settings'); $('btnCourses').click(); };
$('qk-start').onclick = () => { if (!$('btnStart').disabled) $('btnStart').click(); };
