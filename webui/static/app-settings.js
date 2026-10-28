/* 设置页动作与运行启停 */

/* ---------- 设置页动作 ---------- */

$('btnCourses').onclick = async () => {
  flashIcon('btnCourses', 'loader', 0);
  const box = $('courses');
  box.innerHTML = '';
  box.appendChild(emptyState('正在登录并拉取课程…'));
  setMsg('');
  try {
    const d = await post('/api/courses', {username: $('username').value.trim(), password: $('password').value});
    if (!d.courses.length) {
      box.innerHTML = '';
      box.appendChild(emptyState('账号下没有课程'));
      flashIcon('btnCourses', 'warn');
      return;
    }
    const list = document.createElement('div');
    list.className = 'list';
    d.courses.forEach(c => {
      const row = document.createElement('label');
      row.className = 'list-row';
      row.style.cursor = 'pointer';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.value = c.id; cb.style.margin = '0';
      if (savedCourseIds.indexOf(String(c.id)) >= 0) cb.checked = true;
      cb.addEventListener('change', () => {
        const picked = document.querySelectorAll('#courses input:checked');
        if (picked.length > 4) {
          cb.checked = false;
          setMsg('最多同时选择 4 门课程', 'err');
          return;
        }
      });
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = c.title + '（' + c.id + '）';
      row.appendChild(cb);
      row.appendChild(name);
      list.appendChild(row);
    });
    box.innerHTML = '';
    box.appendChild(list);
    setMsg(d.message, 'ok');
    flashIcon('btnCourses', 'success');
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(errorState('拉取失败：' + e.message, () => $('btnCourses').click()));
    flashIcon('btnCourses', 'failed');
  }
};

$('btnModels').onclick = async () => {
  flashIcon('btnModels', 'loader', 0);
  setMsg('正在获取模型列表…');
  try {
    const d = await post('/api/models', {api_key: $('api_key').value.trim(), endpoint: $('endpoint').value.trim()});
    const fill = (id, keepEmpty, emptyText) => {
      const el = $(id);
      el.innerHTML = '';
      if (keepEmpty) {
        const o = document.createElement('option');
        o.value = ''; o.textContent = emptyText;
        el.appendChild(o);
      }
      d.models.forEach(m => {
        const o = document.createElement('option');
        o.value = m; o.textContent = m;
        el.appendChild(o);
      });
    };
    fill('model', false, '');
    fill('vision_model', true, '不使用');
    const prefer = d.models.find(m => /deepseek-chat|qwen|gpt-4o|glm-4/i.test(m));
    if (prefer) $('model').value = prefer;
    setMsg('共 ' + d.models.length + ' 个模型', 'ok');
    flashIcon('btnModels', 'success');
  } catch (e) {
    setMsg('获取失败：' + e.message, 'err');
    flashIcon('btnModels', 'failed');
  }
};

$('btnTest').onclick = async () => {
  flashIcon('btnTest', 'loader', 0);
  setMsg('正在测试连接…');
  try {
    const d = await post('/api/test-llm', {
      api_key: $('api_key').value.trim(),
      endpoint: $('endpoint').value.trim(),
      model: $('model').value
    });
    setMsg(d.message, 'ok');
    flashIcon('btnTest', 'success');
  } catch (e) {
    setMsg('连接失败：' + e.message, 'err');
    flashIcon('btnTest', 'failed');
  }
};

function markDirty(on) {
  const bar = $('save-bar');
  if (bar) bar.hidden = !on;
}
document.querySelectorAll('#page-settings input, #page-settings select').forEach(el => {
  el.addEventListener('change', () => markDirty(true));
  el.addEventListener('input', () => markDirty(true));
});
$('btnSaveTop').onclick = () => $('btnSave').click();

$('btnSave').onclick = async () => {
  flashIcon('btnSave', 'loader', 0);
  try {
    const d = await post('/api/config', formData());
    setMsg(d.message, 'ok');
    markDirty(false);
    flashIcon('btnSave', 'success');
  } catch (e) {
    setMsg('保存失败：' + e.message, 'err');
    flashIcon('btnSave', 'failed');
  }
};

$('btnStart').onclick = async () => {
  // 课程列表为空时后端会刷全部课程，这件事必须让用户明确知道
  const picked = document.querySelectorAll('#courses input:checked').length;
  const typed = $('manual_ids').value.split(/[,，\s]+/).filter(Boolean).length;
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  const saved = (cfg.course_list || []).length;
  if (!picked && !typed && !saved) {
    if (!confirm('没有选择任何课程。继续的话会刷这个账号下的**全部课程**。\n\n确定要开始吗？')) return;
  }
  setMsg('正在启动…');
  try {
    resetView(false);
    const d = await post('/api/start', formData());
    setMsg('已启动，进程 ' + d.pid, 'ok');
    switchPage('overview');
  } catch (e) {
    setMsg('启动失败：' + e.message, 'err');
    // 缺配置时直接把用户送到该去的地方，而不是只丢一句报错
    if (/请填写|请先在设置里/.test(e.message)) {
      switchPage('settings');
      setMsg('启动失败：' + e.message + '（已切到设置页）', 'err');
    }
  }
};

$('btnStop').onclick = async () => {
  try {
    const d = await post('/api/stop');
    setMsg(d.message, 'ok');
  } catch (e) {
    setMsg('停止失败：' + e.message, 'err');
  }
};
