# -*- coding: utf-8 -*-
"""刷课答题的思考强度开关测试。

设置里能调思考强度，但要保证两件事：
1. 选了强度就真的透传给模型；
2. 模型不认这个参数时自动降级，不能因为一个选项让所有答题全挂。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.answer import AI  # noqa: E402


def _tiku(effort="", endpoint="https://api.deepseek.com", model="deepseek-chat"):
    """跳过 AI 的构造（会读配置、建连接），只准备被测方法要用的属性。"""
    inst = AI.__new__(AI)
    inst._conf = {"reasoning_effort": effort} if effort is not None else None
    inst.endpoint = endpoint
    inst.model = model
    return inst


class CompletionKwargsTest(unittest.TestCase):
    def test_effort_is_forwarded(self):
        k = _tiku("high")._completion_kwargs(model="m")
        self.assertEqual(k["reasoning_effort"], "high")
        self.assertNotIn("extra_body", k, "开了思考就不该再禁用 thinking")

    def test_medium_and_low(self):
        for level in ("low", "medium"):
            k = _tiku(level)._completion_kwargs(model="m")
            self.assertEqual(k["reasoning_effort"], level)

    def test_off_disables_thinking_on_v4(self):
        """没选强度时，DeepSeek V4 仍要关掉 thinking（否则 content 是空的）。"""
        k = _tiku("", model="deepseek-v4-chat")._completion_kwargs(model="m")
        self.assertEqual(k["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("reasoning_effort", k)

    def test_off_leaves_other_models_alone(self):
        k = _tiku("", model="deepseek-chat")._completion_kwargs(model="m")
        self.assertEqual(k, {"model": "m"})

    def test_invalid_effort_treated_as_off(self):
        k = _tiku("extreme", model="deepseek-chat")._completion_kwargs(model="m")
        self.assertNotIn("reasoning_effort", k)

    def test_missing_conf_does_not_crash(self):
        k = _tiku(None)._completion_kwargs(model="m")
        self.assertNotIn("reasoning_effort", k)


class CreateFallbackTest(unittest.TestCase):
    def _client(self, fail_first):
        client = mock.Mock()
        seen = []

        def create(**kwargs):
            seen.append(dict(kwargs))
            if fail_first and "reasoning_effort" in kwargs:
                raise TypeError("unexpected keyword argument 'reasoning_effort'")
            return "OK"

        client.chat.completions.create.side_effect = create
        return client, seen

    def test_retries_without_effort(self):
        """模型不认 reasoning_effort 时降级重试，答题不能因此全挂。"""
        inst = _tiku("high")
        client, seen = self._client(fail_first=True)
        result = inst._create(client, model="m", reasoning_effort="high")
        self.assertEqual(result, "OK")
        self.assertEqual(len(seen), 2)
        self.assertIn("reasoning_effort", seen[0])
        self.assertNotIn("reasoning_effort", seen[1])

    def test_no_retry_when_effort_absent(self):
        """没带这个参数就不要吞异常重试。"""
        inst = _tiku("")
        client = mock.Mock()
        client.chat.completions.create.side_effect = RuntimeError("网络炸了")
        with self.assertRaises(RuntimeError):
            inst._create(client, model="m")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_success_first_time(self):
        inst = _tiku("low")
        client, seen = self._client(fail_first=False)
        self.assertEqual(inst._create(client, model="m", reasoning_effort="low"), "OK")
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()