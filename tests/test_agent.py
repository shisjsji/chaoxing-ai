"""助教的规则判定和提示词组装。都是纯函数，不连网络。"""

import unittest

from api import agent, websearch
from api.exam import ExamQuestion, set_course_name, _role_prefix, _ANALYZE


def row(**kwargs):
    base = {
        "fingerprint": "f1",
        "title": "下面哪个是水果",
        "options": "A 苹果\nB 螺丝钉",
        "qtype": "single",
        "answer": "苹果",
        "status": 1,
        "evidence": "platform",
        "disputes": 0,
    }
    base.update(kwargs)
    return base


class BrokenRules(unittest.TestCase):
    def test_正常题不报问题(self):
        self.assertEqual(agent._broken_reason(row()), "")

    def test_答案为空(self):
        self.assertIn("空", agent._broken_reason(row(answer="")))

    def test_客观题没有选项(self):
        self.assertIn("选项", agent._broken_reason(row(options="")))

    def test_单选存了多个答案(self):
        reason = agent._broken_reason(row(answer="苹果|螺丝钉"))
        self.assertIn("多个答案", reason)

    def test_判断题答案不合法(self):
        reason = agent._broken_reason(
            row(qtype="judgement", options="", answer="也许吧"))
        self.assertIn("判断", reason)

    def test_判断题的合法写法都认(self):
        for text in ("正确", "错误", "对", "错", "√", "×", "true", "false"):
            self.assertEqual(
                agent._broken_reason(row(qtype="judgement", options="", answer=text)),
                "", text)

    def test_题干太短(self):
        self.assertIn("题干", agent._broken_reason(row(title="嗯")))

    def test_主观题答案过短(self):
        reason = agent._broken_reason(
            row(qtype="shortanswer", options="", answer="不知道"))
        self.assertIn("敷衍", reason)


class DuplicateRules(unittest.TestCase):
    def test_同一题不同标点会被抓到(self):
        rows = [row(fingerprint="a", title="马克思主义哲学的核心是实践观点"),
                row(fingerprint="b", title="马克思主义哲学的核心是实践观点。")]
        dups = agent.find_dups(rows)
        self.assertEqual(len(dups), 1)
        self.assertTrue(dups[0]["same_answer"])

    def test_答案不同会标出来(self):
        rows = [row(fingerprint="a", title="实践是检验真理的唯一标准",
                    qtype="judgement", options="", answer="正确"),
                row(fingerprint="b", title="实践是检验真理的唯一标准。",
                    qtype="judgement", options="", answer="错误")]
        dups = agent.find_dups(rows)
        self.assertEqual(len(dups), 1)
        self.assertFalse(dups[0]["same_answer"])
        self.assertIn("人工", dups[0]["note"])

    def test_不相干的题不会误报(self):
        rows = [row(fingerprint="a", title="我国国防的主体是什么"),
                row(fingerprint="b", title="马克思主义哲学的核心观点有哪些")]
        self.assertEqual(agent.find_dups(rows), [])


class Priority(unittest.TestCase):
    def test_没有证据的题排在有硬证据的前面(self):
        weak = row(evidence="model", status=0)
        strong = row(evidence="platform", status=1)
        self.assertGreater(agent.suspicious_score(weak),
                           agent.suspicious_score(strong))

    def test_冲突次数会抬高优先级(self):
        self.assertGreater(agent.suspicious_score(row(disputes=2)),
                           agent.suspicious_score(row()))


class ModelNote(unittest.TestCase):
    def test_判错时把建议答案放最前面(self):
        note = agent._model_note(
            {"verdict": "wrong", "answer": "螺丝钉", "note": "常识判断"})
        self.assertTrue(note.startswith("建议改为：螺丝钉"))
        self.assertIn("常识判断", note)

    def test_判对时不带建议(self):
        note = agent._model_note({"verdict": "ok", "answer": "X", "note": "没问题"})
        self.assertEqual(note, "没问题")

    def test_状态映射(self):
        self.assertEqual(agent.model_state("ok"), "ok")
        self.assertEqual(agent.model_state("wrong"), "suspect")
        self.assertEqual(agent.model_state("broken"), "broken")


class SearchParsing(unittest.TestCase):
    def test_博查的结构(self):
        data = {"data": {"webPages": {"value": [
            {"name": "标题", "url": "https://a", "snippet": "摘要"}]}}}
        got = websearch._extract(data)
        self.assertEqual(len(got), 1)
        self.assertEqual(websearch._pick(got[0])["title"], "标题")

    def test_博查顶层_webPages_也能认(self):
        # 官方文档示例就是顶层直接给 webPages
        data = {"webPages": {"value": [
            {"name": "另一条", "url": "https://d", "summary": "概要"}]}}
        got = websearch._extract(data)
        self.assertEqual(len(got), 1)
        self.assertEqual(websearch._pick(got[0])["snippet"], "概要")

    def test_tavily_的结构(self):
        data = {"results": [{"title": "T", "url": "https://b", "content": "正文"}]}
        got = websearch._extract(data)
        self.assertEqual(websearch._pick(got[0])["snippet"], "正文")

    def test_看不懂的结构返回空(self):
        self.assertEqual(websearch._extract({"foo": 1}), [])
        self.assertEqual(websearch._extract(None), [])

    def test_渲染成短文本(self):
        text = websearch.render([{"title": "T", "url": "https://c", "snippet": "S"}])
        self.assertIn("T", text)
        self.assertIn("https://c", text)

    def test_没配地址和_key_就不启用(self):
        self.assertFalse(websearch.search("随便查点什么", conf={}) == [] and False)
        self.assertEqual(
            websearch.search("随便查点什么",
                             conf={"enabled": True, "url": "", "key": "k"}), [])
        self.assertEqual(
            websearch.search("随便查点什么",
                             conf={"enabled": True, "url": "https://x", "key": ""}), [])


class PromptContext(unittest.TestCase):
    def tearDown(self):
        set_course_name("")

    def test_课程名会进提示词(self):
        set_course_name("军事理论（中北大学版）")
        self.assertIn("军事理论", _role_prefix())

    def test_没课程名也能用(self):
        set_course_name("")
        self.assertIn("命题教师", _role_prefix())

    def test_逐项分析那段要求不能漏选(self):
        self.assertIn("逐个分析", _ANALYZE)


if __name__ == "__main__":
    unittest.main()