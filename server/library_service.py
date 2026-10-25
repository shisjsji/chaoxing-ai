"""共享题库服务端。

职责很窄：收「整卷测验全对」验证过的题目，按题干指纹去重，同题按票数决定答案。
不碰任何学习通账号，客户端只上传题目指纹、题干、选项、答案和一个匿名的用户标识哈希。

运行：python library_service.py
环境变量：
  LIB_DB     数据库路径，默认 shared.db
  LIB_TOKEN  访问令牌，客户端需在 X-Token 头里带上
  LIB_PORT   监听端口，默认 5001
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import time

from flask import Flask, jsonify, request

DB_PATH = os.environ.get("LIB_DB", "shared.db")
RELEASE_DIR = os.environ.get("LIB_RELEASE_DIR", "/opt/apps/chaoxing-lib/releases")
TOKEN = os.environ.get("LIB_TOKEN", "change-me")
PORT = int(os.environ.get("LIB_PORT", "5001"))

# 每个用户标识每天最多提交多少条，防止刷库
DAILY_LIMIT = int(os.environ.get("LIB_DAILY_LIMIT", "2000"))
# 客户端令牌是内置共享的，所以单用户限额挡不住伪造标识，真正的闸门按全局算
GLOBAL_DAILY_LIMIT = int(os.environ.get("LIB_GLOBAL_DAILY_LIMIT", "20000"))
# 同一道题最多累计多少条提交，超过就不再接受，防止单题被灌票
PER_QUESTION_CAP = int(os.environ.get("LIB_PER_QUESTION_CAP", "20"))
# 反馈/日志上报：每台设备每天上限，防止有人拿它刷库
FEEDBACK_DAILY_LIMIT = int(os.environ.get("LIB_FEEDBACK_DAILY_LIMIT", "300"))
# 作者查看上报内容的独立令牌；不配置就不开放这个接口
ADMIN_TOKEN = os.environ.get("LIB_ADMIN_TOKEN", "")

app = Flask(__name__)
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    options     TEXT DEFAULT '',
    qtype       TEXT DEFAULT '',
    answer      TEXT NOT NULL,
    votes       INTEGER DEFAULT 0,
    status      INTEGER DEFAULT 1,
    created_at  REAL,
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS submissions (
    fingerprint TEXT,
    user_hash   TEXT,
    answer      TEXT,
    confidence  TEXT DEFAULT 'medium',
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT DEFAULT 'log',
    version     TEXT DEFAULT '',
    module      TEXT DEFAULT '',
    message     TEXT DEFAULT '',
    context     TEXT DEFAULT '',
    device      TEXT DEFAULT '',
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_fb_created ON feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_fb_device ON feedback(device, created_at);
CREATE INDEX IF NOT EXISTS idx_sub_user ON submissions(user_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_sub_fp ON submissions(fingerprint);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.executescript(_SCHEMA)
    # 旧库补列
    cols = [row[1] for row in conn.execute("PRAGMA table_info(submissions)").fetchall()]
    if "confidence" not in cols:
        conn.execute("ALTER TABLE submissions ADD COLUMN confidence TEXT DEFAULT 'medium'")
        conn.commit()
    return conn


def _norm(text: str) -> str:
    return re.sub(r"[\s，。！？；：,.!?;:（）()\[\]【】\"“”‘’\-_/\\|]", "", text or "").lower()


def _fingerprint(title: str, options: str, qtype: str) -> str:
    opts = []
    for line in (options or "").split("\n"):
        cleaned = _norm(re.sub(r"^[A-Za-z]\s*[.、:：)?）]?\s*", "", line).strip())
        if cleaned:
            opts.append(cleaned)
    opts.sort()
    payload = _norm(title) + "|" + "|".join(opts) + "|" + (qtype or "")
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _authorized() -> bool:
    return request.headers.get("X-Token", "") == TOKEN


@app.post("/api/v1/lookup")
def lookup():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    payload = request.get_json(silent=True) or {}
    fps = [str(x) for x in payload.get("fingerprints", [])][:500]
    if not fps:
        return jsonify(ok=True, answers={})

    placeholders = ",".join("?" * len(fps))
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                f"SELECT fingerprint, answer FROM answers "
                f"WHERE status=1 AND fingerprint IN ({placeholders})",
                fps,
            ).fetchall()
        finally:
            conn.close()
    return jsonify(ok=True, answers={fp: ans for fp, ans in rows})


@app.post("/api/v1/submit")
def submit():
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    payload = request.get_json(silent=True) or {}
    user_hash = str(payload.get("user", ""))[:64].strip().lower()
    items = payload.get("items", [])
    if not user_hash or not isinstance(items, list):
        return jsonify(ok=False, error="bad request"), 400
    # 客户端固定用 sha256 前 32 位。格式不对的直接拒掉，省得拿随机串刷限额。
    if not re.fullmatch(r"[0-9a-f]{32}", user_hash):
        return jsonify(ok=False, error="bad user hash"), 400

    now = time.time()
    accepted = 0
    with _lock:
        conn = _conn()
        try:
            today = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE created_at>?", (now - 86400,)
            ).fetchone()[0]
            if today >= GLOBAL_DAILY_LIMIT:
                return jsonify(ok=False, error="今日提交量已达上限，请明天再试"), 429

            used = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE user_hash=? AND created_at>?",
                (user_hash, now - 86400),
            ).fetchone()[0]
            room = max(0, DAILY_LIMIT - used)
            global_room = GLOBAL_DAILY_LIMIT - today

            for item in items[:200]:
                if room <= 0 or global_room <= 0:
                    break
                title = str(item.get("title", ""))[:2000]
                options = str(item.get("options", ""))[:4000]
                qtype = str(item.get("qtype", ""))[:20]
                answer = str(item.get("answer", ""))[:200]
                confidence = str(item.get("confidence", "medium")).lower()
                if confidence not in ("high", "medium", "low"):
                    confidence = "medium"
                fp = str(item.get("fingerprint", "")) or _fingerprint(title, options, qtype)
                if not fp or not answer:
                    continue

                seen = conn.execute(
                    "SELECT COUNT(*) FROM submissions WHERE fingerprint=?", (fp,)
                ).fetchone()[0]
                if seen >= PER_QUESTION_CAP:
                    # 这题票数已经够了，再收只会让统计变慢，也可能被拿来刷
                    continue

                conn.execute(
                    "INSERT INTO submissions(fingerprint,user_hash,answer,confidence,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (fp, user_hash, answer, confidence, now),
                )

                # 按置信度加权统计
                weights: dict[str, int] = {}
                for ans, conf in conn.execute(
                    "SELECT answer, confidence FROM submissions WHERE fingerprint=?", (fp,)
                ).fetchall():
                    weights[ans] = weights.get(ans, 0) + (2 if conf == "high" else 1)
                if not weights:
                    continue
                ordered = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
                best_answer, best_votes = ordered[0]
                second = ordered[1][1] if len(ordered) > 1 else 0
                # 平票才算有争议；多数票（含 2:1）直接采信
                disputed = second > 0 and best_votes == second
                status = 0 if disputed else 1

                conn.execute(
                    "INSERT INTO answers(fingerprint,title,options,qtype,answer,votes,status,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(fingerprint) DO UPDATE SET "
                    "answer=excluded.answer, votes=excluded.votes, status=excluded.status, "
                    "updated_at=excluded.updated_at",
                    (fp, title, options, qtype, best_answer, best_votes, status, now, now),
                )
                room -= 1
                global_room -= 1
                accepted += 1
            conn.commit()
        finally:
            conn.close()

    return jsonify(ok=True, accepted=accepted)


@app.get("/api/v1/stats")
def stats():
    with _lock:
        conn = _conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
            usable = conn.execute("SELECT COUNT(*) FROM answers WHERE status=1").fetchone()[0]
            disputed = conn.execute("SELECT COUNT(*) FROM answers WHERE status=0").fetchone()[0]
        finally:
            conn.close()
    return jsonify(ok=True, total=total, usable=usable, disputed=disputed)


@app.post("/api/v1/feedback")
def submit_feedback():
    """接收客户端的日志、报错和使用反馈。"""
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401

    payload = request.get_json(silent=True) or {}
    kind = str(payload.get("kind", "log"))[:20]
    version = str(payload.get("version", ""))[:32]
    module = str(payload.get("module", ""))[:40]
    message = str(payload.get("message", ""))[:4000]
    context = str(payload.get("context", ""))[:4000]
    device = str(payload.get("device", ""))[:40]
    if not message and not context:
        return jsonify(ok=False, error="empty"), 400

    now = time.time()
    with _lock:
        conn = _conn()
        try:
            used = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE device=? AND created_at>?",
                (device, now - 86400)).fetchone()[0]
            if used >= FEEDBACK_DAILY_LIMIT:
                return jsonify(ok=False, error="今日上报次数已达上限"), 429
            conn.execute(
                "INSERT INTO feedback(kind,version,module,message,context,device,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (kind, version, module, message, context, device, now))
            conn.commit()
        finally:
            conn.close()
    return jsonify(ok=True)


@app.get("/api/v1/feedback")
def list_feedback():
    """作者查看上报内容。需要独立的管理令牌，普通客户端令牌看不了。"""
    if not ADMIN_TOKEN or request.headers.get("X-Admin-Token", "") != ADMIN_TOKEN:
        return jsonify(ok=False, error="unauthorized"), 401
    try:
        limit = min(200, max(1, int(request.args.get("limit", "50"))))
    except ValueError:
        limit = 50
    kind = request.args.get("kind", "").strip()
    sql = "SELECT id,kind,version,module,message,context,device,created_at FROM feedback"
    params = []
    if kind:
        sql += " WHERE kind=?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        finally:
            conn.close()
    return jsonify(ok=True, total=total, items=[
        {"id": r[0], "kind": r[1], "version": r[2], "module": r[3],
         "message": r[4], "context": r[5], "device": r[6],
         "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[7]))}
        for r in rows])


@app.get("/api/v1/latest")
def latest():
    """返回最新版本信息，供客户端检查更新。"""
    path = os.path.join(RELEASE_DIR, "version.json")
    if not os.path.exists(path):
        return jsonify(ok=False, error="尚未发布任何版本"), 404
    try:
        with open(path, encoding="utf-8-sig") as fp:
            data = json.load(fp)
        return jsonify(ok=True, **data)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 500


@app.get("/api/v1/list")
def list_entries():
    """列出服务器题库中的题目，供客户端展示。"""
    if not _authorized():
        return jsonify(ok=False, error="unauthorized"), 401
    try:
        limit = min(200, max(1, int(request.args.get("limit", "50"))))
    except ValueError:
        limit = 50
    keyword = request.args.get("q", "").strip()

    sql = "SELECT fingerprint,title,qtype,answer,votes,status,updated_at FROM answers"
    params: list = []
    if keyword:
        sql += " WHERE title LIKE ?"
        params.append("%" + keyword + "%")
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        finally:
            conn.close()
    return jsonify(ok=True, total=total, items=[
        {"fingerprint": r[0], "title": r[1], "qtype": r[2], "answer": r[3],
         "votes": r[4], "status": r[5], "updated_at": r[6]}
        for r in rows
    ])


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, ts=time.time())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)