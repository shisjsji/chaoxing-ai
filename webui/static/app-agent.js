/* 助教：题库体检 */

/* ---------- 助教 ---------- */
let agentFilter = '', agentTimer = null;
const AGENT_TAG = {ok: '通过', suspect: '存疑', broken: '数据问题', dup: '重复'};

async function loadAgent() {
  try {
    const r = await fetch('/api/agent/config');
    const d = await r.json();
    const s = d.stats || {};
    if (s.total == null) $('as-total').textContent = '–';
    else window.countTo($('as-total'), s.total);
    if (s.ok == null) $('as-ok').textContent = '–';
    else window.countTo($('as-ok'), s.ok);
    if (s.suspect == null) $('as-suspect').textContent = '–';
    else window.countTo($('as-suspect'), s.suspect);
    if (s.broken == null) $('as-broken').textContent = '–';
    else window.countTo($('as-broken'), s.broken);
    if (s.dup == null) $('as-dup').textContent = '–';
    else window.countTo($('as-dup'), s.dup);
    $('agent-model-hint').textContent = d.ready
      ? '判断用 ' + (d.own ? '助教专用模型' : '主配置的大模型') + '：' + (d.model || '')
      : '还没配模型，现在只能查数据毛病和重复题';
    $('btnAgentRun').disabled = !!d.running;
    if (d.running) startAgentPoll();
  } catch (e) {}
  loadAgentResult();
}

async function loadAgentResult() {
  try {
    const r = await fetch('/api/agent/result?limit=200&state=' + encodeURIComponent(agentFilter));
    const d = await r.json();
    if (d.error) $('agent-hint').textContent = d.error;
    renderAgentList(d.items || []);
  } catch (e) {}
}

function renderAgentList(items) {
  const box = $('agent-list');
  box.innerHTML = '';
  if (!items.length) {
    box.appendChild(emptyState('这里还是空的', '点上面的「开始体检」让助教过一遍题库'));
    return;
  }
  const list = document.createElement('div');
  list.className = 'list';
  items.forEach(it => {
    const row = document.createElement('div');
    row.className = 'agent-row';

    const head = document.createElement('div');
    head.className = 'head';
    const q = document.createElement('div');
    q.className = 'q';
    q.textContent = it.title || '（没有题干）';
    head.appendChild(q);
    const st = it.audit_state || '';
    const tag = document.createElement('span');
    if (st) {
      tag.className = 'tag ' + st;
      tag.textContent = AGENT_TAG[st] || st;
    } else {
      tag.className = 'tag none';
      tag.textContent = '未审';
    }
    head.appendChild(tag);
    row.appendChild(head);

    const ans = document.createElement('div');
    ans.className = 'ans';
    ans.appendChild(document.createTextNode('当前答案：'));
    const strong = document.createElement('b');
    strong.textContent = it.answer || '（空）';
    ans.appendChild(strong);
    ans.appendChild(document.createTextNode(
      '　依据：' + (it.evidence || '无') +
      (it.status === 1 ? '　已确认' : '　未确认')));
    row.appendChild(ans);

    if (it.audit_note) {
      const note = document.createElement('div');
      note.className = 'note';
      note.textContent = it.audit_note;
      row.appendChild(note);
    }

    const ops = document.createElement('div');
    ops.className = 'ops';
    const fp = it.fingerprint;
    const mk = (text, icon, action, extra) => {
      const b = window.iconButton(text, icon, 'btn sm');
      b.onclick = () => applyAgent(fp, action, extra, b);
      return b;
    };
    ops.appendChild(mk('确认这条', 'Check', 'confirm'));
    ops.appendChild(mk('标为错误', 'Close', 'reject'));
    // 模型判错并给了答案时，直接给一个采纳按钮，省得手抄
    if (it.audit_note && it.audit_note.indexOf('建议改为：') === 0) {
      const sug = it.audit_note.slice(5).split('。')[0].trim();
      if (sug) ops.appendChild(mk('采纳建议答案', 'Check', 'set', {answer: sug}));
    }
    ops.appendChild(mk('清除结论', 'Trash', 'clear'));
    row.appendChild(ops);

    list.appendChild(row);
  });
  box.appendChild(list);
  window.stagger(list, '.agent-row');
}

async function applyAgent(fp, action, extra, btn) {
  if (btn) btn.disabled = true;
  flashIcon(btn, 'loader', 0);
  try {
    await post('/api/agent/apply', Object.assign({fingerprint: fp, action: action}, extra || {}));
    $('agent-hint').textContent = '已更新';
    flashIcon(btn, 'success');
    loadAgent();
  } catch (e) {
    $('agent-hint').textContent = e.message;
    flashIcon(btn, 'failed');
    if (btn) btn.disabled = false;
  }
}

function startAgentPoll() {
  if (agentTimer) clearInterval(agentTimer);
  agentTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/agent/config');
      const d = await r.json();
      if (!d.running) {
        clearInterval(agentTimer); agentTimer = null;
        $('btnAgentRun').disabled = false;
        $('agent-hint').textContent = '这一轮跑完了';
        loadAgent();
        return;
      }
    } catch (e) {}
    loadAgentResult();
  }, 1500);
}

$('btnAgentRun').onclick = async () => {
  flashIcon('btnAgentRun', 'loader', 0);
  $('agent-hint').textContent = '正在体检…';
  $('btnAgentRun').disabled = true;
  try {
    await post('/api/agent/audit', {
      max_items: parseInt($('agent-max').value, 10) || 80,
      use_model: $('agent-use-model').checked,
      use_search: $('agent-use-search').checked,
      only_unaudited: $('agent-only-new').checked
    });
    flashIcon('btnAgentRun', 'success');
    startAgentPoll();
  } catch (e) {
    $('agent-hint').textContent = e.message;
    flashIcon('btnAgentRun', 'failed');
    $('btnAgentRun').disabled = false;
  }
};

$('btnAgentUpload').onclick = async () => {
  if (!confirm('把「审查通过」并且原本就有硬证据的题上报到共享题库？\n\n上报后其他人也能用到这些答案。')) return;
  const btn = $('btnAgentUpload');
  flashIcon('btnAgentUpload', 'loader', 0);
  btn.disabled = true;
  $('agent-hint').textContent = '正在上报…';
  try {
    const d = await post('/api/agent/upload', {});
    $('agent-hint').textContent = d.message || '已上报';
    flashIcon('btnAgentUpload', 'success');
  } catch (e) {
    $('agent-hint').textContent = e.message;
    flashIcon('btnAgentUpload', 'failed');
  } finally {
    btn.disabled = false;
  }
};

document.querySelectorAll('[data-agent-filter]').forEach(b => {
  b.onclick = () => {
    agentFilter = b.dataset.agentFilter || '';
    loadAgentResult();
  };
});
