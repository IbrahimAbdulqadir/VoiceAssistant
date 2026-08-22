"""One-time voice enrollment for Phase 3 speaker verification. Records several short
clips of your voice via sounddevice and builds one averaged voiceprint from them
(assistant/voiceprint.py does the actual embedding). Run with `python main.py --enroll`.

Per voice-assistant-scope.md: what you say doesn't matter -- voiceprints are built
from vocal characteristics, not vocabulary. What matters is varying *conditions*
between takes (quiet room, background noise, tired voice, rushed voice), so the
voiceprint generalizes instead of overfitting to one recording session.
"""

import numpy as np

from assistant import voiceprint
from assistant.logger import get_logger

log = get_logger(__name__)

NUM_SAMPLES = 6
SAMPLE_SECONDS = 4
SAMPLE_RATE = 16000


def run() -> None:
    import sounddevice as sd

    print(f"Voice enrollment -- {NUM_SAMPLES} recordings, {SAMPLE_SECONDS}s each.")
    print(
        "Say anything you like, but vary the *conditions* between takes if you can -- "
        "quiet room, some background noise, tired voice, rushed voice. What you say "
        "doesn't matter; only your voice does.\n"
    )

    wavs = []
    for i in range(1, NUM_SAMPLES + 1):
        input(f"[{i}/{NUM_SAMPLES}] Press Enter, then talk for about {SAMPLE_SECONDS}s...")
        print("  recording...")
        recording = sd.rec(
            int(SAMPLE_SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
        )
        sd.wait()
        wav = recording.flatten().astype(np.float32) / 32768.0
        wavs.append(wav)
        print("  captured.\n")

    print("Building voiceprint...")
    voiceprint.enroll(wavs)
    print(
        "Done -- saved to config/voiceprint.npy. `python main.py --listen` will now "
        "only act on your voice; anyone else saying the wake word gets ignored."
    )


if __name__ == "__main__":
    run()
