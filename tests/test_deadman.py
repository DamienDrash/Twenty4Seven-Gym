"""Dead-Man's-Switch: darf nie den Worker beeinflussen, aber korrekt melden."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nuki_integration.services import deadman


class PingTests(unittest.TestCase):
    def _settings(self, url="https://hc-ping.com/abc-123"):
        return SimpleNamespace(healthcheck_ping_url=url)

    def test_disabled_without_url(self):
        with patch("httpx.post") as post:
            self.assertFalse(deadman.ping(self._settings("")))
            post.assert_not_called()

    def test_success_ping_hits_base_url(self):
        with patch("httpx.post") as post:
            post.return_value = SimpleNamespace(status_code=200)
            self.assertTrue(deadman.ping(self._settings()))
            self.assertEqual(post.call_args[0][0], "https://hc-ping.com/abc-123")

    def test_suffixes_map_to_endpoints(self):
        for suffix, expected in (("start", "https://hc-ping.com/abc-123/start"),
                                 ("fail", "https://hc-ping.com/abc-123/fail")):
            with patch("httpx.post") as post:
                post.return_value = SimpleNamespace(status_code=200)
                deadman.ping(self._settings(), suffix=suffix)
                self.assertEqual(post.call_args[0][0], expected)

    def test_network_failure_is_swallowed(self):
        """Der Worker darf an seiner eigenen Überwachung nicht scheitern."""
        with patch("httpx.post", side_effect=OSError("kein Netz")):
            self.assertFalse(deadman.ping(self._settings()))

    def test_http_error_reported_but_not_raised(self):
        with patch("httpx.post") as post:
            post.return_value = SimpleNamespace(status_code=404)
            self.assertFalse(deadman.ping(self._settings()))

    def test_payload_is_capped(self):
        with patch("httpx.post") as post:
            post.return_value = SimpleNamespace(status_code=200)
            deadman.ping(self._settings(), payload="x" * 50_000)
            self.assertLessEqual(len(post.call_args.kwargs["content"]), 10_000)


class WorkerWiringTests(unittest.TestCase):
    def test_success_ping_only_after_a_completed_cycle(self):
        """Ein Ping am Schleifenanfang würde einen mitten abbrechenden Zyklus grün melden."""
        import inspect
        from nuki_integration import worker
        src = inspect.getsource(worker.run_forever)
        start_at = src.index('suffix="start"')
        run_at = src.index("run_cycle(db, settings, logger)")
        ok_at = src.index("deadman.ping(settings, payload=")
        fail_at = src.index('suffix="fail"')
        self.assertLess(start_at, run_at)
        self.assertLess(run_at, ok_at)
        self.assertLess(ok_at, fail_at)

class RobustnessTests(unittest.TestCase):
    def test_odd_settings_value_never_raises(self):
        """Ein kaputter Konfigurationswert darf den Worker-Zyklus nicht mitreißen."""
        for value in (object(), 12345, ["x"]):
            with patch("httpx.post") as post:
                post.return_value = SimpleNamespace(status_code=200)
                try:
                    deadman.ping(SimpleNamespace(healthcheck_ping_url=value))
                except Exception as exc:      # pragma: no cover
                    self.fail(f"ping() hat {type(exc).__name__} geworfen: {exc}")

if __name__ == "__main__":
    unittest.main()
