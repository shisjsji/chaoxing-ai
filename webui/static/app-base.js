/* 全局状态、导航、考场区域跟随 */

const $ = id => document.getElementById(id);
const SEP = '\x1f';
const MAX_LINES = 2000;

const STATE_LABEL = {
  pending: '等待中', running: '进行中', confirming: '等待平台确认',
  done: '已完成', error: '失败', notopen: '未开放'
};
const LEVEL_LABEL = {TRACE:'跟踪', DEBUG:'调试', INFO:'信息', WARNING:'警告', ERROR:'错误', CRITICAL:'严重'};

let since = 0, pollFails = 0, sysOnly = false, chapSeq = 0;
const chapMap = new Map();
let examCourses = [], examTimer = null, pendingUpdate = null;
let savedCourseIds = [];   // 已保存的课程，用来恢复勾选状态，不回填手填框

/* ---------- 导航 ---------- */
document.querySelectorAll('.nav-item').forEach(btn => {
  btn.onclick = () => switchPage(btn.dataset.page);
  // 侧栏收成图标栏之后文字会被藏起来，靠 title 兜底
  const label = btn.querySelector('.nav-label');
  if (label) btn.title = label.textContent.trim();
});

// 每项都有一对形状：普通态和选中态。切换页面时做形变过渡。
// 助教用 Sparkle，只有一种形状，不在表里。
const NAV_ICON_ON = {overview: 'GridOn', exams: 'ExamOn', library: 'LibraryOn',
                     settings: 'SlidersOn', about: 'InfoOn'};
const NAV_ICON_OFF = {overview: 'Grid', exams: 'Exam', library: 'Library',
                      settings: 'Sliders', about: 'Info'};

function morphNavIcons(page) {
  const icons = window.MORPH_ICONS;
  if (!icons) return;
  Object.keys(NAV_ICON_ON).forEach(key => {
    const btn = document.querySelector('.nav-item[data-page="' + key + '"]');
    if (!btn) return;
    const el = btn.querySelector('morph-icon');
    if (!el) return;
    const name = key === page ? NAV_ICON_ON[key] : NAV_ICON_OFF[key];
    if (!icons[name] || el.dataset.morph === name) return;
    el.dataset.morph = name;
    try { el.morphTo(icons[name], 'snappy'); } catch (e) {}
  });
}

function switchPage(page) {
  document.querySelectorAll('.nav-item').forEach(b =>
    b.setAttribute('aria-current', b.dataset.page === page ? 'page' : 'false'));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('on', p.id === 'page-' + page));
  morphNavIcons(page);
  if (page === 'library') loadLibrary();
  if (page === 'agent') loadAgent();
  if (page === 'exams') {
    if (!examCourses.length) loadExams();
    refreshExamCloseBtn();
  }
  queueExamSync(60);
}

/* ---------- 考场窗口跟随页面里留出的那块区域 ---------- */
let stageSyncTimer = 0;
let lastStageBox = '';

function examStageBox() {
  const stage = $('examStage');
  const host = document.querySelector('.content');
  if (!stage || !host || !stage.offsetParent) return null;
  const r = stage.getBoundingClientRect();
  const c = host.getBoundingClientRect();
  // 和内容区可见范围求交：滚到看不见的部分不摆窗口。
  // 坐标是视口坐标，正好等于主窗口客户区坐标，原生窗口可以直接用。
  const left = Math.max(r.left, c.left) + 1;
  const top = Math.max(r.top, c.top) + 1;
  const right = Math.min(r.right, c.right) - 1;
  const bottom = Math.min(r.bottom, c.bottom) - 1;
  if (right - left < 80 || bottom - top < 80) return null;
  return {
    left: left, top: top,
    width: right - left, height: bottom - top,
    dpr: window.devicePixelRatio || 1
  };
}

function syncExamStage(force) {
  const box = examStageBox();
  const key = JSON.stringify(box);
  // 上方列表加载完会把这块区域推下去，尺寸没变但位置变了，
  // 所以不能只靠 resize/scroll 事件，还得自己盯着。
  if (!force && key === lastStageBox) return Promise.resolve();
  lastStageBox = key;
  return fetch('/api/exam/rect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ box: box })
  }).catch(() => {});
}

function queueExamSync(delay) {
  clearTimeout(stageSyncTimer);
  stageSyncTimer = setTimeout(() => syncExamStage(true), delay === undefined ? 80 : delay);
}

async function refreshExamCloseBtn() {
  try {
    const r = await fetch('/api/exam/browser');
    const d = await r.json();
    $('btnExamClose').hidden = !d.active;
  } catch (e) {}
}

window.addEventListener('resize', () => queueExamSync(120));
document.addEventListener('scroll', () => queueExamSync(120), true);
setInterval(syncExamStage, 400);
