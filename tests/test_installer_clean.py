# -*- coding: utf-8 -*-
"""安装器清理逻辑的回归测试。

两条底线：
1. 重装/升级不能删掉用户的配置、cookie 和题库；
2. 安装目录选错了，也只碰程序自己的固定文件名，不清空别人的目录。
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer_ui as inst  # noqa: E402


def _make_payload(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("_internal/python312.dll", "NEW-DLL")
        zf.writestr("超星AI答题.exe", "NEW-EXE")
    return path


class InstallerCleanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.payload = _make_payload(self.tmp / "payload.zip")
        self.target = self.tmp / "超星AI答题"
        self.target.mkdir()
        (self.target / "_internal").mkdir()
        (self.target / "_internal" / "python312.dll").write_text("OLD-DLL", encoding="utf-8")
        (self.target / "超星AI答题.exe").write_text("OLD-EXE", encoding="utf-8")
        # 用户数据
        (self.target / "config.ini").write_text("USER-CONFIG", encoding="utf-8")
        (self.target / "library.db").write_text("USER-LIBRARY", encoding="utf-8")
        (self.target / "cookies.txt").write_text("USER-COOKIES", encoding="utf-8")

    def _install(self):
        with mock.patch.object(inst, "find_payload", lambda: self.payload), \
                mock.patch.object(inst, "make_shortcut"), \
                mock.patch.object(inst, "write_registry"):
            inst.install_files(self.target)

    def test_user_data_survives_reinstall(self):
        """重装后配置和题库必须还在。"""
        self._install()
        self.assertEqual((self.target / "config.ini").read_text(encoding="utf-8"), "USER-CONFIG")
        self.assertEqual((self.target / "library.db").read_text(encoding="utf-8"), "USER-LIBRARY")
        self.assertEqual((self.target / "cookies.txt").read_text(encoding="utf-8"), "USER-COOKIES")

    def test_program_files_are_replaced(self):
        """程序文件要被换成本次安装的版本。"""
        self._install()
        self.assertEqual(
            (self.target / "_internal" / "python312.dll").read_text(encoding="utf-8"), "NEW-DLL")
        self.assertEqual(
            (self.target / "超星AI答题.exe").read_text(encoding="utf-8"), "NEW-EXE")

    def test_other_files_untouched(self):
        """目录里不属于本程序的文件不能被删（防止选错目录清空人家文件夹）。"""
        stranger = self.target / "我的作业.docx"
        stranger.write_text("IMPORTANT", encoding="utf-8")
        self._install()
        self.assertTrue(stranger.exists(), "安装不该删除无关文件")


if __name__ == "__main__":
    unittest.main()