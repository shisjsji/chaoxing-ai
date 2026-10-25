# -*- coding: utf-8 -*-
"""共享题库服务端的入库闸门测试。

客户端令牌是内置共享的，任何人都能提取。所以服务端不能只靠「单用户限额」，
否则换个随机标识就能无限灌库。
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "server"))

VALID_HASH = "a" * 32
OTHER_HASH = "b" * 32


def _load_service(**env):
    for key, value in env.items():
        os.environ[key] = str(value)
    import library_service
    return importlib.reload(library_service)


class SubmitGuardTest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.svc = _load_service(LIB_DB=self.db, LIB_TOKEN="t0ken",
                                 LIB_DAILY_LIMIT="3", LIB_GLOBAL_DAILY_LIMIT="100",
                                 LIB_PER_QUESTION_CAP="2")
        self.client = self.svc.app.test_client()

    def tearDown(self):
        self.svc._lock = self.svc.threading.Lock()
        try:
            os.remove(self.db)
        except OSError:
            pass

    def _post(self, items, user=VALID_HASH):
        return self.client.post(
            "/api/v1/submit",
            json={"user": user, "items": items},
            headers={"X-Token": "t0ken"},
        )

    def _item(self, fp="fp1", answer="A"):
        return {"fingerprint": fp, "title": "题", "options": "A a\nB b",
                "qtype": "single", "answer": answer, "confidence": "high"}

    def test_rejects_malformed_user_hash(self):
        """随机串当标识要被拒，不能靠换标识绕开限额。"""
        r = self._post([self._item()], user="hello-world")
        self.assertEqual(r.status_code, 400)

    def test_rejects_short_hash(self):
        r = self._post([self._item()], user="abc123")
        self.assertEqual(r.status_code, 400)

    def test_accepts_valid_hash(self):
        r = self._post([self._item()])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["accepted"], 1)

    def test_per_user_daily_limit(self):
        """同一标识超出每日额度后不再接受。"""
        for _ in range(3):
            self._post([self._item(fp="fpX")])
        r = self._post([self._item(fp="fpX")])
        self.assertEqual(r.get_json()["accepted"], 0)

    def test_per_question_cap(self):
        """同一道题累计到上限后，换新标识也不再收。"""
        self._post([self._item(fp="same", answer="A")], user=VALID_HASH)
        self._post([self._item(fp="same", answer="A")], user=OTHER_HASH)
        r = self._post([self._item(fp="same", answer="B")], user="c" * 32)
        self.assertEqual(r.get_json()["accepted"], 0,
                         "单题达到上限后不该再计入")

    def test_answer_kept_when_cap_reached(self):
        """达到上限后，原有答案不能被后来的提交改写。"""
        self._post([self._item(fp="same", answer="A")], user=VALID_HASH)
        self._post([self._item(fp="same", answer="A")], user=OTHER_HASH)
        self._post([self._item(fp="same", answer="WRONG")], user="d" * 32)
        conn = self.svc._conn()
        try:
            row = conn.execute(
                "SELECT answer FROM answers WHERE fingerprint='same'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "A")


if __name__ == "__main__":
    unittest.main()