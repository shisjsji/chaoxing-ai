"""本地题库。

按「题干 + 排序后的选项」做指纹存进 SQLite，同一道题不同排版或选项顺序打乱都能对上。
状态三档：0 待验证（AI 刚答的）、1 已确认（整卷全对验证过）、2 已判错。
只有已确认的答案会被直接复用，待验证的要重新问模型。
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
    source      TEXT DEFAULT 'ai',
    status      INTEGER DEFAULT 0,
    hits        INTEGER DEFAULT 0,
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_updated ON questions(updated_at);
"""

_OPTION_PREFIX = re.compile(r"^[A-Za-z]\s*[.、:：)?）]?\s*")
_NOISE = re.compile(r"[\s，。！？；：,.!?;:（）()\[\]【】\"“”‘’\-_/\\|]")

_lock = threading.Lock()


def _norm(text: str) -> str:
    return _NOISE.sub("", text or "").lower()


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
               source: str = "ai", status: int = 0) -> None:
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                exists = conn.execute(
                    "SELECT status FROM questions WHERE fingerprint=?", (fp,)
                ).fetchone()
                if exists:
                    if exists[0] == 1:
                        # 已确认的答案锁定, 不接受后续（可能是错的）答案覆盖
                        conn.execute(
                            "UPDATE questions SET updated_at=? WHERE fingerprint=?", (now, fp)
                        )
                    else:
                        conn.execute(
                            "UPDATE questions SET answer=?, options=?, qtype=?, updated_at=?,"
                            " status=? WHERE fingerprint=?",
                            (answer, options, qtype, now, status, fp),
                        )
                else:
                    conn.execute(
                        "INSERT INTO questions(fingerprint,title,options,qtype,answer,source,status,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (fp, title, options, qtype, answer, source, status, now, now),
                    )
                conn.commit()
            finally:
                conn.close()

    def confirm_many(self, fps: list[str]) -> int:
        """整卷全对时把这些题目标记为已确认。"""
        if not fps:
            return 0
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executemany(
                    "UPDATE questions SET status=1, updated_at=? WHERE fingerprint=?",
                    [(now, fp) for fp in fps],
                )
                conn.commit()
                return len(fps)
            finally:
                conn.close()

    def mark_wrong(self, fps: list[str]) -> int:
        """整卷未全对时把本卷题目标为待验证（不保留错误答案）。"""
        if not fps:
            return 0
        now = time.time()
        with _lock:
            conn = sqlite3.connect(self.path)
            try:
                conn.executemany(
                    "UPDATE questions SET status=2, updated_at=? WHERE fingerprint=? AND status=0",
                    [(now, fp) for fp in fps],
                )
                conn.commit()
                return len(fps)
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

    def search(self, keyword: str = "", limit: int = 200) -> list[dict]:
        sql = ("SELECT fingerprint,title,qtype,answer,status,hits,updated_at "
               "FROM questions")
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
             "status": r[4], "hits": r[5], "updated_at": r[6]}
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