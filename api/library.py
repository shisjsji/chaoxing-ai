"""本地题库。

按「题干 + 排序后的选项」做指纹存进 SQLite，同一道题不同排版或选项顺序打乱都能对上。

状态三档：0 待验证、1 已确认、2 已判错。只有已确认的答案会被直接复用。

「已确认」不是随便给的，每条答案都记着依据（evidence）：
  platform  平台判分满分，且拿到了每题的正确答案（最硬）
  quiz      整卷满分，平台判分通过
  ai_agree  同一题换提示词独立问两次，两次答案一致
  shared    共享题库里别人验证过的
  manual    用户手工确认或改过的，永不被自动覆盖
只有上面这些才算数；单次模型答案永远是待验证（model）。
"""

import hashlib
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

DB_NAME = "library.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    options     TEXT DEFAULT '',
    qtype       TEXT DEFAULT '',
    answer      TEXT NOT NULL,
    answer_key  TEXT DEFAULT '',
    source      TEXT DEFAULT 'ai',
    evidence    TEXT DEFAULT '',
    status      INTEGER DEFAULT 0,
    disputes    INTEGER DEFAULT 0,
    hits        INTEGER DEFAULT 0,
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_updated ON questions(updated_at);
"""

_MIGRATIONS = (
    ("answer_key", "ALTER TABLE questions ADD COLUMN answer_key TEXT DEFAULT ''"),
    ("evidence", "ALTER TABLE questions ADD COLUMN evidence TEXT DEFAULT ''"),
    ("disputes", "ALTER TABLE questions ADD COLUMN disputes INTEGER DEFAULT 0"),
    # 助教审查结论。ok=确认无误 / suspect=有疑问 / broken=数据本身有问题 /
    # dup=和其它题重复，空字符串表示还没查过。
    ("audit_state", "ALTER TABLE questions ADD COLUMN audit_state TEXT DEFAULT ''"),
    ("audit_note", "ALTER TABLE questions ADD COLUMN audit_note TEXT DEFAULT ''"),
    ("audited_at", "ALTER TABLE questions ADD COLUMN audited_at REAL DEFAULT 0"),
)

_OPTION_PREFIX = re.compile(r"^[A-Za-z]\s*[.、:：)?）]?\s*")
_NOISE = re.compile(r"[\s，。！？；：,.!?;:（）()\[\]【】\"“”‘’\-_/\\|]")

# 判断题的各种写法。先判否，因为「不正确」里含「正确」
_NEG = ("不正确", "不对", "不是", "错误", "错", "×", "否")
_POS = ("正确", "对", "是", "√")
_EXACT_T = {"true", "t", "yes", "y", "1", "正确", "对", "是", "√", "对的", "正确的"}
_EXACT_F = {"false", "f", "no", "n", "0", "错误", "错", "否", "×", "x", "错的"}

_lock = threading.Lock()

# 客观题才值得双答比对；主观题两次措辞必然不同，比了没意义
OBJECTIVE_QTYPES = {"single", "multiple", "judgement"}


def _norm(text: str) -> str:
    return _NOISE.sub("", text or "").lower()


def _option_texts(options: str) -> list:
    out = []
    for line in (options or "").split("\n"):
        cleaned = _OPTION_PREFIX.sub("", line.strip())
        if cleaned:
            out.append(cleaned)
    return out


def _letters_only(text: str) -> list:
    """整句只有选项字母时返回字母列表，否则空。"""
    cleaned = re.sub(r"^(答案|answer)\s*[:：]?", "", (text or "").strip(), flags=re.I)
    if re.fullmatch(r"[A-Fa-f][\s,、，/.]*([A-Fa-f][\s,、，/.]*)*", cleaned):
        return [ch.upper() for ch in re.findall(r"[A-Fa-f]", cleaned)]
    return []


def _as_bool(text: str) -> str:
    low = (text or "").strip().lower()
    if low in _EXACT_T:
        return "T"
    if low in _EXACT_F:
        return "F"
    for word in _NEG:
        if word in text:
            return "F"
    for word in _POS:
        if word in text:
            return "T"
    return ""


def normalize_answer(answer: str, qtype: str = "", options: str = "") -> str:
    """把同一个答案的不同写法归一成同一个 key，用于比较和投票。

    不改变入库的原文，只用于判断「两次答案是不是同一个」。
    选字母和选文本会被映射到同样的 key（有选项时按字母取回原文）。
    """
    text = (answer or "").strip()
    if not text:
        return ""
    kind = (qtype or "").lower()

    if kind in ("judgement", "judge"):
        return _as_bool(text) or _norm(text)

    if kind in ("single", "multiple", "multi"):
        letters = _letters_only(text)
        if letters:
            opts = _option_texts(options)
            if opts:
                picked = []
                for ch in letters:
                    idx = ord(ch) - 65
                    if 0 <= idx < len(opts):
                        picked.append(_norm(opts[idx]))
                if picked:
                    return "|".join(sorted(set(picked)))
            return "".join(sorted(set(letters)))
        parts = [p for p in re.split(r"[、,，;；/|\n]+", text) if p.strip()]
        return "|".join(sorted({_norm(p) for p in parts}))

    return _norm(text)


def fingerprint(title: str, options: str = "", qtype: str = "") -> str:
    """题干 + 排序后的选项文本 → 稳定指纹。选项顺序不同不影响。"""
    opts = []
    for line in (options or "").split("\n"):
        cleaned = _norm(_OPTION_PREFIX.sub("", line).strip())
        if cleaned:
            opts.append(cleaned)
    opts.sort()
    payload = _norm(title) + "|" + "|".join(opts) + "|" + (qtype or "")
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class Library:
    def __init__(self, path: str | Path = DB_NAME):
        self.path = str(path)
        self._ensure()

    def _ensure(self) -> None:
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executescript(_SCHEMA)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
                added_key = False
                added_evidence = False
                for name, ddl in _MIGRATIONS:
                    if name not in cols:
                        conn.execute(ddl)
                        added_key = added_key or name == "answer_key"
                        added_evidence = added_evidence or name == "evidence"
                if added_evidence:
                    # 老数据补一个大致依据，题库页才不至于每题都空着
                    conn.execute(
                        "UPDATE questions SET evidence = CASE"
                        " WHEN source='share' THEN 'shared'"
                        " WHEN status=1 THEN 'quiz'"
                        " ELSE 'model' END"
                        " WHERE evidence='' OR evidence IS NULL"
                    )
                if added_key:
                    # 老库没有 answer_key，按现有答案补一遍，冲突判定才有依据
                    rows = conn.execute(
                        "SELECT fingerprint,answer,qtype,options FROM questions"
                    ).fetchall()
                    for fp, answer, qtype, options in rows:
                        conn.execute(
                            "UPDATE questions SET answer_key=? WHERE fingerprint=?",
                            (normalize_answer(answer, qtype, options), fp),
                        )
                conn.commit()
            finally:
                conn.close()

    def lookup(self, fp: str, confirmed_only: bool = True) -> Optional[str]:
        """查答案。confirmed_only=True 时只返回已验证过的。"""
        sql = "SELECT answer FROM questions WHERE fingerprint=?"
        params: list = [fp]
        if confirmed_only:
            sql += " AND status=1"
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute(sql, params).fetchone()
                if row:
                    conn.execute(
                        "UPDATE questions SET hits=hits+1 WHERE fingerprint=?", (fp,)
                    )
                    conn.commit()
                    return row[0]
                return None
            finally:
                conn.close()

    def upsert(self, fp: str, title: str, options: str, qtype: str, answer: str,
               source: str = "ai", status: int = 0, evidence: str = "") -> None:
        """写入一条答案。

        已确认的答案不会被后续答案覆盖；但如果新答案和它不一致，会累积冲突次数，
        连续两次对不上就降级为「已判错」，不让可疑答案一直挂着已确认。
        """
        now = time.time()
        key = normalize_answer(answer, qtype, options)
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute(
                    "SELECT status,answer_key,disputes FROM questions WHERE fingerprint=?",
                    (fp,),
                ).fetchone()
                if row:
                    cur_status, cur_key, disputes = row[0], row[1] or "", row[2] or 0
                    if cur_status == 1:
                        if cur_key == key:
                            conn.execute(
                                "UPDATE questions SET updated_at=? WHERE fingerprint=?",
                                (now, fp),
                            )
                        else:
                            disputes += 1
                            if disputes >= 2:
                                conn.execute(
                                    "UPDATE questions SET status=2, disputes=?, evidence='conflict',"
                                    " updated_at=? WHERE fingerprint=?",
                                    (disputes, now, fp),
                                )
                            else:
                                conn.execute(
                                    "UPDATE questions SET disputes=?, updated_at=? WHERE fingerprint=?",
                                    (disputes, now, fp),
                                )
                    else:
                        conn.execute(
                            "UPDATE questions SET answer=?, answer_key=?, options=?, qtype=?,"
                            " status=?, evidence=?, updated_at=? WHERE fingerprint=?",
                            (answer, key, options, qtype, status, evidence, now, fp),
                        )
                else:
                    conn.execute(
                        "INSERT INTO questions(fingerprint,title,options,qtype,answer,answer_key,"
                        "source,evidence,status,disputes,created_at,updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,0,?,?)",
                        (fp, title, options, qtype, answer, key, source, evidence,
                         status, now, now),
                    )
                conn.commit()
            finally:
                conn.close()

    def confirm_many(self, fps: list, evidence: str = "quiz") -> int:
        """整卷全对时把这些题目标记为已确认。"""
        if not fps:
            return 0
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executemany(
                    "UPDATE questions SET status=1, evidence=?, disputes=0, updated_at=?"
                    " WHERE fingerprint=?",
                    [(evidence, now, fp) for fp in fps],
                )
                conn.commit()
                return len(fps)
            finally:
                conn.close()

    def mark_wrong(self, fps: list) -> int:
        """确认答错：不再复用，直到被正确答案覆盖。"""
        if not fps:
            return 0
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executemany(
                    "UPDATE questions SET status=2, evidence='platform_wrong', updated_at=?"
                    " WHERE fingerprint=? AND status<>1",
                    [(now, fp) for fp in fps],
                )
                conn.commit()
                return len(fps)
            finally:
                conn.close()

    def force_answer(self, fp: str, title: str, options: str, qtype: str, answer: str,
                     source: str = "platform", evidence: str = "platform") -> None:
        """平台公布的正确答案：直接覆盖，优先级高于任何模型答案。"""
        now = time.time()
        key = normalize_answer(answer, qtype, options)
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.execute(
                    "INSERT INTO questions(fingerprint,title,options,qtype,answer,answer_key,"
                    "source,evidence,status,disputes,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,1,0,?,?)"
                    " ON CONFLICT(fingerprint) DO UPDATE SET answer=excluded.answer,"
                    " answer_key=excluded.answer_key, options=excluded.options,"
                    " qtype=excluded.qtype, status=1, evidence=excluded.evidence,"
                    " disputes=0, updated_at=excluded.updated_at"
                    # 用户手工确认/改过的答案不被自动覆盖（见文件头说明）
                    " WHERE coalesce(questions.evidence,'') <> 'manual'",
                    (fp, title, options, qtype, answer, key, source, evidence, now, now),
                )
                conn.commit()
            finally:
                conn.close()

    def confirm(self, fp: str, evidence: str = "manual") -> None:
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.execute(
                    "UPDATE questions SET status=1, evidence=?, disputes=0, updated_at=?"
                    " WHERE fingerprint=?",
                    (evidence, now, fp),
                )
                conn.commit()
            finally:
                conn.close()

    def reject(self, fp: str) -> None:
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.execute(
                    "UPDATE questions SET status=2, evidence='manual_wrong', updated_at=?"
                    " WHERE fingerprint=?",
                    (now, fp),
                )
                conn.commit()
            finally:
                conn.close()

    def set_answer(self, fp: str, answer: str, evidence: str = "manual") -> None:
        """用户手工改答案：改完直接算已确认，且这种答案不会被自动覆盖。"""
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute(
                    "SELECT qtype,options FROM questions WHERE fingerprint=?", (fp,)
                ).fetchone()
                if not row:
                    return
                key = normalize_answer(answer, row[0], row[1])
                conn.execute(
                    "UPDATE questions SET answer=?, answer_key=?, status=1, evidence=?,"
                    " disputes=0, updated_at=? WHERE fingerprint=?",
                    (answer, key, evidence, now, fp),
                )
                conn.commit()
            finally:
                conn.close()

    def stats(self) -> dict:
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM questions GROUP BY status"
                ).fetchall()
                total = sum(c for _, c in rows)
                by = {s: c for s, c in rows}
                return {
                    "total": total,
                    "confirmed": by.get(1, 0),
                    "pending": by.get(0, 0),
                    "wrong": by.get(2, 0),
                }
            finally:
                conn.close()

    def rows_for_audit(self, limit: int = 0) -> list:
        """取全部题目给助教检查。limit=0 表示不限。"""
        sql = ("SELECT fingerprint,title,options,qtype,answer,status,evidence,"
               "disputes,source FROM questions")
        params: list = []
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        keys = ("fingerprint", "title", "options", "qtype", "answer", "status",
                "evidence", "disputes", "source")
        out = []
        for r in rows:
            item = dict(zip(keys, r))
            item["evidence"] = item["evidence"] or ""
            item["disputes"] = item["disputes"] or 0
            out.append(item)
        return out

    def set_audit(self, items: list) -> int:
        """批量写审查结论。items 是 [(fingerprint, state, note)]。"""
        if not items:
            return 0
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executemany(
                    "UPDATE questions SET audit_state=?, audit_note=?, audited_at=?"
                    " WHERE fingerprint=?",
                    [(st, (note or "")[:500], now, fp) for fp, st, note in items],
                )
                conn.commit()
                return len(items)
            finally:
                conn.close()

    def audit_stats(self) -> dict:
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                rows = conn.execute(
                    "SELECT audit_state, COUNT(*) FROM questions GROUP BY audit_state"
                ).fetchall()
            finally:
                conn.close()
        by = {(s or ""): c for s, c in rows}
        return {
            "total": sum(by.values()),
            "audited": sum(c for s, c in by.items() if s),
            "ok": by.get("ok", 0),
            "suspect": by.get("suspect", 0),
            "broken": by.get("broken", 0),
            "dup": by.get("dup", 0),
            "none": by.get("", 0),
        }

    def audit_list(self, state: str = "", limit: int = 200) -> list:
        sql = ("SELECT fingerprint,title,options,qtype,answer,status,evidence,"
               "audit_state,audit_note FROM questions")
        params: list = []
        if state:
            sql += " WHERE audit_state=?"
            params.append(state)
        sql += " ORDER BY audited_at DESC LIMIT ?"
        params.append(int(limit))
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        keys = ("fingerprint", "title", "options", "qtype", "answer", "status",
                "evidence", "audit_state", "audit_note")
        return [dict(zip(keys, r)) for r in rows]

    def search(self, keyword: str = "", limit: int = 200) -> list:
        sql = ("SELECT fingerprint,title,qtype,answer,status,hits,updated_at,evidence,"
               "disputes FROM questions")
        params: list = []
        if keyword:
            sql += " WHERE title LIKE ?"
            params.append("%" + keyword + "%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [
            {"fingerprint": r[0], "title": r[1], "qtype": r[2], "answer": r[3],
             "status": r[4], "hits": r[5], "updated_at": r[6], "evidence": r[7] or "",
             "disputes": r[8] or 0}
            for r in rows
        ]

    def delete(self, fp: str) -> None:
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.execute("DELETE FROM questions WHERE fingerprint=?", (fp,))
                conn.commit()
            finally:
                conn.close()