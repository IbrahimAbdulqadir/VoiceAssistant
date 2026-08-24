"""Covers _CommandGate -- the thing standing in for a plain threading.Lock around
"one command being processed at a time." A real hang (e.g. a Spotify OAuth login
opened in the browser and never completed) previously held that lock forever, so
every later wake word was silently ignored until the process was killed and
restarted by hand. _CommandGate bounds that outage to a timeout instead."""

import time
import unittest

from assistant.listen import _CommandGate


class TestCommandGate(unittest.TestCase):
    def test_acquire_when_free(self):
        gate = _CommandGate(timeout=60)
        self.assertTrue(gate.try_acquire())

    def test_second_acquire_fails_while_busy(self):
        gate = _CommandGate(timeout=60)
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())

    def test_acquire_succeeds_again_after_release(self):
        gate = _CommandGate(timeout=60)
        gate.try_acquire()
        gate.release()
        self.assertTrue(gate.try_acquire())

    def test_stale_holder_is_abandoned_after_timeout(self):
        gate = _CommandGate(timeout=0.05)
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())  # still within timeout
        time.sleep(0.1)
        self.assertTrue(gate.try_acquire())  # previous holder now treated as abandoned

    def test_release_never_raises_even_if_not_held(self):
        gate = _CommandGate(timeout=60)
        gate.release()  # should be a no-op, not an error


if __name__ == "__main__":
    unittest.main()
