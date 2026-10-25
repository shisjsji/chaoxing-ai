"""读取服务器上收到的反馈与报错日志。

在服务器上直接运行：
    python3 read_feedback.py              # 最近 30 条
    python3 read_feedback.py 100          # 最近 100 条
    python3 read_feedback.py 50 error     # 只看 error 类型
    python3 read_feedback.py 50 '' 2      # 只看最近 2 天

放在 /opt/apps/chaoxing-lib/ 下与 shared.db 同目录使用。
"""

import os
import sqlite3
import sys
import time

DB = os.environ.get("LIB_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "data", "shared.db"))


def main() -> int:
    limit = 30
    kind = ""
    days = 0
    args = [a for a in sys.argv[1:] if a != ""]
    if args:
        try:
            limit = max(1, int(args[0]))
        except ValueError:
            pass
    if len(args) > 1:
        kind = args[1]
    if len(args) > 2:
        try:
            days = int(args[2])
        except ValueError:
            days = 0

    if not os.path.exists(DB):
        print(f"数据库不存在: {DB}")
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM feedback"
        params: list = []
        conds = []
        if kind:
            conds.append("kind=?")
            params.append(kind)
        if days > 0:
            conds.append("created_at>?")
            params.append(time.time() - days * 86400)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    finally:
        conn.close()

    print(f"反馈总数 {total}，本次显示 {len(rows)} 条")
    for r in rows:
        when = time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))
        print("-" * 60)
        print(f"[{r['id']}] {when}  {r['kind']:<8} v{r['version']:<7} "
              f"{r['module']:<18} dev={r['device']}")
        msg = (r["message"] or "").strip()
        if msg:
            for line in msg.splitlines():
                print(f"   {line}")
        ctx = (r["context"] or "").strip()
        if ctx:
            print("   --- context ---")
            for line in ctx.splitlines()[:40]:
                print(f"   {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())