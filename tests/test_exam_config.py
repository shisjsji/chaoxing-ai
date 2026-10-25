# -*- coding: utf-8 -*-
"""考试模块读取模型配置的缓存失效测试。

改完设置里的模型或密钥，考试要立刻用新的，不能等程序重启。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.exam as exam  # noqa: E402


class AiConfCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "config.ini"
        self._stamp = 1_700_000_000.0
        self._write("m1", "k1")
        self._patch = mock.patch.object(exam, "CONFIG_PATH", self.cfg)
        self._patch.start()
        exam._AI_CONF_CACHE = {}
        exam._AI_CONF_MTIME = -1.0

    def tearDown(self):
        self._patch.stop()
        exam._AI_CONF_CACHE = {}
        exam._AI_CONF_MTIME = -1.0

    def _write(self, model, key):
        self.cfg.write_text(
            f"[tiku]\nendpoint = https://api.example.com\nkey = {key}\nmodel = {model}\n",
            encoding="utf-8")
        # mtime 每次严格递增，避免时间精度不足导致「文件变了但 mtime 相同」
        self._stamp += 60.0
        os.utime(self.cfg, (self._stamp, self._stamp))

    def test_reads_current_values(self):
        conf = exam._ai_conf()
        self.assertEqual(conf["model"], "m1")
        self.assertEqual(conf["key"], "k1")

    def test_cache_is_used_without_change(self):
        first = exam._ai_conf()
        second = exam._ai_conf()
        self.assertIs(first, second, "文件没变时应复用缓存")

    def test_reloads_after_config_changed(self):
        self.assertEqual(exam._ai_conf()["model"], "m1")
        self._write("m2", "k2")
        conf = exam._ai_conf()
        self.assertEqual(conf["model"], "m2", "配置改过后必须重新读取")
        self.assertEqual(conf["key"], "k2")

    def test_missing_config_returns_empty(self):
        self.cfg.unlink()
        self.assertEqual(exam._ai_conf(), {})


if __name__ == "__main__":
    unittest.main()