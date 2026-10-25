"""在考试窗口里自动答题。

不走 HTTP 提交，全部通过内嵌浏览器模拟真人点击：
读题 → 问模型 → 点选项 → 回读答案确认 → 翻页。

漏答的两个真实原因（都已在下面处理）：
1. 判断题的选项 key 是 true/false，不是字母，按字母匹配会落空；
2. 点击偶尔没写进 answer 字段，翻页前必须回读确认。
"""

import json
import re
import time

from api.exam import ExamQuestion, ask_ai, ask_with_check

TYPE_MAP_CN = {"单选题": "single", "多选题": "multiple", "判断题": "judgement",
               "填空题": "completion", "简答题": "shortanswer",
               "名词解释": "term", "论述题": "essay", "计算题": "calculation"}

SUBJECTIVE = ("简答题", "论述题", "名词解释", "计算题")

_CLEAN = [
    re.compile(r"^.*?[（(]共\s*\d+\s*题[^）)]*[）)]\s*"),
    re.compile(r"^\d+\s*[.、]\s*"),
    re.compile(r"^[（(]\s*\d+(\.\d+)?\s*分\s*[）)]\s*"),
    re.compile(r"^标记\s*"),
]

READ_Q = r"""
(function () {
  var w = document.querySelector('.questionWrap');
  if (!w) return JSON.stringify({ok: false});
  var tit = w.querySelector('div.tit');
  var qidNode = w.querySelector("input[name='questionId']");
  var typeNode = w.querySelector("input[name^='typeName']");
  var opts = [];
  var boxes = w.querySelectorAll('div.answerList');
  for (var i = 0; i < boxes.length; i++) {
    var b = boxes[i];
    var cc = b.querySelector('cc');
    opts.push({
      key: b.getAttribute('name') || '',
      shown: ((b.querySelector('.No') || {}).innerText || '').trim(),
      text: cc ? cc.innerText.trim() : (b.innerText || '').trim().slice(0, 60)
    });
  }
  var ans = w.querySelector("input[id^='answer']");
  var prog = document.body.innerText.match(/(\d+)\s*\/\s*(\d+)/);
  return JSON.stringify({
    ok: true,
    qid: qidNode ? qidNode.value : '',
    typeName: typeNode ? typeNode.value : '',
    title: tit ? tit.innerText.replace(/\s+/g, ' ').trim() : '',
    options: opts,
    answered: ans ? ans.value : '',
    hasTextarea: !!w.querySelector('textarea'),
    progress: prog ? prog[0] : ''
  });
})()
"""

READ_CARD = r"""
(function () {
  var lis = document.querySelectorAll('.answerSheetBox li.nav-revise');
  if (!lis.length) return JSON.stringify({ok: false});
  // 当前停留的那一题只带 current、不带 complated，但它可能已经答好了，
  // 所以单独按 answer 字段判断，避免把它误算成未答。
  var cur = document.querySelector('.answerSheetBox li.nav-revise.current');
  var curNum = cur ? ((cur.querySelector('a') || {}).innerText || '').trim() : '';
  var curAns = (document.querySelector("input[id^='answer']") || {}).value || '';
  var undone = [], done = 0;
  for (var i = 0; i < lis.length; i++) {
    var li = lis[i], a = li.querySelector('a');
    var n = a ? (a.innerText || '').trim() : '';
    if (/(^|\s)complated(\s|$)/.test(li.className)) {
      done++;
    } else if (n === curNum && curAns) {
      done++;
    } else {
      undone.push(n);
    }
  }
  return JSON.stringify({ok: true, total: lis.length, done: done, undone: undone});
})()
"""


def clean_title(text: str) -> str:
    t = (text or "").strip()
    for _ in range(3):
        before = t
        for pat in _CLEAN:
            t = pat.sub("", t).strip()
        if t == before:
            break
    return t


_EXEC = None


def _executor():
    """跨线程执行 evaluate_js 用；单例复用，避免每次调用都新建线程池。"""
    global _EXEC
    if _EXEC is None:
        from concurrent.futures import ThreadPoolExecutor

        _EXEC = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cx-eval")
    return _EXEC


def _js(win, script: str, timeout: float = 15.0):
    """执行页面 JS，带超时。

    evaluate_js 是同步调用：页面一旦弹出原生 alert/confirm，JS 主线程被挂起，
    这个调用就永远不返回，整条答题线程会死在这儿。超时后放弃等待，
    让流程能继续走并把情况写进日志。
    """
    if win is None:
        return None
    try:
        future = _executor().submit(win.evaluate_js, script)
        return future.result(timeout=timeout)
    except Exception:
        return None


GUARD_JS = r"""
(function () {
  if (window.__cxGuard) return 'already';
  window.__cxGuard = true;
  window.__cxDialogs = [];
  window.alert = function (m) {
    window.__cxDialogs.push(['alert', String(m)]);
  };
  window.confirm = function (m) {
    window.__cxDialogs.push(['confirm', String(m)]);
    return true;   // 默认同意：阻塞比误操作更致命，所有弹窗都会写进日志
  };
  window.prompt = function (m) {
    window.__cxDialogs.push(['prompt', String(m)]);
    return null;
  };
  return 'ok';
})()
"""


def install_guard(win) -> None:
    """装对话框守卫。页面重载后要重新装，所以每次跳题后都调一次。"""
    _js(win, GUARD_JS, timeout=5.0)


def drain_dialogs(win, log) -> None:
    """把页面弹过的对话框取出来写进日志，方便判断卡在哪。"""
    raw = _js(win,
              "(function(){if(!window.__cxDialogs)return '';"
              "var d=window.__cxDialogs.splice(0);"
              "return d.map(function(x){return x[0]+':'+x[1].slice(0,60);}).join(' | ');})()",
              timeout=5.0)
    if raw:
        log(f"  页面弹窗（已自动同意）：{raw}")


def wait_js(win, expr: str, timeout: float = 8.0, interval: float = 0.25):
    """轮询等某个 JS 表达式变成真值，超时返回 None。

    比固定 sleep 可靠：页面快就立刻过，页面慢也不至于点空。
    """
    import time as _t

    end = _t.time() + timeout
    while _t.time() < end:
        value = _js(win, expr)
        if value:
            return value
        _t.sleep(interval)
    return None


QA_READY = "!!document.querySelector('.questionWrap, .questionLi, .questionContent')"


def current_qid(win) -> str:
    """当前页面的题目 ID。用它判断页面是不是真的换了。"""
    return _js(win, "(function(){var n=document.querySelector(\"input[name='questionId']\");"
                    "return n?n.value:'';})()", timeout=6.0) or ""


def wait_qid_change(win, old: str, timeout: float = 10.0) -> str:
    """等题目真的换成另一道。

    旧题的容器还在 DOM 里，所以不能用「容器存在」判断加载完成，
    必须比对题号。这是之前读错题、答案点错位的根因。
    """
    end = time.time() + timeout
    while time.time() < end:
        q = current_qid(win)
        if q and q != old:
            return q
        time.sleep(0.2)
    return ""


def prev_question(win) -> bool:
    """翻回上一题，以题号变化为准。"""
    old = current_qid(win)
    r = _js(win, "(function(){var b=document.getElementById('preQuestion');"
                 "if(!b)return 'none';b.click();return 'ok';})()", timeout=6.0)
    if r != "ok":
        return False
    return bool(wait_qid_change(win, old, timeout=6.0))


def goto_first(win, log=None) -> bool:
    """回到第 1 题。

    先走答题卡（最快）；答题卡里第 1 题可能因为已完成而点不动，
    这时退回「连续点上一题」，别因为一次点击失败就放弃整轮。
    """
    old = current_qid(win)
    open_card(win)
    js = ("(function(){var lis=document.querySelectorAll('.answerSheetBox li.nav-revise');"
          "for(var i=0;i<lis.length;i++){var a=lis[i].querySelector('a');"
          "if(a&&(a.innerText||'').trim()==='1'){a.click();return 'ok';}}"
          "return 'notfound';})()")
    if _js(win, js, timeout=6.0) == "ok":
        if wait_qid_change(win, old) or current_qid(win) == old:
            return True

    # 兜底：一路点上一题退回开头
    if log:
        log("  答题卡跳转没生效，改用「上一题」逐步退回开头")
    for _ in range(120):
        before = current_qid(win)
        if not prev_question(win):
            # 没有上一题按钮了，说明已经在第一题
            return True
        if current_qid(win) == before:
            return False
    return False


def next_question(win) -> bool:
    """翻到下一题，以题号变化为准。"""
    old = current_qid(win)
    r = _js(win, "(function(){var b=document.getElementById('nextQuestion');"
                 "if(!b)return 'none';b.click();return 'ok';})()", timeout=6.0)
    if r != "ok":
        return False
    return bool(wait_qid_change(win, old))


def wait_ready(win, timeout: float = 8.0) -> bool:
    """等题目区渲染出来。"""
    return bool(wait_js(win, QA_READY, timeout))


def read_question(win):
    raw = _js(win, READ_Q)
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def read_answer(win) -> str:
    return _js(win, "(function(){var w=document.querySelector('.questionWrap');"
                    "if(!w)return '';var a=w.querySelector(\"input[id^='answer']\");"
                    "return a?a.value:'';})()") or ""


def open_card(win) -> None:
    """展开答题卡面板。已经展开时不要再点，否则会把它关掉。"""
    state = _js(win, "(function(){"
                    "var box=document.querySelector('.answerSheetBox');"
                    "var vis=box&&(box.offsetWidth>0||box.offsetHeight>0||box.getClientRects().length>0);"
                    "if(vis)return 'open';"
                    "var b=document.querySelector('div.answerIcon');"
                    "if(b){b.click();return 'clicked';}"
                    "return 'notfound';})()", timeout=5.0)
    if state == "clicked":
        time.sleep(0.6)


def read_card(win):
    open_card(win)
    raw = _js(win, READ_CARD)
    try:
        return json.loads(raw) if raw else {"ok": False}
    except Exception:
        return {"ok": False}


def goto(win, num: str) -> bool:
    open_card(win)
    js = ("(function(){var lis=document.querySelectorAll('.answerSheetBox li.nav-revise');"
          "for(var i=0;i<lis.length;i++){var a=lis[i].querySelector('a');"
          f"if(a&&(a.innerText||'').trim()==='{num}'){{a.click();return 'ok';}}}}"
          "return 'notfound';})()")
    old = current_qid(win)
    if _js(win, js, timeout=6.0) != "ok":
        return False
    # 点了题号必须等到题目真的换掉，否则后面读到的是上一题
    return bool(wait_qid_change(win, old))


def click_option(win, key: str) -> None:
    js = ("(function(){"
          f"var el=document.querySelector('div.answerList[name=\"{key}\"]');"
          "if(!el)return 'missing';"
          "['mousedown','mouseup','click'].forEach(function(t){"
          "el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,button:0,view:window}));});"
          "return 'ok';})()")
    _js(win, js)


FILL_JS = r"""
(function (text) {
  var box = document.querySelector('.questionWrap') || document;
  var el = box.querySelector('.fillAnswer input, .fillAnswer textarea')
        || box.querySelector('textarea')
        || box.querySelector('input[type=text]');
  if (!el) return 'no-input';

  // React 受控组件只认原生 setter，直接改 value 不会触发 onChange
  var proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;

  el.dispatchEvent(new Event('focus', {bubbles: true}));
  setter.call(el, text);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new Event('blur', {bubbles: true}));
  el.dispatchEvent(new Event('keyup', {bubbles: true}));
  return (el.value || '').length ? 'ok' : 'empty';
})(%s)
"""


def fill_text(win, text: str):
    """填主观题的输入框。返回 'ok' / 'no-input' / 'empty'。"""
    import json as _json

    if not (text or '').strip():
        return 'empty'
    return _js(win, FILL_JS % _json.dumps(text, ensure_ascii=False)) or 'no-input'


def pick_keys(options, answer: str):
    """把模型的答案映射到真实选项 key（选项顺序被平台打乱过）。"""
    text = (answer or "").strip()
    if not text:
        return []

    real = {o["key"].lower(): o["key"] for o in options}
    if "true" in real or "false" in real:
        low = text.lower()
        yes = ("正确", "对", "√", "是", "true", "t", "yes", "y")
        no = ("错误", "错", "×", "否", "false", "f", "no", "n")
        if any(w in text for w in yes) or low in yes:
            return [real.get("true", "true")]
        if any(w in text for w in no) or low in no:
            return [real.get("false", "false")]
        return []

    letters = re.findall(r"[A-Za-z]", text)
    if letters and len(text) <= 8:
        shown = {o["shown"].upper(): o["key"] for o in options if o.get("shown")}
        keys = []
        for ch in letters:
            k = shown.get(ch.upper())
            if k and k not in keys:
                keys.append(k)
        if keys:
            return keys

    keys = []
    for o in options:
        ot = (o.get("text") or "").strip()
        if ot and (ot in text or text in ot) and o["key"] not in keys:
            keys.append(o["key"])
    return keys


def _library():
    """题库实例复用，避免每次答题都重开一次数据库。"""
    global _LIB
    if _LIB is None:
        from api.library import Library

        _LIB = Library()
    return _LIB


_LIB = None


def remember(q, info, keys, evidence: str = "model") -> None:
    """把这道题和答案记进题库。

    存答案原文而不是字母：这场考试的选项是被打乱过的，
    同一个字母换个场次就指向别的选项，只有文本能对上。

    只有经过核对的答案才记成已确认（双答一致），单次模型答案一律待验证。
    """
    try:
        from api.library import fingerprint

        picked = [(o.get("text") or "").strip()
                  for o in info["options"] if o["key"] in keys]
        picked = [t for t in picked if t]
        if not picked:
            return
        answer_text = "、".join(picked) if len(picked) > 1 else picked[0]
        fp = fingerprint(q.title, q.options_text, q.qtype)
        _library().upsert(fp, q.title, q.options_text, q.qtype,
                          answer_text, source="exam",
                          status=1 if evidence == "ai_agree" else 0,
                          evidence=evidence)
    except Exception:
        pass


def cached_answer(q, fp: str) -> tuple:
    """先问本地题库，再问共享题库。返回 (答案, 依据来源)。"""
    try:
        local = _library().lookup(fp)
    except Exception:
        local = None
    if local:
        return local, "library"

    try:
        from api.remote_library import lookup_batch

        shared = lookup_batch([fp]).get(fp) or ""
    except Exception:
        shared = ""
    if not shared:
        return "", ""

    # 落到本地，下次直接命中，不用每题都联网
    try:
        _library().upsert(fp, q.title, q.options_text, q.qtype, shared,
                          source="share", status=1, evidence="shared")
    except Exception:
        pass
    return shared, "shared"


def solve_current(win, log=print):
    """作答当前这一题并复查。返回 'ok' / 'already' / 'subjective' / 'fail'。"""
    info = read_question(win)
    if not info or not info.get("ok"):
        return "fail"

    prog = info.get("progress", "")

    # 主观题：现在走浏览器输入框，能真的填进去（以前只能跳过）
    if info.get("hasTextarea") or info["typeName"] in SUBJECTIVE:
        if info.get("answered"):
            log(f"  [{prog}] 已有答案，跳过")
            return "already"
        q = ExamQuestion(index=0, qid=info["qid"],
                         qtype=TYPE_MAP_CN.get(info["typeName"], "shortanswer"),
                         title=clean_title(info["title"]), options={},
                         type_name=info["typeName"])
        answer = (ask_ai(q) or "").strip()
        if not answer:
            log(f"  [{prog}] 主观题没写出来")
            return "fail"
        result = fill_text(win, answer)
        if result == "ok":
            log(f"  [{prog}] {info['typeName'] or '主观题'} 已填写 {len(answer)} 字")
            return "ok"
        log(f"  [{prog}] 主观题填不进去（{result}）")
        return "fail"

    options = {o["key"]: o["text"] for o in info["options"] if o["key"]}
    q = ExamQuestion(index=0, qid=info["qid"],
                     qtype=TYPE_MAP_CN.get(info["typeName"], "single"),
                     title=clean_title(info["title"]), options=options,
                     type_name=info["typeName"])

    if info.get("answered"):
        # 不把页面上的既有答案写进题库：它可能是旧版本错位留下的错误答案，
        # 也可能是用户手填的，来源不可验证。入库会让错误一直传下去。
        log(f"  [{prog}] 已有答案 {info['answered']!r}（沿用，不入库）")
        return "already"

    if not options:
        log(f"  [{prog}] 没解析到选项")
        return "fail"

    from api.library import fingerprint

    fp = fingerprint(q.title, q.options_text, q.qtype)

    keys, answer, evidence = [], "", ""
    hit, where = cached_answer(q, fp)
    if hit:
        keys = pick_keys(info["options"], hit)
        if keys:
            answer, evidence = hit, where
            log(f"  [{prog}] 题库命中（{'共享' if where == 'shared' else '本地已确认'}）"
                f"：{hit[:26]}")

    if not keys:
        log(f"  [{prog}] 正在问模型（双答核对）…")
        for _ in range(2):
            answer, agreed = ask_with_check(q, log)
            keys = pick_keys(info["options"], answer)
            if keys:
                evidence = "ai_agree" if agreed else "model"
                break
    if not keys:
        log(f"  [{prog}] 没拿到可用答案")
        return "fail"

    # 点完必须回读，没写进去就重试；用轮询等而不是固定延时，
    # 慢页面上也能等到答案真正落到字段里
    for _ in range(3):
        for k in keys:
            click_option(win, k)
            time.sleep(0.15)
        cur = wait_js(
            win,
            "(function(){var w=document.querySelector('.questionWrap');"
            "if(!w)return '';var a=w.querySelector(\"input[id^='answer']\");"
            "return a?a.value:'';})()",
            timeout=3.0, interval=0.2) or ''
        if cur:
            if evidence in ("ai_agree", "model"):
                remember(q, info, keys, evidence)
            log(f"  [{prog}] 已作答 {keys} = {cur!r} <- {answer[:26]}")
            return "ok"
    log(f"  [{prog}] 点了三次都没写进答案")
    return "fail"


SUBMIT_FIND = r"""
(function () {
  var bs = document.querySelectorAll('a,button');
  for (var i = 0; i < bs.length; i++) {
    var t = (bs[i].innerText || '').replace(/\\s+/g, '');
    if (/^(交卷|提交试卷|提交答卷|立即交卷)$/.test(t)) {
      window.__cxSubmit = bs[i];
      return t;
    }
  }
  return '';
})()
"""

SUBMIT_CONFIRM = r"""
(function () {
  var bs = document.querySelectorAll('a,button,.btn,span');
  for (var i = 0; i < bs.length; i++) {
    var t = (bs[i].innerText || '').replace(/\\s+/g, '');
    if (/^(确定|确认|确认交卷|确定交卷)$/.test(t)) { bs[i].click(); return t; }
  }
  return '';
})()
"""

SUBMIT_STATE = r"""
(function () {
  var txt = document.body.innerText || '';
  if (/考试已提交|已交卷|提交成功|交卷成功|考试结束/.test(txt)) return 'done';
  if (!document.querySelector('.questionWrap')) return 'gone';
  return '';
})()
"""


def submit_paper(win, log=print) -> bool:
    """点交卷。只在答题卡确认全部作答后才会被调用。"""
    label = _js(win, SUBMIT_FIND)
    if not label:
        log("没找到交卷按钮，请在窗口里手动点交卷")
        return False

    log(f"找到「{label}」按钮，正在交卷")
    _js(win, "window.__cxSubmit && window.__cxSubmit.click()")
    time.sleep(2.0)

    confirmed = _js(win, SUBMIT_CONFIRM)
    if confirmed:
        log(f"已确认「{confirmed}」")
        time.sleep(2.0)

    state = _js(win, SUBMIT_STATE)
    if state in ("done", "gone"):
        log("交卷完成")
        return True

    log("点了交卷，但没看到「已提交」的迹象，请到考试窗口里确认一下")
    return False


def run_pass(win, log=print, rounds: int = 3, deadline_minutes: float = 25) -> dict:
    """按答题卡把所有未答的题补完，每轮都重新读卡核对。

    deadline_minutes 是硬上限：跨线程操作 WebView 万一卡住，
    也要能自己收尾，不能让状态一直挂在「作答中」。
    """
    import time as _t

    deadline = _t.time() + deadline_minutes * 60
    stats = {"ok": 0, "fail": 0, "left": 0}
    for rnd in range(rounds):
        if _t.time() > deadline:
            log(f"已到 {deadline_minutes:g} 分钟上限，先停在这里")
            break
        card = read_card(win)
        if not card.get("ok"):
            log("读不到答题卡，停止")
            break
        undone = card["undone"]
        log(f"第 {rnd + 1} 轮：共 {card['total']} 题，已答 {card['done']}，未答 {len(undone)}")
        install_guard(win)
        drain_dialogs(win, log)
        if not undone:
            stats["left"] = 0
            return stats

        # 从第 1 题顺序翻到卷尾：不靠答题卡乱跳，阅读顺序也更符合直觉
        if not goto_first(win, log):
            log("回不到第 1 题，本轮放弃")
            break
        install_guard(win)
        drain_dialogs(win, log)

        seen: set = set()
        while True:
            if _t.time() > deadline:
                log("时间到，本轮中止")
                break
            info = read_question(win)
            qid = (info or {}).get("qid", "")
            if not info or not info.get("ok"):
                log("读不到题目，视为卷尾")
                break
            if qid and qid in seen:
                log("题目回到已处理位置，视为卷尾")
                break
            if qid:
                seen.add(qid)

            prog = info.get("progress", "")
            status = solve_current(win, log)
            drain_dialogs(win, log)
            if status == "ok":
                stats["ok"] += 1
            elif status == "fail":
                stats["fail"] += 1

            if not next_question(win):
                log("已到最后一题")
                break

        card = read_card(win)
        if card.get("ok"):
            stats["left"] = len(card["undone"])

    card = read_card(win)
    if card.get("ok"):
        stats["left"] = len(card["undone"])
        log(f"复查结果：{card['total']} 题，已答 {card['done']}，未答 {stats['left']} 题")
        if card["undone"]:
            log("仍未作答：" + "、".join(card["undone"][:20]))

    # 只有确认全部作答才交卷；有未答就留着让人处理，绝不交白卷
    stats["submitted"] = False
    if stats.get("left", 1) == 0:
        stats["submitted"] = submit_paper(win, log)
    else:
        log(f"还有 {stats.get('left', 0)} 题没答完，不自动交卷")
    return stats
