"""Pure time-window pin-pool logic (stdlib only)."""
import unittest

from nuki_integration.timewindow import pin_pool as p


class BucketTests(unittest.TestCase):
    def test_24_offpeak_buckets_96_slots(self):
        buckets = p.compute_offpeak_buckets()
        self.assertEqual(len(buckets), 24)
        slots = p.build_slots(buckets)
        self.assertEqual(len(slots), 96)
        self.assertLessEqual(len(slots), p.KEYPAD_CODE_LIMIT)
        p.assert_within_budget(slots)

    def test_weekday_masks(self):
        by_hour = {b.hour: b.weekday_mask for b in p.compute_offpeak_buckets()}
        self.assertEqual(by_hour[3], 127)   # 00–06 alle Tage
        self.assertEqual(by_hour[10], 1)    # 07–14 nur So
        self.assertEqual(by_hour[17], 3)    # 15–20 Sa+So
        self.assertEqual(by_hour[22], 127)  # 21–23 alle Tage

    def test_time_windows(self):
        b = {x.hour: x for x in p.compute_offpeak_buckets()}
        self.assertEqual(b[0].from_time, 0)
        self.assertEqual(b[23].until_time, 1439)


class GuardTests(unittest.TestCase):
    def test_needs_keypad_code(self):
        self.assertFalse(p.needs_keypad_code(0, 10))  # Mo 10:00 offen
        self.assertTrue(p.needs_keypad_code(0, 3))    # Mo 03:00 off-peak
        self.assertFalse(p.needs_keypad_code(5, 9))   # Sa 09:00 offen
        self.assertTrue(p.needs_keypad_code(5, 16))   # Sa 16:00 off-peak
        self.assertTrue(p.needs_keypad_code(6, 10))   # So zu


class PinTests(unittest.TestCase):
    def test_unique_valid_rotation(self):
        slots = p.build_slots(p.compute_offpeak_buckets())
        p.rotate_pins(slots)
        codes = [s.code for s in slots]
        self.assertEqual(len(set(codes)), 96)
        for c in codes:
            p.validate_keypad_code(c)

    def test_validate_rejects(self):
        for bad in ("102345", "123456", "12345"):
            with self.assertRaises(ValueError):
                p.validate_keypad_code(bad)


class AntiRepeatTests(unittest.TestCase):
    def test_log_cycles_pool(self):
        log = p.AssignmentLog()
        chosen = [log.assign("M42", 1, 3) for _ in range(p.POOL_PER_HOUR)]
        self.assertEqual(sorted(chosen), list(range(p.POOL_PER_HOUR)))

    def test_choose_pool_index_stateless(self):
        self.assertEqual(p.choose_pool_index([]), 0)
        self.assertEqual(p.choose_pool_index([0]), 1)
        self.assertEqual(p.choose_pool_index([0, 1, 2]), 3)
        self.assertEqual(p.choose_pool_index([0, 1, 2, 3]), 3)  # exhausted → reuse


if __name__ == "__main__":
    unittest.main()
