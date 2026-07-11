"""Thread-aware Telegram transport tests (no network; httpx mocked)."""
import unittest
from types import SimpleNamespace
from unittest import mock

from nuki_integration import notifications as N
from nuki_integration.notifications import TelegramConfig, TelegramService
from nuki_integration.services.settings import get_effective_telegram_config


class _Resp:
    def __init__(self, ok=True, status=200, description=None):
        self.is_success = 200 <= status < 300
        self.status_code = status
        self._ok = ok
        self._description = description
        self.text = "{}"

    def json(self):
        d = {"ok": self._ok}
        if self._description:
            d["description"] = self._description
        return d


class BuildPayloadTests(unittest.TestCase):
    def test_includes_thread_when_configured(self):
        svc = TelegramService(TelegramConfig(bot_token="t", chat_id="7473721797",
                                             message_thread_id="4253"))
        p = svc.build_payload("hi")
        self.assertEqual(p["chat_id"], "7473721797")
        self.assertEqual(p["message_thread_id"], 4253)  # coerced to int

    def test_omits_thread_when_absent(self):
        svc = TelegramService(TelegramConfig(bot_token="t", chat_id="c", message_thread_id=""))
        self.assertNotIn("message_thread_id", svc.build_payload("hi"))

    def test_negative_supergroup_thread_coerced(self):
        svc = TelegramService(TelegramConfig(bot_token="t", chat_id="c", message_thread_id="-100123"))
        self.assertEqual(svc.build_payload("hi")["message_thread_id"], -100123)


class SendMessageTests(unittest.TestCase):
    def test_success_posts_thread_and_returns_true(self):
        svc = TelegramService(TelegramConfig(bot_token="tok", chat_id="7473721797",
                                             message_thread_id="4253"))
        with mock.patch.object(N.httpx, "post", return_value=_Resp(ok=True)) as post:
            self.assertTrue(svc.send_message(text="hello"))
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["message_thread_id"], 4253)
        self.assertEqual(sent["chat_id"], "7473721797")

    def test_api_error_returns_false_not_raise(self):
        svc = TelegramService(TelegramConfig(bot_token="tok", chat_id="c", message_thread_id="4253"))
        bad = _Resp(ok=False, status=400, description="message thread not found")
        with mock.patch.object(N.httpx, "post", return_value=bad):
            self.assertFalse(svc.send_message(text="x"))  # surfaced, not raised

    def test_not_configured_returns_false(self):
        self.assertFalse(TelegramService(TelegramConfig(bot_token="", chat_id="")).send_message(text="x"))


class EffectiveConfigTests(unittest.TestCase):
    def test_effective_config_carries_thread_from_settings(self):
        db = SimpleNamespace(get_system_setting=lambda k: {})
        settings = SimpleNamespace(telegram_bot_token="tok", telegram_chat_id="7473721797",
                                   telegram_message_thread_id="4253")
        cfg = get_effective_telegram_config(db, settings)
        self.assertEqual(cfg.chat_id, "7473721797")
        self.assertEqual(cfg.message_thread_id, "4253")


if __name__ == "__main__":
    unittest.main()
