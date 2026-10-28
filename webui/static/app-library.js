/* 本地题库与共享题库 */

/* ---------- 题库 ---------- */
async function loadLibrary() {
  const box = $('lib-list');
  box.innerHTML = '';
  box.appendChild(emptyState('正在读取…'));
  try {
    const kw = $('lib-search').value.trim();
    const r = await fetch('/api/library?q=' + encodeURIComponent(kw));
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || '读取失败');
    $('lib-total').textContent = d.stats.total;
    $('lib-confirmed').textContent = d.stats.confirmed;
    $('lib-pending').textContent = d.stats.pending;
    $('lib-wrong').textContent = d.stats.wrong;

    box.innerHTML = '';
    if (!d.items.length) {
      box.appendChild(emptyState('还没有题目', '跑一次带章节测验的任务后，答过的题会出现在这里。'));
      return;
    }
    const list = document.createElement('div');
    list.className = 'list';
    const statusMap = {0: '待验证', 1: '已确认', 2: '已判错'};
    // 答案的可信依据：用户一眼就能看出哪些能信、哪些只是模型猜的
    const evidenceMap = {
      platform: '平台判分', quiz: '整卷满分', ai_agree: '双答一致',
      shared: '共享库', manual: '手工确认', conflict: '答案冲突',
      manual_wrong: '手工标错', platform_wrong: '平台判错', model: '仅模型猜测'
    };
    d.items.forEach(it => {
      const row = document.createElement('div');
      row.className = 'list-row';
      const name = document.createElement('span');
      name.className = 'name';
      name.innerHTML = '<small></small><span></span>';
      const tag = [statusMap[it.status] || ''];
      if (it.evidence && evidenceMap[it.evidence]) tag.push(evidenceMap[it.evidence]);
      name.querySelector('small').textContent = tag.filter(Boolean).join(' · ');
      name.querySelector('span').textContent = it.title.slice(0, 60);
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = '答案：' + (it.answer || '—');
      const actions = document.createElement('span');
      actions.className = 'actions';

      const mkBtn = (text, handler, cls) => {
        const b = document.createElement('button');
        b.className = cls || 'btn text';
        b.textContent = text;
        b.onclick = handler;
        actions.appendChild(b);
        return b;
      };

      if (it.status !== 1) {
        mkBtn('确认', async () => {
          try {
            await post('/api/library/confirm', {fingerprint: it.fingerprint});
            loadLibrary();
          } catch (err) { setMsg('确认失败：' + err.message, 'err'); }
        });
      }
      if (it.status !== 2) {
        mkBtn('标错', async () => {
          try {
            await post('/api/library/reject', {fingerprint: it.fingerprint});
            loadLibrary();
          } catch (err) { setMsg('标记失败：' + err.message, 'err'); }
        });
      }
      mkBtn('删除', async () => {
        if (!confirm('删除这道题？')) return;
        try {
          await post('/api/library/delete', {fingerprint: it.fingerprint});
          loadLibrary();
        } catch (err) { setMsg('删除失败：' + err.message, 'err'); }
      });

      row.appendChild(name); row.appendChild(meta); row.appendChild(actions);
      list.appendChild(row);
    });
    box.appendChild(list);
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(errorState('读取失败：' + e.message, loadLibrary));
  }
}

async function loadRemoteLibrary() {
  const box = $('remote-list');
  box.innerHTML = '';
  box.appendChild(emptyState('正在读取服务器题库…'));
  try {
    const kw = $('remote-search').value.trim();
    const r = await fetch('/api/library/remote?q=' + encodeURIComponent(kw));
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || '服务器题库暂不可用');
    $('remote-count').textContent = ' · 共 ' + d.total + ' 题';
    box.innerHTML = '';
    if (!d.items.length) {
      box.appendChild(emptyState('服务器题库还没有题目'));
      return;
    }
    const list = document.createElement('div');
    list.className = 'list';
    const statusMap = {0: '有争议', 1: '已收录'};
    d.items.forEach(it => {
      const row = document.createElement('div');
      row.className = 'list-row';
      const name = document.createElement('span');
      name.className = 'name';
      name.innerHTML = '<small></small><span></span>';
      name.querySelector('small').textContent = (statusMap[it.status] || '') +
        (it.votes ? ' ' + it.votes + ' 票' : '');
      name.querySelector('span').textContent = (it.title || '').slice(0, 60);
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = '答案：' + (it.answer || '—');
      row.appendChild(name); row.appendChild(meta);
      list.appendChild(row);
    });
    box.appendChild(list);
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(errorState('读取失败：' + e.message, loadRemoteLibrary));
  }
}

$('btnLibRefresh').onclick = loadLibrary;
$('lib-search').addEventListener('keydown', e => { if (e.key === 'Enter') loadLibrary(); });
$('btnRemoteRefresh').onclick = loadRemoteLibrary;
$('remote-search').addEventListener('keydown', e => { if (e.key === 'Enter') loadRemoteLibrary(); });
$('remote-lib').addEventListener('toggle', e => { if (e.target.open) loadRemoteLibrary(); });
