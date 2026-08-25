"""Covers _CommandGate -- the thing standing in for a plain threading.Lock around
"one command being processed at a time." A real hang (e.g. a Spotify OAuth login
opened in the browser and never completed) previously held that lock forever, so
every later wake word was silently ignored until the process was killed and
restarted by hand. _CommandGate bounds that outage to a timeout instead.

Also covers _process_command's had_real_speech handling -- a wake word that
fires on background noise/silence (no real speech in the recording at all)
should stay silent on an empty transcription instead of speaking "I didn't
catch that" to an empty room, while a genuine attempt that Whisper still
couldn't parse should still get spoken feedback."""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from assistant.listen import _CommandGate, _process_command


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


class TestProcessCommandSilentMisfire(unittest.TestCase):
    def _mock_whisper(self, text: str):
        ready = threading.Event()
        ready.set()
        model = MagicMock()
        segment = MagicMock()
        segment.text = text
        model.transcribe.return_value = ([segment] if text else [], None)
        return ready, {"model": model}

    @patch("assistant.listen.tts")
    @patch("assistant.listen.voiceprint")
    def test_empty_transcription_with_no_real_speech_stays_silent(self, mock_voiceprint, mock_tts):
        mock_voiceprint.verify.return_value = True
        ready, holder = self._mock_whisper("")
        _process_command(np.zeros(16000, dtype=np.float32), ready, holder, "", had_real_speech=False)
        mock_tts.speak_no_match.assert_not_called()

    @patch("assistant.listen.tts")
    @patch("assistant.listen.voiceprint")
    def test_empty_transcription_with_real_speech_still_speaks(self, mock_voiceprint, mock_tts):
        mock_voiceprint.verify.return_value = True
        ready, holder = self._mock_whisper("")
        _process_command(np.zeros(16000, dtype=np.float32), ready, holder, "", had_real_speech=True)
        mock_tts.speak_no_match.assert_called_once()

    @patch("assistant.listen.execute_with_status")
    @patch("assistant.listen.tts")
    @patch("assistant.listen.voiceprint")
    def test_had_real_speech_defaults_true_for_callers_that_omit_it(self, mock_voiceprint, mock_tts, mock_execute):
        from assistant.listen import ExecStatus

        mock_voiceprint.verify.return_value = True
        mock_execute.return_value = (ExecStatus.NO_MATCH, "didn't understand")
        ready, holder = self._mock_whisper("some nonsense")
        _process_command(np.zeros(16000, dtype=np.float32), ready, holder, "")
        mock_tts.speak_no_match.assert_called_once()


if __name__ == "__main__":
    unittest.main()
