"""版本号比较。

这里出过一次事故：3.8.0-beta.1 和 3.8.0-beta.3 被算成同一个版本，
装了 beta.1 的人检查更新永远显示「已是最新」，一条更新都没收到过。
"""

import unittest

from api.updater import _newer, _version_tuple


class VersionTuple(unittest.TestCase):
    def test_正式版排在同号_beta_之后(self):
        self.assertGreater(_version_tuple("3.8.0"), _version_tuple("3.8.0-beta.9"))

    def test_beta_之间按序号排(self):
        self.assertGreater(_version_tuple("3.8.0-beta.3"),
                           _version_tuple("3.8.0-beta.1"))
        self.assertGreater(_version_tuple("3.8.0-beta.10"),
                           _version_tuple("3.8.0-beta.9"))

    def test_主版本号优先级最高(self):
        self.assertGreater(_version_tuple("3.8.1-beta.1"),
                           _version_tuple("3.8.0"))
        self.assertGreater(_version_tuple("3.9.0-beta.1"),
                           _version_tuple("3.8.9"))

    def test_没写后缀算正式版(self):
        self.assertEqual(_version_tuple("3.8.0"), (3, 8, 0, 1, 0))
        self.assertEqual(_version_tuple("3.8.0-beta.3"), (3, 8, 0, 0, 3))

    def test_缺位的补零(self):
        self.assertEqual(_version_tuple("3.8"), _version_tuple("3.8.0"))

    def test_空值不会炸(self):
        self.assertEqual(_version_tuple(""), (0, 0, 0, 1, 0))
        self.assertEqual(_version_tuple(None), (0, 0, 0, 1, 0))


class NewerCheck(unittest.TestCase):
    def test_beta_递进能识别(self):
        """就是这条没做到，导致老用户收不到更新。"""
        self.assertTrue(_newer("3.8.0-beta.3", "3.8.0-beta.1"))

    def test_同版本不算更新(self):
        self.assertFalse(_newer("3.8.0-beta.3", "3.8.0-beta.3"))

    def test_正式版覆盖同号_beta(self):
        self.assertTrue(_newer("3.8.0", "3.8.0-beta.3"))

    def test_beta_不覆盖同号正式版(self):
        self.assertFalse(_newer("3.8.0-beta.3", "3.8.0"))

    def test_降级不提示(self):
        self.assertFalse(_newer("3.8.0-beta.1", "3.8.0-beta.3"))

    def test_老客户端能收到_3_8_1(self):
        """装在别人机器上的旧客户端只比主版本号，靠提主版本号把它们捞回来。"""
        self.assertTrue(_newer("3.8.1-beta.1", "3.8.0-beta.1"))
        # 旧实现下这几个值会是 (3,8,1) 和 (3,8,0)，同样成立
        self.assertNotEqual(_version_tuple("3.8.1-beta.1")[:3],
                            _version_tuple("3.8.0-beta.1")[:3])


if __name__ == "__main__":
    unittest.main()