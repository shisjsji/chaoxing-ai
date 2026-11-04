"""本机界面接口的令牌闸门。

这个服务监听 127.0.0.1 却没有登录，接口能改配置、清题库、下载并重启「更新包」。
没有令牌时任何本机网页都能对着它发 POST，所以这两条必须一直绿。
"""

import unittest

from webui import server


class TokenGuardTest(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        self.client = server.app.test_client()

    def test_api_without_token_is_rejected(self):
        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 403)

    def test_api_with_wrong_token_is_rejected(self):
        resp = self.client.get("/api/config", headers={"X-Token": "not-the-token"})
        self.assertEqual(resp.status_code, 403)

    def test_api_with_token_passes(self):
        resp = self.client.get("/api/config", headers={"X-Token": server.UI_TOKEN})
        self.assertNotEqual(resp.status_code, 403)

    def test_page_carries_token(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(server.UI_TOKEN, html)


class UpdateApplyTest(unittest.TestCase):
    def setUp(self):
        server.app.config.update(TESTING=True)
        self.client = server.app.test_client()
        server._pending_update.update(url="", sha256="", version="")

    def test_apply_rejects_url_that_was_never_checked(self):
        resp = self.client.post(
            "/api/update/apply",
            json={"url": "http://evil.example/u.zip", "sha256": "a" * 64},
            headers={"X-Token": server.UI_TOKEN},
        )
        self.assertEqual(resp.status_code, 400)

    def test_apply_rejects_sha_mismatch(self):
        server._pending_update.update(url="https://example.com/ok.zip", sha256="b" * 64)
        resp = self.client.post(
            "/api/update/apply",
            json={"url": "https://example.com/ok.zip", "sha256": "c" * 64},
            headers={"X-Token": server.UI_TOKEN},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()