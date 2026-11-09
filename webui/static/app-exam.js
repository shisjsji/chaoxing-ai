/* 考试列表、考场浏览器、自动作答 */

/* ---------- 考试 ---------- */
/* 同一时间只发一轮请求：切页和点刷新很容易同时触发，
   两份并发拉取会互相等，界面就一直显示「正在读取」。 */
let examsLoading = false;

async function loadExams() {
  if (examsLoading) return;
  examsLoading = true;
  try {
    await loadExamsOnce();
  } finally {
    examsLoading = false;
  }
}

async function loadExamsOnce() {
  const box = $('exam-list');
  box.innerHTML = '';
  box.appendChild(emptyState('正在读取考试列表…'));
  $('exam-hint').textContent = '';
  try {
    // 不再往这里塞账号密码：列表用的是「登录学习通」保存下来的那份登录态
    const r = await post('/api/exams', {});
    examCourses = r.courses || [];
    if (!examCourses.length) {
      box.innerHTML = '';
      box.appendChild(emptyState('没有找到已发布的考试', '课程考试发布后会出现在这里。', loadExams));
      return;
    }
    $('exam-hint').textContent = '共 ' + r.total + ' 场';
    box.innerHTML = '';
    const list = document.createElement('div');
    list.className = 'list';
    examCourses.forEach(group => {
      group.items.forEach(item => {
        const row = document.createElement('div');
        row.className = 'list-row';
        const name = document.createElement('span');
        name.className = 'name';
        name.innerHTML = '<small></small><span></span>';
        name.querySelector('small').textContent = group.course + ' / ';
        name.querySelector('span').textContent = item.name;
        const meta = document.createElement('span');
        meta.className = 'meta';
        meta.textContent = [item.status, item.expire].filter(Boolean).join(' · ') || '—';
        const actions = document.createElement('span');
        actions.className = 'actions';
        const finished = item.status === '已完成' || item.status === '已批阅';
        const btn = document.createElement('button');
        btn.className = 'btn';
        btn.textContent = finished ? '重考' : '开始';
        btn.title = '打开考试窗口：你过验证码，程序自动答题';
        btn.onclick = () => openExamBrowser(group, item, btn, finished ? 'retake' : 'start');
        actions.appendChild(btn);
        row.appendChild(name); row.appendChild(meta); row.appendChild(actions);
        list.appendChild(row);
      });
    });
    box.appendChild(list);
  window.stagger(list, '.list-row');
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(errorState('读取失败：' + e.message, loadExams));
  }
}

async function openExamBrowser(group, item, btn, mode) {
  const tip = mode === 'retake'
    ? '重考「' + item.name + '」？\n\n会打开考试窗口，你在里面点重考并完成验证码，之后程序自动答完整套卷子并交卷。重考次数会用掉一次。'
    : '开始「' + item.name + '」？\n\n会打开考试窗口，你在里面点进入考试并完成验证码，之后程序自动答题、自动交卷。';
  if (!confirm(tip)) return;
  if (btn) { btn.disabled = true; btn.textContent = '打开中…'; }

  const q = new URLSearchParams({
    redo: 1, taskrefId: item.exam_id, courseId: group.courseId,
    classId: group.clazzId, role: '', source: 0,
    enc_task: item.enc_task || '', cpi: group.cpi, vx: 0, examsignal: 1
  });
  const url = 'https://mooc1.chaoxing.com/exam-ans/exam/phone/task-exam?' + q.toString();
  try {
    await syncExamStage(true);
    const res = await post('/api/exam/browser', {url});
    $('btnExamClose').hidden = !(res && res.embedded);
    setMsg('考场页面已嵌到下方区域：在里面点「进入考试」并完成验证码，程序会自动接手', 'warn');
    $('exam-hint').textContent = '等待你在下方考场页面里进入考场…';
    $('app').classList.remove('nologs');
    watchExamBrowser(group, item, btn);
  } catch (e) {
    alert('打开考试窗口失败：' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '开始'; }
  }
}

function watchExamBrowser(group, item, btn) {
  if (examTimer) clearInterval(examTimer);
  let ticks = 0;
  examTimer = setInterval(async () => {
    ticks++;
    if (ticks > 420) {
      clearInterval(examTimer); examTimer = null;
      $('exam-hint').textContent = '等待超时，未检测到进入考场';
      return;
    }
    try {
      const r = await fetch('/api/exam/browser');
      const d = await r.json();
      if (!d.enc) return;
      clearInterval(examTimer); examTimer = null;
      $('exam-hint').textContent = '已进入考场，开始自动答题…';
      setMsg('已接手考试，正在答题', 'ok');
      try {
        await post('/api/exam/autodrive', {course: (group && group.course) || ''});
      } catch (err) {
        // 这一步失败以前被空 catch 吞掉：界面停在「正在答题」，其实一题没答。
        $('exam-hint').textContent = '自动作答没能开始';
        setMsg('自动作答没能开始：' + err.message, 'err');
        setTimeout(watchExamBrowser, 2000);
        return;
      }
      startExamPolling();
    } catch (e) {
      $('exam-hint').textContent = '读取考场状态失败，正在重试…';
    }
  }, 1000);
}

function startExamPolling() {
  if (examTimer) clearInterval(examTimer);
  examTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/exam/status');
      const d = await r.json();
      $('face-card').hidden = d.status !== 'waiting_face';
      if (d.status === 'running') $('exam-hint').textContent = '答题中，已提交 ' + d.answered + ' 题';
      if (d.status === 'done') {
        clearInterval(examTimer); examTimer = null;
        if (d.submitted) {
          $('exam-hint').textContent = '已交卷，共提交 ' + d.answered + ' 题';
          setMsg('考试已交卷', 'ok');
        } else {
          $('exam-hint').textContent = '未交卷，已作答 ' + d.answered + ' 题，请到平台手动完成并提交';
          setMsg('考试未交卷，需要你手动处理', 'warn');
        }
        setTimeout(loadExams, 800);
      }
      if (d.status === 'error') {
        clearInterval(examTimer); examTimer = null;
        $('exam-hint').textContent = '已中断';
        setMsg('考试中断：' + d.error, 'err');
        setTimeout(loadExams, 800);
      }
    } catch (e) {}
  }, 1500);
}

$('about-kuan').onclick = async () => {
  try {
    await navigator.clipboard.writeText('30432128');
    setMsg('酷安号已复制', 'ok');
  } catch (e) {
    setMsg('复制失败，酷安号是 30432128', 'warn');
  }
};
$('btnFeedback').onclick = () => {
  $('feedback-dialog').showModal();
};
$('feedback-cancel').onclick = () => $('feedback-dialog').close();
$('feedback-send').onclick = async () => {
  const text = $('feedback-text').value.trim();
  if (!text) { setMsg('先写点内容再发', 'err'); return; }
  const btn = $('feedback-send');
  btn.disabled = true;
  flashIcon(btn, 'loader', 0);
  setBtnText(btn, '发送中…');
  let context = '版本 ' + $('ver-current').textContent;
  if ($('feedback-logs').checked) {
    const lines = Array.from($('logs').children).slice(-100)
      .map(el => el.textContent);
    if (lines.length) context += '\n\n--- 最近日志 ---\n' + lines.join('\n');
  }
  try {
    const r = await post('/api/feedback/push', {
      kind: 'feedback', module: 'ui', message: text, context: context
    });
    $('feedback-dialog').close();
    $('feedback-text').value = '';
    setMsg(r.message || '已发送', 'ok');
    flashIcon(btn, 'success');
    window.setIcon('ico-fb', 'Check');
    setTimeout(() => window.setIcon('ico-fb', 'Message'), 2200);
  } catch (e) {
    setMsg('发送失败：' + e.message, 'err');
    flashIcon(btn, 'failed');
  } finally {
    btn.disabled = false;
    setBtnText(btn, '发送');
  }
};

$('btnExamRefresh').onclick = loadExams;

/* 登录：程序只负责开窗口，账号密码和验证码都由用户自己来 */
$('btnLoginOpen').onclick = async () => {
  flashIcon('btnLoginOpen', 'loader', 0);
  try {
    const r = await post('/api/login/open', {});
    $('login-hint').textContent = r.message
      || '在下方窗口里完成登录，然后点「我已登录，读取状态」';
    flashIcon('btnLoginOpen', 'success');
    setMsg('登录窗口已打开，请在下方完成登录', 'ok');
  } catch (e) {
    $('login-hint').textContent = '打不开登录页：' + e.message;
    flashIcon('btnLoginOpen', 'failed');
  }
};

$('btnLoginDone').onclick = async () => {
  flashIcon('btnLoginDone', 'loader', 0);
  $('login-hint').textContent = '正在读取窗口里的登录状态…';
  try {
    const r = await post('/api/login/collect', {});
    $('login-hint').textContent = r.message || '登录状态已保存';
    flashIcon('btnLoginDone', 'success');
    setMsg(r.message || '登录状态已保存', 'ok');
    await loadExams();
  } catch (e) {
    $('login-hint').textContent = '读取登录状态失败：' + e.message;
    flashIcon('btnLoginDone', 'failed');
  }
};

$('btnOpenUrl').onclick = async () => {
  const input = $('open-url');
  let url = (input.value || '').trim();
  if (!url) {
    setMsg('先把要打开的网址粘到左边输入框', 'err');
    input.focus();
    return;
  }
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  flashIcon('btnOpenUrl', 'loader', 0);
  try {
    await syncExamStage(true);
    const res = await post('/api/exam/browser', {url: url});
    $('btnExamClose').hidden = !(res && res.embedded);
    $('exam-hint').textContent = '已在下方的浏览器里打开';
    setMsg('页面已打开，复制粘贴限制已解除', 'ok');
    flashIcon('btnOpenUrl', 'success');
  } catch (e) {
    setMsg('打不开：' + e.message, 'err');
    flashIcon('btnOpenUrl', 'failed');
  }
};
$('btnExamClose').onclick = async () => {
  if ($('exam-hint').textContent.includes('答题中')
      && !confirm('正在自动答题，关掉会中断。确定关闭？')) return;
  $('btnExamClose').hidden = true;
  $('exam-hint').textContent = '';
  try { await post('/api/exam/browser/close', {}); } catch (e) {}
};
$('btnFaceDone').onclick = async () => {
  try {
    await post('/api/exam/face_done', {});
    $('face-card').hidden = true;
  } catch (e) {
    alert('提交失败：' + e.message);
  }
};

$('btnTestSearch').onclick = async () => {
  flashIcon('btnTestSearch', 'loader', 0);
  const hint = $('search-hint');
  hint.textContent = '测试中…';
  try {
    const d = await post('/api/test-search', {
      search_url: $('search_url').value.trim(),
      search_key: $('search_key').value.trim()
    });
    hint.textContent = d.message || '正常';
    flashIcon('btnTestSearch', 'success');
  } catch (e) {
    hint.textContent = e.message;
    flashIcon('btnTestSearch', 'failed');
  }
};
