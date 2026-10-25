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

from api.exam import ExamQuestion, ask_ai

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


def _js(win, script: str):
    try:
        return win.evaluate_js(script)
    except Exception:
        return None


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
    _js(win, "(function(){var b=document.querySelector('div.answerIcon');"
             "if(b)b.click();return 'ok';})()")
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
    ok = _js(win, js) == "ok"
    if ok:
        wait_ready(win)
    return ok


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


def remember(q, info, keys) -> None:
    """把这道题和答案记进题库。

    存答案原文而不是字母：这场考试的选项是被打乱过的，
    同一个字母换个场次就指向别的选项，只有文本能对上。
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
                          answer_text, source="exam")
    except Exception:
        pass


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
        # 已经答过的也记进题库，不然之前答的全白答
        if info["answered"] in options:
            remember(q, info, [info["answered"]])
        log(f"  [{prog}] 已有答案 {info['answered']!r}")
        return "already"

    if not options:
        log(f"  [{prog}] 没解析到选项")
        return "fail"

    keys, answer = [], ""
    for _ in range(2):
        answer = ask_ai(q) or ""
        keys = pick_keys(info["options"], answer)
        if keys:
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
            remember(q, info, keys)
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
        if not undone:
            stats["left"] = 0
            return stats

        for num in undone:
            if _t.time() > deadline:
                log("时间到，本轮中止")
                break
            if not goto(win, num):
                log(f"  [{num}] 跳不过去")
                stats["fail"] += 1
                continue
            status = solve_current(win, log)
            if status == "ok":
                stats["ok"] += 1
            elif status == "fail":
                stats["fail"] += 1

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
