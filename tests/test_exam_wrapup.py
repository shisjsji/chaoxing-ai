# -*- coding: utf-8 -*-
"""考试收尾逻辑的回归测试。

重点是一条：卷上有主观题（程序不支持作答）时不能自动交卷，
否则那几道题直接记 0 分且不可更改。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.exam as exam  # noqa: E402


def _question(index, qtype="single", qid=None, saved=""):
    return exam.ExamQuestion(
        index=index,
        qid=qid or f"q{index}",
        qtype=qtype,
        title=f"第{index}题",
        options={"A": "A 选项一", "B": "B 选项二"},
        saved_answer=saved,
    )


class FakeSession:
    """替掉真正的 ExamSession，只关心流程走位。"""

    def __init__(self, questions):
        self.questions = questions
        self.final_submitted = False
        self.last_html = ""
        self.last_url = ""

    def open(self, reset=False):
        pass

    def start(self):
        pass

    def attach(self):
        pass

    def need_face(self):
        return False

    def fetch(self, index):
        if index >= len(self.questions):
            raise exam.ExamError("卷尾")
        return self.questions[index]

    def submit(self, question, answer, final=False, page_fields=None):
        if final:
            self.final_submitted = True
        return {"status": True}

    def final_submit(self):
        self.final_submitted = True
        return {"status": True}


class FakeLibrary:
    def lookup(self, fp, confirmed_only=True):
        return None

    def upsert(self, *args, **kwargs):
        pass


def _run(questions, answer="选项一", draft="这是主观题的参考答案"):
    fake = FakeSession(questions)
    submitted_flags = []
    with mock.patch.object(exam, "ExamSession", lambda *a, **k: fake), \
            mock.patch.object(exam, "Library", FakeLibrary), \
            mock.patch.object(exam, "resolve_answer", lambda q, lib: (answer, "test")), \
            mock.patch.object(exam, "ask_ai", lambda q: draft), \
            mock.patch.object(exam, "SUBJECTIVE_FILE", _draft_file()):
        count = exam.run_exam(
            None, {"courseId": "1", "clazzId": "2", "cpi": "3"}, "9", "enc",
            log=lambda *_: None, delay=1, max_questions=20,
            on_finish=submitted_flags.append,
        )
    return count, fake.final_submitted, submitted_flags


_DRAFT_DIR = tempfile.mkdtemp()


def _draft_file():
    return Path(_DRAFT_DIR) / "主观题参考答案.txt"


class ExamWrapUpTest(unittest.TestCase):
    def test_all_objective_submits_paper(self):
        """全是客观题：正常交卷。"""
        count, final, flags = _run([_question(0), _question(1)])
        self.assertEqual(count, 2)
        self.assertTrue(final)
        self.assertEqual(flags, [True])

    def test_subjective_blocks_submission(self):
        """有主观题没作答：不许自动交卷，并回调 False。"""
        count, final, flags = _run([_question(0), _question(1, qtype="essay")])
        self.assertEqual(count, 1)
        self.assertFalse(final, "有未作答的主观题时不该交卷")
        self.assertEqual(flags, [False])

    def test_subjective_draft_is_saved(self):
        """主观题的参考答案要写成文件，使用者照着抄就行。"""
        draft_file = _draft_file()
        if draft_file.exists():
            draft_file.unlink()
        _run([_question(0, qtype="essay")], draft="论述题参考答案正文")
        self.assertTrue(draft_file.exists(), "应生成主观题参考答案文件")
        text = draft_file.read_text(encoding="utf-8")
        self.assertIn("论述题参考答案正文", text)
        self.assertIn("第 1 题", text)

    def test_subjective_without_draft_still_blocks(self):
        """模型没给出答案时，仍然不许交卷，只是没有文件。"""
        draft_file = _draft_file()
        if draft_file.exists():
            draft_file.unlink()
        count, final, flags = _run([_question(0, qtype="essay")], draft="")
        self.assertFalse(final)
        self.assertEqual(flags, [False])
        self.assertFalse(draft_file.exists())

    def test_nothing_answered_blocks_submission(self):
        """一道都没答上（题库和模型都失败）：不许交白卷。"""
        count, final, flags = _run([_question(0)], answer="")
        self.assertEqual(count, 0)
        self.assertFalse(final)
        self.assertEqual(flags, [False])

    def test_saved_answers_still_submit(self):
        """卷面答案都已存在（saved_answer）时，虽然本次没新交题，也应当交卷。"""
        count, final, flags = _run([_question(0, saved="A"), _question(1, saved="B")])
        self.assertEqual(count, 0)
        self.assertTrue(final)
        self.assertEqual(flags, [True])


if __name__ == "__main__":
    unittest.main()