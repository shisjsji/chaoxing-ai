"""题库记忆与判定机制：答案归一化、证据、冲突降级。"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.library import Library, fingerprint, normalize_answer

OPTIONS = "A. 端午节\nB. 清明节\nC. 中秋节\nD. 元宵节"


class NormalizeAnswerTest(unittest.TestCase):
    def test_judgement_variants_collapse(self):
        for text in ("正确", "对", "是", "true", "True", "√", "对的"):
            self.assertEqual(normalize_answer(text, "judgement"), "T", text)
        for text in ("错误", "错", "否", "false", "×", "错的", "不正确"):
            self.assertEqual(normalize_answer(text, "judgement"), "F", text)

    def test_letter_and_text_map_to_same_key(self):
        by_letter = normalize_answer("A", "single", OPTIONS)
        by_text = normalize_answer("端午节", "single", OPTIONS)
        self.assertEqual(by_letter, by_text)
        self.assertEqual(by_letter, "端午节")

    def test_multiple_sorted_and_text_mix(self):
        a = normalize_answer("CA", "multiple", OPTIONS)
        b = normalize_answer("中秋节、端午节", "multiple", OPTIONS)
        c = normalize_answer("A、C", "multiple", OPTIONS)
        self.assertEqual(a, c)
        self.assertEqual(a, b)

    def test_judgement_wrapped_letter_text(self):
        # 考试路径会把选项原文存进来，出现过「B 错」这种混写
        self.assertEqual(normalize_answer("B 错", "judgement"), "F")
        self.assertEqual(normalize_answer("A 正确", "judgement"), "T")

    def test_fingerprint_ignores_option_order(self):
        a = fingerprint("中华人民共和国的首都是？", "A. 北京\nB. 上海", "single")
        b = fingerprint("中华人民共和国的首都是？", "B. 上海\nA. 北京", "single")
        self.assertEqual(a, b)


class LibraryWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "library.db")
        self.lib = Library(self.db)
        self.fp = fingerprint("首都是？", OPTIONS, "single")

    def tearDown(self):
        self.tmp.cleanup()

    def _row(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT status,answer,answer_key,evidence,disputes FROM questions"
                " WHERE fingerprint=?", (self.fp,)
            ).fetchone()
        finally:
            conn.close()

    def test_new_answer_is_pending(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京")
        self.assertEqual(self._row()[0], 0)
        self.assertIsNone(self.lib.lookup(self.fp))

    def test_confirmed_is_reused_and_not_overwritten(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京", status=1,
                        evidence="quiz")
        self.assertEqual(self.lib.lookup(self.fp), "北京")
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "上海")
        status, answer, _key, evidence, disputes = self._row()
        self.assertEqual(status, 1)
        self.assertEqual(answer, "北京")
        self.assertEqual(evidence, "quiz")
        self.assertEqual(disputes, 1)

    def test_two_disagreements_demote(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京", status=1,
                        evidence="quiz")
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "上海")
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "广州")
        status, _answer, _key, evidence, _disputes = self._row()
        self.assertEqual(status, 2)
        self.assertEqual(evidence, "conflict")
        self.assertIsNone(self.lib.lookup(self.fp))

    def test_same_key_different_wording_does_not_conflict(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "A", status=1,
                        evidence="shared")
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "端午节")
        self.assertEqual(self._row()[0], 1)
        self.assertEqual(self._row()[4], 0)

    def test_confirm_many_sets_evidence(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京")
        self.lib.confirm_many([self.fp], evidence="platform")
        self.assertEqual(self._row()[0], 1)
        self.assertEqual(self._row()[3], "platform")

    def test_manual_answer_wins(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京")
        self.lib.set_answer(self.fp, "上海")
        self.assertEqual(self.lib.lookup(self.fp), "上海")
        self.assertEqual(self._row()[3], "manual")

    def test_reject_drops_from_reuse(self):
        self.lib.upsert(self.fp, "首都是？", OPTIONS, "single", "北京", status=1)
        self.lib.reject(self.fp)
        self.assertEqual(self._row()[0], 2)
        self.assertIsNone(self.lib.lookup(self.fp))


class MigrationTest(unittest.TestCase):
    def test_old_database_gets_new_columns(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE questions (fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL,"
            " options TEXT DEFAULT '', qtype TEXT DEFAULT '', answer TEXT NOT NULL,"
            " source TEXT DEFAULT 'ai', status INTEGER DEFAULT 0, hits INTEGER DEFAULT 0,"
            " created_at REAL, updated_at REAL);"
        )
        fp = fingerprint("首都是？", OPTIONS, "single")
        conn.execute(
            "INSERT INTO questions(fingerprint,title,options,qtype,answer,status)"
            " VALUES(?,?,?,?,?,1)", (fp, "首都是？", OPTIONS, "single", "A")
        )
        conn.commit()
        conn.close()

        lib = Library(db)
        row = lib.search()[0]
        self.assertEqual(row["fingerprint"], fp)
        self.assertEqual(row["answer"], "A")
        conn = sqlite3.connect(db)
        key = conn.execute("SELECT answer_key FROM questions WHERE fingerprint=?",
                           (fp,)).fetchone()[0]
        conn.close()
        self.assertEqual(key, "端午节")  # 字母已按选项还原成原文


if __name__ == "__main__":
    unittest.main()