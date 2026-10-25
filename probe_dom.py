# -*- coding: utf-8 -*-
"""在内嵌考试窗口里执行 JS，用来探测答题页结构。

用法：
    python probe_dom.py            读当前题目结构
    python probe_dom.py "<js>"     执行任意 JS
"""
import json
import sys

import requests

BASE = "http://127.0.0.1:5000"


def run_js(js: str):
    r = requests.post(BASE + "/api/exam/eval", json={"js": js}, timeout=25)
    data = r.json()
    if not data.get("ok"):
        print("执行失败:", data.get("error"))
        return None
    return data.get("result")


READ_JS = r"""
(function () {
  var out = {url: location.href.slice(0, 120)};
  var node = document.querySelector('div.questionLi.singleQuesId')
          || document.querySelector('div.questionWrap')
          || document.querySelector('.mark_table');
  out.hasNode = !!node;
  if (!node) return JSON.stringify(out);

  out.qid = node.getAttribute('data') || '';

  var h3 = node.querySelector('h3.mark_name') || node.querySelector('div.Zy_TItle');
  out.title = h3 ? h3.innerText.replace(/\s+/g, ' ').trim().slice(0, 160) : '';

  var tit = node.querySelector('h2.type_tit');
  out.typeText = tit ? tit.innerText.replace(/\s+/g, ' ').trim().slice(0, 40) : '';

  // 选项：抓所有带 input 的选项容器
  out.options = [];
  var boxes = node.querySelectorAll('div.answerList, div.addChoice, label');
  for (var i = 0; i < boxes.length && out.options.length < 12; i++) {
    var b = boxes[i];
    var inp = b.querySelector('input');
    if (!inp) continue;
    out.options.push({
      cls: (b.className || '').slice(0, 40),
      name: inp.getAttribute('name') || '',
      value: inp.getAttribute('value') || '',
      type: inp.getAttribute('type') || '',
      text: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60)
    });
  }

  // 按钮
  out.buttons = [];
  var bs = document.querySelectorAll('button, a');
  for (var j = 0; j < bs.length; j++) {
    var t = (bs[j].innerText || '').replace(/\s+/g, ' ').trim();
    if (t && /下一题|上一题|提交|交卷|保存/.test(t)) {
      out.buttons.push({text: t.slice(0, 12), id: bs[j].id || '',
                        cls: (bs[j].className || '').slice(0, 40)});
    }
  }

  // 当前进度
  var prog = document.body.innerText.match(/(\d+)\s*\/\s*(\d+)/);
  out.progress = prog ? prog[0] : '';
  return JSON.stringify(out);
})()
"""


def main():
    if len(sys.argv) > 1:
        result = run_js(sys.argv[1])
        print(result)
        return
    raw = run_js(READ_JS)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except Exception:
        print("原始返回:", raw)
        return
    print(f"URL      : {data.get('url')}")
    print(f"进度      : {data.get('progress')}")
    print(f"找到题目框: {data.get('hasNode')}  qid={data.get('qid')}")
    print(f"题型      : {data.get('typeText')}")
    print(f"题干      : {data.get('title')}")
    print(f"选项 {len(data.get('options', []))} 个:")
    for o in data.get("options", []):
        print(f"   [{o['type']}] name={o['name']!r} value={o['value']!r} "
              f"cls={o['cls']!r} text={o['text']!r}")
    print(f"按钮: {data.get('buttons')}")


if __name__ == "__main__":
    main()