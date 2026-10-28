# -*- coding: utf-8 -*-
"""断网时任务必须干净停下，不能卡在「运行中」。

线上故障：域名解析失败时进度上报抛 ConnectionError，worker 线程直接死掉，
但它没有调用 task_done()，run() 里的 task_queue.join() 就永远等下去 ——
界面显示「运行中」，实际上什么都没在跑。
"""

import threading
import unittest
from unittest import mock

from requests import RequestException

import main as cxmain


def _task(i):
    return cxmain.ChapterTask(index=i, point={"title": f"点{i}"}, course={"title": "课程"})


class NetworkDownTest(unittest.TestCase):
    def test_run_stops_instead_of_hanging(self):
        proc = cxmain.JobProcessor(None, [_task(i) for i in range(10)], {"jobs": 2, "speed": 1})
        calls = {"n": 0}

        def boom(*_args, **_kwargs):
            calls["n"] += 1
            raise RequestException("Failed to resolve 'mooc1.chaoxing.com'")

        # 卡死的话这个测试会自己超时，所以用一个守护线程跑，并设上限
        result = {}

        def runner():
            try:
                with mock.patch.object(cxmain, "process_chapter", boom):
                    proc.run()
                result["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                result["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=30)

        self.assertFalse(thread.is_alive(), "run() 卡住了，超时未返回")
        self.assertIsInstance(result.get("error"), cxmain.NetworkDownError)
        # 连续 8 次就判为断网，不该把 10 个任务全跑完
        self.assertLess(calls["n"], 10)

    def test_single_failure_does_not_stop(self):
        """偶发一次网络抖动不该中止整轮，任务应记为失败并继续。"""
        proc = cxmain.JobProcessor(None, [_task(0)], {"jobs": 1, "speed": 1})
        seq = {"n": 0}

        def flaky(*_args, **_kwargs):
            seq["n"] += 1
            if seq["n"] == 1:
                raise RequestException("临时抖动")
            return cxmain.ChapterResult.SUCCESS

        with mock.patch.object(cxmain, "process_chapter", flaky):
            proc.run()  # 不该抛异常
        self.assertFalse(proc.stopped)


if __name__ == "__main__":
    unittest.main()