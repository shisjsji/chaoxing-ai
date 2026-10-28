"""助教：把题库理一遍。

分工是这样的——
本机能算的（答案为空、选项缺失、单选题给了多个答案、两道题一模一样），
规则直接算，一分钱 token 不花，也绝不会算错；
规则判断不了的（答案对不对），才交给模型。
送模型之前按「可疑程度」排序，先问最没底的那些。

审查只产出结论，不改库。改答案、标错、上报共享库，都要使用者自己点。
自动改库这种事一旦出错，事后很难查是哪一步坏的。
"""

import difflib
import json
import re
from configparser import ConfigParser
from pathlib import Path

from api.library import _norm as norm_text
from api.library import Library, normalize_answer

CONFIG_PATH = Path("config.ini")
SECRET_PATH = Path("secret.dat")

# 外部给的证据，比模型自己说的一致要可信得多
HARD_EVIDENCE = {"platform", "quiz", "shared", "manual"}

# 一次送多少道题给模型
BATCH = 8

_TRUE_WORDS = {"正确", "对", "是", "true", "t", "yes", "y", "1", "√"}
_FALSE_WORDS = {"错误", "错", "否", "false", "f", "no", "n", "0", "×", "x"}


def _secret(name: str) -> str:
    if not SECRET_PATH.exists():
        return ""
    try:
        data = json.loads(SECRET_PATH.read_text(encoding="utf-8"))
        from api.dpapi import unprotect

        return unprotect(data.get(name, "") or "")
    except Exception:
        return ""


def conf() -> dict:
    """助教用的模型。没单独配就沿用主配置，省得让使用者填两遍。"""
    parser = ConfigParser(interpolation=None)
    if CONFIG_PATH.exists():
        try:
            parser.read(CONFIG_PATH, encoding="utf-8")
        except Exception:
            parser = ConfigParser(interpolation=None)
    agent = parser["agent"] if "agent" in parser else {}
    tiku = parser["tiku"] if "tiku" in parser else {}

    endpoint = (str(agent.get("url", "") or "").strip()
                or str(tiku.get("endpoint", "") or "").strip())
    model = (str(agent.get("model", "") or "").strip()
             or str(tiku.get("model", "") or "").strip())
    key = str(agent.get("key", "") or "").strip() or _secret("agent_key")
    own = bool(str(agent.get("url", "") or "").strip()
               or str(agent.get("key", "") or "").strip())
    if not key:
        key = str(tiku.get("key", "") or "").strip() or _secret("api_key")
    return {"endpoint": endpoint.rstrip("/"), "model": model, "key": key,
            "own": own,
            "ready": bool(endpoint and model and key)}


# ---------- 本地规则 ----------

def _answer_words(text: str) -> set:
    low = (text or "").strip().lower()
    words = set()
    if low in _TRUE_WORDS:
        words.add("true")
    if low in _FALSE_WORDS:
        words.add("false")
    return words


def _broken_reason(row: dict) -> str:
    """这条记录本身是不是坏的 —— 不用模型也能确定的。"""
    title = (row.get("title") or "").strip()
    answer = (row.get("answer") or "").strip()
    qtype = row.get("qtype") or ""
    options = row.get("options") or ""

    if len(norm_text(title)) < 4:
        return "题干为空或过短"
    if not answer:
        return "答案是空的"

    if qtype in ("single", "multiple"):
        lines = [ln for ln in options.split("\n") if ln.strip()]
        if len(lines) < 2:
            return "客观题没有解析出选项"
        if qtype == "single":
            picked = normalize_answer(answer, qtype, options)
            if picked and len(picked.split("|")) > 1:
                return "单选题却存了多个答案"
    elif qtype == "judgement":
        if not _answer_words(answer):
            return "判断题的答案不是「正确/错误」"
    else:
        if len(norm_text(answer)) < 4:
            return "主观题答案敷衍得过分"
    return ""


def find_broken(rows: list) -> list:
    out = []
    for row in rows:
        reason = _broken_reason(row)
        if reason:
            out.append({"fingerprint": row["fingerprint"], "title": row.get("title", ""),
                        "note": reason})
    return out


def _same_title(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_dups(rows: list, threshold: float = 0.88) -> list:
    """找出题干几乎一样的题。

    先按字符集重合度粗筛（很便宜），只有过筛的才去算精确相似度，
    不然 700 条两两比会慢到没人愿意点第二次。
    """
    items = []
    for row in rows:
        text = norm_text(row.get("title") or "")
        if len(text) >= 6:
            items.append((row, text, set(text)))

    out = []
    seen = set()
    for i in range(len(items)):
        row_a, text_a, set_a = items[i]
        for j in range(i + 1, len(items)):
            row_b, text_b, set_b = items[j]
            key = (row_a["fingerprint"], row_b["fingerprint"])
            if key in seen:
                continue
            longest = max(len(text_a), len(text_b))
            if abs(len(text_a) - len(text_b)) > longest * 0.45:
                continue
            inter = len(set_a & set_b)
            union = len(set_a) + len(set_b) - inter
            if not union or inter / union < 0.82:
                continue
            score = _same_title(text_a, text_b)
            if score < threshold:
                continue
            seen.add(key)
            same_answer = (normalize_answer(row_a.get("answer", ""), row_a.get("qtype", ""),
                                            row_a.get("options", ""))
                           == normalize_answer(row_b.get("answer", ""), row_b.get("qtype", ""),
                                               row_b.get("options", "")))
            out.append({
                "fingerprint": row_a["fingerprint"],
                "fingerprint_b": row_b["fingerprint"],
                "title": row_a.get("title", ""),
                "title_b": row_b.get("title", ""),
                "score": round(score, 3),
                "note": "两处答案一致" if same_answer else "两处答案不一致，必须人工定夺",
                "same_answer": same_answer,
            })
            if len(out) >= 200:
                return out
    return out


def suspicious_score(row: dict) -> int:
    """越没底的分越高，排队问模型时排前面。"""
    score = 0
    evidence = (row.get("evidence") or "").strip()
    if evidence in HARD_EVIDENCE:
        score -= 40
    elif evidence == "ai_agree":
        score -= 10
    elif evidence == "model" or not evidence:
        score += 25
    score += min(int(row.get("disputes") or 0), 3) * 20
    if not row.get("status"):
        score += 10
    return score


# ---------- 模型 ----------

_PROMPT = """你是题库质检员。下面是从一个学习通题库里取出的题目，
每题带着它当前的答案和答案的依据。

请逐题判断：
1. 答案在学科事实上是否成立；
2. 如果不成立，给出你认为正确的答案（按原选项原文作答，不要给字母）；
3. 如果题目或选项本身残缺、答非所问，判为 broken。

依据的可信程度从高到低：platform、quiz、shared、manual、ai_agree、model。
model 和 ai_agree 都只是模型自己的判断，不能当作事实，仍要独立核验。

只输出下面这个 JSON，不要任何其他内容：
{"items":[{"id":1,"verdict":"ok","answer":"","reason":"一句话理由"}]}
verdict 只能是 ok / wrong / broken 三者之一。"""


def _client(conf_: dict):
    from openai import OpenAI

    return OpenAI(base_url=conf_["endpoint"], api_key=conf_["key"], timeout=120)


def ask_batch(client, conf_: dict, batch: list) -> list:
    lines = []
    for idx, row in enumerate(batch, 1):
        qtype = row.get("qtype") or "未知题型"
        lines.append(f"[{idx}]（{qtype}）{row.get('title', '')}")
        options = (row.get("options") or "").strip()
        if options:
            lines.append("选项：\n" + options)
        lines.append(f"当前答案：{row.get('answer', '')}"
                     f"（依据：{row.get('evidence') or '没有记录'}）")
        lines.append("")

    completion = client.chat.completions.create(
        model=conf_["model"],
        messages=[
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
    )
    content = completion.choices[0].message.content or ""
    matched = re.search(r"\{.*\}", content, re.S)
    if not matched:
        return []
    try:
        data = json.loads(matched.group(0))
    except Exception:
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []

    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(batch)):
            continue
        verdict = str(item.get("verdict") or "").lower()
        if verdict not in ("ok", "wrong", "broken"):
            verdict = "suspect"
        out.append({
            "fingerprint": batch[idx - 1]["fingerprint"],
            "title": batch[idx - 1].get("title", ""),
            "current": batch[idx - 1].get("answer", ""),
            "verdict": verdict,
            "answer": str(item.get("answer") or "").strip(),
            "note": str(item.get("reason") or "").strip()[:300],
        })
    return out


def model_state(verdict: str) -> str:
    if verdict == "ok":
        return "ok"
    if verdict == "broken":
        return "broken"
    return "suspect"


_VERIFY_PROMPT = """下面是一道题的作答结论，以及刚从网上查到的资料。

请判断这些资料是否支持原结论：
- 资料明确否掉原答案 → 给正确作答；
- 资料和原答案一致 → verdict 给 ok；
- 资料互相矛盾、或和题目对不上 → verdict 给 broken，并在 reason 里说明。

只输出 JSON：{"verdict":"ok","answer":"","reason":""}
verdict 只能是 ok / wrong / broken。"""


def _search_context(row: dict) -> str:
    try:
        from api import websearch
    except Exception:
        return ""
    if not websearch.configured():
        return ""
    title = (row.get("title") or "").strip()
    if len(title) < 6:
        return ""
    try:
        results = websearch.search(title[:60], 3)
    except Exception:
        return ""
    return websearch.render(results)


def verify_online(client, conf_: dict, batch: list, conclusions: list,
                  log=None, limit: int = 10) -> list:
    """对模型判错的题，先搜资料再复核一遍。

    搜索按次计费，所以只挑判错的、而且限量。搜不到资料的直接跳过。
    """
    log = log or (lambda _m: None)
    by_fp = {c["fingerprint"]: c for c in conclusions}
    targets = [c for c in conclusions if c.get("verdict") == "wrong"][:max(0, limit)]
    if not targets:
        return conclusions

    rows = {r["fingerprint"]: r for r in batch}
    checked = 0
    for item in targets:
        row = rows.get(item["fingerprint"])
        if not row:
            continue
        ctx = _search_context(row)
        if not ctx:
            continue
        question = (f"题目：{row.get('title', '')}\n"
                    f"选项：\n{row.get('options', '')}\n"
                    f"题库里的答案：{item.get('current', '')}\n"
                    f"刚才的判断：答案错误，建议改为「{item.get('answer', '')}」\n\n"
                    f"网上查到的资料：\n{ctx}")
        try:
            completion = client.chat.completions.create(
                model=conf_["model"],
                messages=[
                    {"role": "system", "content": _VERIFY_PROMPT},
                    {"role": "user", "content": question},
                ],
            )
            content = completion.choices[0].message.content or ""
            matched = re.search(r"\{.*\}", content, re.S)
            if not matched:
                continue
            data = json.loads(matched.group(0))
        except Exception as exc:
            log(f"  联网复核失败，跳过一题：{exc}")
            continue
        verdict = str(data.get("verdict") or "").lower()
        if verdict not in ("ok", "wrong", "broken"):
            continue
        newer = dict(item)
        newer["verdict"] = verdict
        newer["answer"] = str(data.get("answer") or "").strip()
        note = str(data.get("reason") or "").strip()
        newer["note"] = ("已联网核实：" + note) if note else "已联网核实"
        by_fp[item["fingerprint"]] = newer
        checked += 1
        log(f"  联网核实 {checked} 题")

    if checked:
        log(f"联网复核完成，实际查了 {checked} 题")
    return list(by_fp.values())


def _model_note(item: dict) -> str:
    """模型判为错误时，把建议答案放在最前面，界面好识别。"""
    note = (item.get("note") or "").strip()
    if item.get("verdict") == "wrong" and item.get("answer"):
        head = "建议改为：" + item["answer"]
        return head + ("。" + note if note else "")
    return note


# ---------- 串起来 ----------

def audit(max_items: int = 80, use_model: bool = True, log=None,
          only_unaudited: bool = False, lib=None, use_search: bool = True) -> dict:
    """跑一轮完整审查。返回汇总和明细。

    lib 不传就用当前目录下的题库；测试时可以直接塞一个临时库进来。
    """
    log = log or (lambda _m: None)
    lib = lib or Library()
    rows = lib.rows_for_audit()
    if only_unaudited:
        done = {r["fingerprint"] for r in lib.audit_list("", limit=100000)
                if r.get("audit_state")}
        rows = [r for r in rows if r["fingerprint"] not in done]

    if not rows:
        return {"ok": True, "total": 0, "broken": [], "dups": [], "model": [],
                "note": "题库是空的"}

    log(f"题库共 {len(rows)} 条，先跑本地规则")

    broken = find_broken(rows)
    broken_fps = {b["fingerprint"] for b in broken}
    dups = find_dups([r for r in rows if r["fingerprint"] not in broken_fps])
    dup_fps = set()
    for d in dups:
        if not d.get("same_answer"):
            continue
        dup_fps.add(d["fingerprint"])
        dup_fps.add(d["fingerprint_b"])

    writes = [(b["fingerprint"], "broken", b["note"]) for b in broken]
    for d in dups:
        writes.append((d["fingerprint"], "dup", d["note"]))
        # 答案一致的两条都标上，处理起来可以随便留一条；
        # 答案冲突的只标一条，等人工定夺，不能两条都动
        if d.get("same_answer"):
            writes.append((d["fingerprint_b"], "dup",
                           "和另一条重复，答案一致"))
    log(f"本地规则：{len(broken)} 条数据有问题，{len(dups)} 组疑似重复")

    model_results = []
    conf_ = conf()
    if use_model:
        if not conf_["ready"]:
            log("助教还没配好模型，只能做本地规则检查")
        else:
            skip = broken_fps | dup_fps
            pool = [r for r in rows if r["fingerprint"] not in skip]
            pool.sort(key=suspicious_score, reverse=True)
            pool = pool[:max(0, int(max_items))]
            if not pool:
                log("没有需要问模型的题了")
            else:
                log(f"挑出 {len(pool)} 道最没底的题，分批问模型")
                try:
                    client = _client(conf_)
                except Exception as exc:
                    log(f"模型客户端不可用：{exc}")
                    client = None
                if client is not None:
                    for start in range(0, len(pool), BATCH):
                        chunk = pool[start:start + BATCH]
                        try:
                            got = ask_batch(client, conf_, chunk)
                        except Exception as exc:
                            log(f"第 {start // BATCH + 1} 批出问题，先跳过：{exc}")
                            continue
                        model_results.extend(got)
                        log(f"  已核对 {min(start + BATCH, len(pool))}/{len(pool)} 道")

                    # 判错的题再去网上找依据复核一遍，避免模型一句「错了」就采信
                    if use_search and model_results:
                        try:
                            from api import websearch

                            if websearch.configured():
                                log("开始联网复核判错的题")
                                model_results = verify_online(
                                    client, conf_, pool, model_results, log=log)
                            else:
                                log("没配联网搜索，跳过复核")
                        except Exception as exc:
                            log(f"联网复核跳过：{exc}")

                    writes += [(g["fingerprint"], model_state(g["verdict"]),
                                _model_note(g)) for g in model_results]

    if writes:
        lib.set_audit(writes)
    stats = lib.audit_stats()
    log(f"本轮完成：确认 {sum(1 for w in writes if w[1] == 'ok')} 道，"
        f"存疑 {sum(1 for w in writes if w[1] in ('suspect', 'broken'))} 道，"
        f"重复 {sum(1 for w in writes if w[1] == 'dup')} 道")
    return {"ok": True, "total": len(rows), "broken": broken, "dups": dups,
            "model": model_results, "stats": stats}


def upload_candidates(min_count: int = 1, lib=None) -> list:
    """能上报共享库的题：审查通过 + 原始依据本身是硬证据。"""
    lib = lib or Library()
    rows = lib.audit_list("ok", limit=100000)
    out = []
    for row in rows:
        evidence = (row.get("evidence") or "").strip()
        if evidence not in HARD_EVIDENCE:
            continue
        if not row.get("answer"):
            continue
        out.append(row)
    return out if len(out) >= min_count else []