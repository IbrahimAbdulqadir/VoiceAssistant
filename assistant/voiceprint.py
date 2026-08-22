"""Phase 3: speaker verification via Resemblyzer voice embeddings. `enroll.py`
builds one averaged voiceprint from several short recordings of you (see
voice-assistant-scope.md: vary *conditions* -- quiet/noisy room, tired/rushed voice
-- not vocabulary, since the voiceprint is built from vocal characteristics, not
what's said). `verify()` checks a freshly-recorded utterance's embedding against
that voiceprint via cosine similarity -- Resemblyzer's embeddings are already
L2-normalized, so a plain dot product *is* the cosine similarity.

This gates command execution in listen.py so the assistant only acts on your voice,
not whoever else says the wake word. Until you've run enrollment, verify() always
returns True -- Phase 2's "responds to anyone" behavior is the default, not a
lockout, until you opt into Phase 3.
"""

import os
from pathlib import Path
from typing import List, Optional

import numpy as np

from assistant.logger import get_logger

log = get_logger(__name__)

VOICEPRINT_PATH = Path(__file__).resolve().parent.parent / "config" / "voiceprint.npy"
DEFAULT_THRESHOLD = float(os.environ.get("SPEAKER_THRESHOLD", "0.72"))

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder()
    return _encoder


def is_enrolled() -> bool:
    return VOICEPRINT_PATH.exists()


def enroll(wavs: List[np.ndarray]) -> None:
    """wavs: float32 mono 16kHz waveforms from several short recordings. Averages
    them into one robust voiceprint and saves it to VOICEPRINT_PATH."""
    from resemblyzer import preprocess_wav

    encoder = _get_encoder()
    processed = [preprocess_wav(w, source_sr=16000) for w in wavs]
    embedding = encoder.embed_speaker(processed)
    VOICEPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.save(VOICEPRINT_PATH, embedding)
    log.info("Saved voiceprint from %d samples to %s", len(wavs), VOICEPRINT_PATH)


def verify(wav: np.ndarray, threshold: Optional[float] = None) -> bool:
    """wav: float32 mono 16kHz waveform of the command just recorded. Returns True if
    it matches the enrolled voiceprint closely enough, or if nothing is enrolled yet."""
    if not is_enrolled():
        return True

    from resemblyzer import preprocess_wav

    encoder = _get_encoder()
    enrolled = np.load(VOICEPRINT_PATH)
    processed = preprocess_wav(wav, source_sr=16000)
    embedding = encoder.embed_utterance(processed)
    similarity = float(np.dot(embedding, enrolled))
    thresh = threshold if threshold is not None else DEFAULT_THRESHOLD
    log.info("Speaker similarity: %.3f (threshold %.2f)", similarity, thresh)
    return similarity >= thresh
