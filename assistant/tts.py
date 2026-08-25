"""Spoken feedback for the voice front-end -- lets the assistant say what happened
instead of only printing/logging it where nobody's looking. Uses pyttsx3's Windows
SAPI5 driver: fully offline, no account or API key, consistent with the rest of the
project (Whisper, openWakeWord) all running locally.

Deliberately not used by assistant/cli.py -- audio feedback is for a hands-off voice
session; the typed REPL already shows the same text on screen.
"""

import os
import random
import threading

from assistant.logger import get_logger

log = get_logger(__name__)

# Who the assistant is talking to -- used in spoken responses. Override via
# config/.env if this assistant is ever set up for someone else.
USER_NAME = os.environ.get("ASSISTANT_USER_NAME", "Ibrahim")

SUCCESS_PHRASES = [
    "Done.",
    "Done, {name}.",
    "Got it.",
]
NO_MATCH_PHRASES = [
    "I don't understand, {name}.",
    "Sorry {name}, I didn't catch that. Can you speak a little clearer?",
    "I'm not sure what you mean, {name}.",
]
FAILED_PHRASES = [
    "I can't carry that out, {name}.",
    "Sorry {name}, I wasn't able to do that.",
    "That didn't work, {name}.",
]

_lock = threading.Lock()


def _enabled() -> bool:
    return os.environ.get("SPEAK_RESPONSES", "1").lower() not in ("0", "false", "no")


def speak(text: str) -> None:
    """Speaks text synchronously. Blocking is fine here -- callers always run this
    on the background command thread (see listen.py's _process_command), never on
    the wake-word detection loop itself. Never raises: a broken TTS engine should
    degrade to silence, not take the rest of the command handling down with it.

    Builds a fresh engine (and initializes COM) on *this* thread every call rather
    than reusing one global engine across calls. pyttsx3's Windows driver is SAPI5
    via COM, and COM apartment state is thread-local -- listen.py runs every
    command on a freshly spawned thread, so an engine created on one thread hung
    forever in runAndWait() when driven from the next command's thread. That
    silently ate every spoken response (never raised, just never returned) until
    listen.py's 60s command timeout abandoned it -- during which every wake word
    heard in between was ignored as "still processing a previous command"."""
    if not _enabled() or not text:
        return
    try:
        import pythoncom
        import pyttsx3

        with _lock:
            pythoncom.CoInitialize()
            try:
                engine = pyttsx3.init()
                rate = os.environ.get("TTS_RATE")
                if rate:
                    engine.setProperty("rate", int(rate))
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            finally:
                pythoncom.CoUninitialize()
    except Exception:
        log.exception("Text-to-speech failed")


def speak_success() -> None:
    speak(random.choice(SUCCESS_PHRASES).format(name=USER_NAME))


def speak_no_match() -> None:
    speak(random.choice(NO_MATCH_PHRASES).format(name=USER_NAME))


def speak_failed() -> None:
    speak(random.choice(FAILED_PHRASES).format(name=USER_NAME))
