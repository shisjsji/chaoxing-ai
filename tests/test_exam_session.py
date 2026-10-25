# -*- coding: utf-8 -*-
"""考试会话的协议细节测试。

两处曾经写错、会让整场考试白跑的地方：
1. 开始考试的接口返回 302，enc 签名参数在跳转地址里，不是 JSON；
2. 每拉一题、每提交一次，平台都会下发新的计时参数，不同步就会被判超时。
"""
import os
import sys
import unittest
from unittest import mock

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.exam import ExamError, ExamQuestion, ExamSession, _status_ok  # noqa: E402


def _bare_session():
    """跳过一次 __init__，只准备被测方法用到的属性。"""
    sess = ExamSession.__new__(ExamSession)
    sess.course_id = "c1"
    sess.class_id = "k1"
    sess.exam_id = "e1"
    sess.exam_answer_id = 42
    sess.cpi = "p1"
    sess.need_face = False
    sess.enc = ""
    sess.enc_remain_time = ""
    sess.remain_time = "0"
    sess.last_update_time = ""
    sess.uid = "u1"
    sess.enc_task = "task1"
    sess.title = ""
    return sess


class StatusOkTest(unittest.TestCase):
    def test_string_success(self):
        self.assertTrue(_status_ok({"status": "success"}))

    def test_bool_true(self):
        self.assertTrue(_status_ok({"status": True}))

    def test_numeric_one(self):
        self.assertTrue(_status_ok({"status": 1}))

    def test_string_fail_is_not_ok(self):
        """'fail' 是真值字符串，用 if not status 判断会误当成功。"""
        self.assertFalse(_status_ok({"status": "fail"}))

    def test_bool_false(self):
        self.assertFalse(_status_ok({"status": False}))

    def test_missing_status(self):
        self.assertFalse(_status_ok({}))


class StartTest(unittest.TestCase):
    def test_reads_enc_from_redirect(self):
        """302 才是开始成功，enc 在 Location 的查询串里。"""
        sess = _bare_session()
        resp = mock.Mock(status_code=302,
                         headers={"Location": "https://m/x?enc=ENC9&other=1"})
        sess.session = mock.Mock()
        sess.session.get.return_value = resp

        sess.start()

        self.assertEqual(sess.enc, "ENC9")
        self.assertFalse(sess.session.get.call_args.kwargs["allow_redirects"],
                         "必须关掉自动跟随，否则拿不到 Location")

    def test_200_raises_with_tip(self):
        """200 带提示页时是失败，要把原因说出来。"""
        sess = _bare_session()
        body = '<html><body><p class="blankTips">验证码错误！</p></body></html>'
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(status_code=200, text=body)

        with self.assertRaises(ExamError) as ctx:
            sess.start()
        self.assertIn("验证码错误", str(ctx.exception))

    def test_missing_enc_still_succeeds(self):
        """跳转地址里没有 enc 时不至于崩，照常继续（后面拉题会补上）。"""
        sess = _bare_session()
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            status_code=302, headers={"Location": "https://m/x?other=1"})
        sess.start()
        self.assertEqual(sess.enc, "")


class SyncParamsTest(unittest.TestCase):
    def test_sync_from_form(self):
        """拉题响应里的表单带四个计时参数，必须全部同步。"""
        sess = _bare_session()
        html = (
            '<form id="submitTest">'
            '<input id="enc" value="E1">'
            '<input id="encRemainTime" value="1800">'
            '<input id="remainTime" value="1795">'
            '<input id="encLastUpdateTime" value="1700000000000">'
            '</form>'
        )
        form = BeautifulSoup(html, "lxml").select_one("form#submitTest")
        sess._sync_from_form(form)

        self.assertEqual(sess.enc, "E1")
        self.assertEqual(sess.enc_remain_time, "1800")
        self.assertEqual(sess.remain_time, "1795")
        self.assertEqual(sess.last_update_time, "1700000000000")

    def test_sync_from_form_skips_missing(self):
        """表单缺字段时保留旧值，不要清空。"""
        sess = _bare_session()
        sess.enc = "OLD"
        form = BeautifulSoup('<form id="submitTest"><input id="enc" value="NEW"></form>',
                             "lxml").select_one("form#submitTest")
        sess._sync_from_form(form)
        self.assertEqual(sess.enc, "NEW")
        self.assertEqual(sess.remain_time, "0")

    def test_sync_from_data(self):
        """提交成功后 data 是「更新时间|剩余秒数|enc」三段。"""
        sess = _bare_session()
        sess._sync_from_data("1700000001000|1750|ENC2")
        self.assertEqual(sess.last_update_time, "1700000001000")
        self.assertEqual(sess.enc_remain_time, "1750")
        self.assertEqual(sess.enc, "ENC2")

    def test_sync_from_data_ignores_garbage(self):
        sess = _bare_session()
        sess.enc = "KEEP"
        sess._sync_from_data("")
        sess._sync_from_data(None)
        sess._sync_from_data("only|two")
        self.assertEqual(sess.enc, "KEEP")


class TimeoutMessageTest(unittest.TestCase):
    def test_timeout_message_is_translated(self):
        """提交返回「考试时间已用完」时要给出能看懂的提示。"""
        sess = _bare_session()
        sess.session = mock.Mock()
        sess.session.post.return_value = mock.Mock(
            json=lambda: {"status": "fail", "msg": "考试时间已用完，不允许提交答案"})
        question = ExamQuestion(index=0, qid="q1", qtype="single", title="题干",
                                options={"A": "A 甲", "B": "B 乙"},
                                type_code="0", type_name="单选题")
        with self.assertRaises(ExamError) as ctx:
            sess.submit(question, "A")
        self.assertIn("时间已用完", str(ctx.exception))


class AttachTest(unittest.TestCase):
    """接管已经开始（或进行中）的考试。"""

    def _page(self, form=True, tip=""):
        parts = ["<html><body>"]
        if tip:
            parts.append(f'<p class="blankTips">{tip}</p>')
        if form:
            parts.append(
                '<form id="submitTest">'
                '<input id="enc" value="ENC-ATTACH">'
                '<input id="encRemainTime" value="5400">'
                '<input id="remainTime" value="5395">'
                '<input id="encLastUpdateTime" value="1700000009999">'
                '</form>')
        parts.append("</body></html>")
        return "".join(parts)

    def test_reads_params_and_exam_answer_id(self):
        """接管时要从跳转地址取 examAnswerId，从页面取四个计时参数。"""
        sess = _bare_session()
        sess.exam_answer_id = None
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            url="https://m/exam/test/reVersionTestStartNew?examAnswerId=777&x=1",
            text=self._page())

        sess.attach()

        self.assertEqual(sess.exam_answer_id, 777)
        self.assertEqual(sess.enc, "ENC-ATTACH")
        self.assertEqual(sess.enc_remain_time, "5400")
        self.assertEqual(sess.remain_time, "5395")
        self.assertEqual(sess.last_update_time, "1700000009999")

    def test_exam_answer_id_from_hidden_field(self):
        """跳转地址里没有时，从隐藏域兜底。"""
        sess = _bare_session()
        sess.exam_answer_id = None
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            url="https://m/exam/test/reVersionTestStartNew?x=1",
            text=self._page().replace(
                "<form", '<input id="testUserRelationId" value="888"><form'))

        sess.attach()
        self.assertEqual(sess.exam_answer_id, 888)

    def test_no_form_raises_actionable_error(self):
        """没读到试卷页时要给出能照做的提示，而不是含糊报错。"""
        sess = _bare_session()
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            url="https://m/exam-ans/exam/phone/task-exam", text=self._page(form=False))

        with self.assertRaises(ExamError) as ctx:
            sess.attach()
        self.assertIn("接管", str(ctx.exception))

    def test_not_started_message(self):
        """考试还没开始时明确说清楚。"""
        sess = _bare_session()
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            url="https://m/x", text=self._page(form=False, tip="考试尚未开始"))

        with self.assertRaises(ExamError) as ctx:
            sess.attach()
        self.assertIn("尚未开始", str(ctx.exception))

    def test_missing_exam_answer_id_raises(self):
        """页面能解析但拿不到 examAnswerId 时不能硬着头皮往下走。"""
        sess = _bare_session()
        sess.exam_answer_id = None
        sess.session = mock.Mock()
        sess.session.get.return_value = mock.Mock(
            url="https://m/x?y=1", text=self._page())

        with self.assertRaises(ExamError) as ctx:
            sess.attach()
        self.assertIn("examAnswerId", str(ctx.exception))


class RunExamAttachTest(unittest.TestCase):
    def test_attach_skips_open_and_start(self):
        """attach 模式不能再调 open/start，否则会抢使用者的考试。"""
        sess = mock.Mock()
        sess.attach = mock.Mock()
        sess.open = mock.Mock()
        sess.start = mock.Mock()
        sess.fetch.side_effect = ExamError("卷尾")

        with mock.patch("api.exam.ExamSession", lambda *a, **k: sess), \
                mock.patch("api.exam.Library", lambda: mock.Mock()):
            from api.exam import run_exam
            run_exam(None, {"courseId": "1"}, "9", "enc", log=lambda *_: None,
                     attach=True, delay=1)

        sess.attach.assert_called_once()
        sess.open.assert_not_called()
        sess.start.assert_not_called()


class TitleCleanTest(unittest.TestCase):
    """题干里常被塞进题型标题、总分和序号，这些会污染题库指纹。"""

    def _title(self, raw, qid="1"):
        html = (f'<div><input name="questionId" value="{qid}">'
                f'<input name="type{qid}" value="0">'
                f'<div class="tit">{raw}</div></div>')
        from api.exam import parse_question
        return parse_question(BeautifulSoup(html, "lxml")).title

    def test_strips_type_and_total(self):
        self.assertEqual(
            self._title("单选题 （共40题，40.0分） 1. 吃粽子是哪个节日的习惯？"),
            "吃粽子是哪个节日的习惯？")

    def test_strips_nested_prefixes(self):
        self.assertEqual(
            self._title("一、 单选题 （共 40 题，40.0 分） 1. (单选题, 1.0 分) 劳动合同应当具备的条款？"),
            "劳动合同应当具备的条款？")

    def test_plain_title_untouched(self):
        self.assertEqual(self._title("吃粽子是哪个节日的习惯？"),
                         "吃粽子是哪个节日的习惯？")


class FormPayloadTest(unittest.TestCase):
    """提交时必须回传页面上的结构性字段，但不能把页面的 tempSave 带进去。"""

    PAGE = (
        '<form id="submitTest">'
        '<input name="paperId" value="210206138">'
        '<input name="examCreateUserId" value="596898319">'
        '<input name="openc" value="5aa848e4f8a474d1932d47e8883970c4">'
        '<input name="questionScore" value="1.0">'
        '<input name="score883531007" value="1.0">'
        '<input name="tempSave" value="false">'
        '<input name="enc" value="abc">'
        '<input name="answer883531007" value="">'
        '<input name="typeName883531007" value="单选题">'
        '</form>'
    )

    def test_keeps_structure_fields(self):
        from api.exam import form_payload
        f = form_payload(self.PAGE)
        self.assertEqual(f["paperId"], "210206138")
        self.assertEqual(f["examCreateUserId"], "596898319")
        self.assertEqual(f["openc"], "5aa848e4f8a474d1932d47e8883970c4")
        self.assertEqual(f["score883531007"], "1.0")

    def test_drops_controlled_fields(self):
        """tempSave=false 照抄会直接交卷，必须剔除由 submit 自己决定。"""
        from api.exam import form_payload
        f = form_payload(self.PAGE)
        for key in ("tempSave", "enc", "answer883531007", "typeName883531007"):
            self.assertNotIn(key, f)

    def test_empty_html(self):
        from api.exam import form_payload
        self.assertEqual(form_payload(""), {})


if __name__ == "__main__":
    unittest.main()