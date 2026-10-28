/* 启动初始化 */

/* ---------- 初始化 ---------- */
(async function init() {
  try {
    const r = await fetch('/api/config');
    const c = await r.json();
    if (c.username) { $('username').value = c.username; $('nav-account').textContent = c.username; }
    if (c.speed) $('speed').value = c.speed;
    if (c.jobs) $('jobs').value = c.jobs;
    if (c.endpoint) $('endpoint').value = c.endpoint;
    if (c.notopen_action) $('notopen_action').value = c.notopen_action;
    if (c.min_interval !== undefined) $('min_interval').value = c.min_interval;
    if (c.target_accuracy !== undefined) {
      $('target_accuracy').value = c.target_accuracy;
      $('acc-val').textContent = Math.round(c.target_accuracy) + '%';
    }
    if (c.exam_answer_gap !== undefined) $('exam_answer_gap').value = c.exam_answer_gap;
    if (c.exam_min_minutes !== undefined) $('exam_min_minutes').value = c.exam_min_minutes;
    if (c.exam_hold_seconds !== undefined) $('exam_hold_seconds').value = c.exam_hold_seconds;
    if (c.reasoning_effort) $('reasoning_effort').value = c.reasoning_effort;
    $('submit').checked = c.submit !== false;
    $('work_redo_enabled').checked = !!c.work_redo_enabled;
    if (c.model) {
      const o = document.createElement('option');
      o.value = c.model; o.textContent = c.model;
      $('model').innerHTML = ''; $('model').appendChild(o);
    }
    if (c.vision_model) {
      const o = document.createElement('option');
      o.value = c.vision_model; o.textContent = c.vision_model;
      $('vision_model').appendChild(o);
      $('vision_model').value = c.vision_model;
    }
    savedCourseIds = (c.course_list || []).map(String);
    if (c.has_key) $('api_key').placeholder = '已保存（尾号 ' + c.api_key_tail + '），留空沿用';
    if (c.password_saved) $('password').placeholder = '已保存，留空沿用';
    if (c.search_url) $('search_url').value = c.search_url;
    if (c.search_key_saved) $('search_key').placeholder = '已保存，留空沿用';
    if (c.search_enabled === false) $('search_enabled').checked = false;
    if (c.agent_url) $('agent_url').value = c.agent_url;
    if (c.agent_model) $('agent_model').value = c.agent_model;
    if (c.agent_key_saved) $('agent_key').placeholder = '已保存，留空沿用';
    if (c.version) {
      $('ver-current').textContent = c.version;
      $('nav-version').textContent = 'v' + c.version;
      const aboutVer = $('about-version');
      if (aboutVer) aboutVer.textContent = 'v' + c.version;
    }
    $('nav-account').textContent = c.username || '未配置账号';
  } catch (e) {}

  poll();
  setInterval(poll, 1000);
  // 上次关程序时可能已经下载好更新包，启动就把它摆出来
  syncUpdateState();
  setTimeout(checkUpdateSilently, 3000);
  setInterval(checkUpdateSilently, 6 * 3600 * 1000);
})();
