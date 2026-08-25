"""Phase 2 front-end: wake word (openWakeWord) -> voice-activity-gated recording
(openWakeWord's bundled Silero VAD) -> local transcription (faster-whisper) -> the
same executor.execute() the CLI uses. Run with `python main.py --listen`.

Fully local, no account or API key needed for any of it -- this replaced an earlier
Picovoice-based design (Porcupine + Cobra) after Picovoice's signup started
rejecting personal email addresses. openWakeWord ships pretrained keywords
(alexa, hey_mycroft, hey_jarvis, hey_rhasspy, timer, weather) and its own Silero
VAD model, both downloaded once on first run (see download_models() below) with no
signup of any kind.

All heavy imports happen inside run(), not at module load, so `python main.py`
(the typed REPL) never pays for them.

A small on-screen indicator (assistant/indicator.py) flashes the instant the wake
word is heard, so you don't have to say it repeatedly just to check it's working --
set SHOW_INDICATOR=0 in config/.env to turn it off.
"""

import os
import threading
import time
from pathlib import Path
from typing import List, Optional

import win32api
import win32event
import winerror

from assistant import actions, tts, voiceprint
from assistant.executor import ExecStatus, execute_with_status
from assistant.logger import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CUSTOM_MODEL_PATH = CONFIG_DIR / "wake_word.onnx"
# Any openWakeWord built-in works here: alexa, hey_mycroft, hey_jarvis, hey_rhasspy,
# timer, weather. Falls back to hey_jarvis if unset or unrecognized (and no custom
# model is trained yet) -- see README "Wake word + voice input setup" for training
# a custom one (e.g. "Spiderman") with the local wakeword_trainer tool.
DEFAULT_KEYWORD = os.environ.get("WAKE_WORD", "hey_jarvis")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480          # 30ms @ 16kHz -- matches openWakeWord VAD's recommended frame size
# Custom models trained on synthetic TTS-only data (no real ambient-noise negatives)
# run a bit hotter than the pretrained built-ins -- override via WAKE_THRESHOLD in
# config/.env if you're seeing false triggers on background noise/conversation.
WAKE_THRESHOLD = float(os.environ.get("WAKE_THRESHOLD", "0.6"))
# How long the model refuses to re-fire after a detection, regardless of score --
# prevents the "keeps re-triggering on its own" loop. Comfortably longer than one
# recording cycle's worth of residual audio context.
WAKE_DEBOUNCE_SECONDS = float(os.environ.get("WAKE_DEBOUNCE_SECONDS", "3.0"))
SILENCE_THRESHOLD = 0.4      # VAD probability below this counts as silence
MIN_SPEECH_FRAMES = 17       # ~0.5s -- don't allow a cutoff before at least this much audio
# Seconds of continuous silence before ending the recording -- long enough to
# survive a mid-command pause (thinking, a breath, an accent needing a moment to
# form the words) without cutting the command off early. Defaults to 5s.
SILENCE_FRAMES_TO_STOP = int(float(os.environ.get("SILENCE_SECONDS", "5.0")) * 1000 / 30)
MAX_RECORD_FRAMES = 700      # hard cap (~21s) so a stuck/open mic can't record forever

# --- Fast lane: instant per-word detection instead of waiting for Whisper ---
# Each of these has its own small openWakeWord-style classifier (trained via
# wakeword_trainer/train_command_word.py), running off the same shared audio
# embedding pipeline as the main wake word -- cheap to add, since only the
# small classifier head differs per word. Two of them completing a valid
# combo (or one standalone word) inside COMMAND_WORDS_DIR executes instantly,
# skipping recording-then-Whisper-transcription entirely. If nothing in this
# set fires before the normal silence-timeout logic ends the recording, that
# audio is transcribed by Whisper exactly as before -- the fast lane can only
# ever add speed, never remove the existing fallback.
COMMAND_WORDS_DIR = CONFIG_DIR / "command_words"
ACTION_WORDS = {"open", "close", "minimize"}
TARGET_WORDS = {
    "spotify", "chrome", "telegram", "vscode", "notepad",
    "chess", "day_one", "vm", "file_explorer", "terminal", "unigram",
}
STANDALONE_COMMANDS = {
    "desktop": "show desktop",
    "wifi": "show wifi",
    "notifications": "open notifications",
    "search": "open search",
    "battery": "show battery",
    "play": "play",
    "pause": "pause",
    "next": "next",
    "previous": "previous",
}
FAST_LANE_THRESHOLD = float(os.environ.get("FAST_LANE_THRESHOLD", "0.85"))

# How long a single command (transcription + action) may run before a new wake
# word is allowed to take over anyway. Real-world example that motivated this:
# "play X on spotify" with no cached Spotify token yet opens a browser tab for
# the user to log in and blocks waiting for that OAuth redirect -- if the user
# doesn't complete it (they weren't looking at the screen, the command was
# fired in the background, etc.), that wait never times out on its own, and
# without this the plain Lock it used to hold stayed locked forever: every
# later wake word was silently ignored ("still processing a previous command")
# until the process was killed and restarted by hand. This bounds that outage
# to COMMAND_TIMEOUT_SECONDS instead of leaving it permanent.
COMMAND_TIMEOUT_SECONDS = float(os.environ.get("COMMAND_TIMEOUT_SECONDS", "60"))

# Guards against a second --listen process ever running alongside this one.
# The VoiceAssistantListener scheduled task re-triggers main.py --listen every
# 5 minutes as a watchdog (so a crashed listener comes back on its own), and
# its own "IgnoreNew" duplicate guard stops it from double-launching -- but
# only for instances *it* started. It has no way to know about one started any
# other way (a manual restart, a debugger, run_listen.bat), so without this,
# any of those produces a real second process: double the wake word, audio
# capture, and on-screen indicator, each fighting the other for the mic and
# for topmost/screen position (symptom actually seen: the indicator flickering
# between idle and active and getting covered by other windows). A named
# Windows mutex, not a PID file -- Windows releases it the instant this
# process exits for any reason, crash included, so there's no stale-lock state
# to ever clean up by hand.
_SINGLE_INSTANCE_MUTEX_NAME = "VoiceAssistantListener_SingleInstance"
_single_instance_mutex = None


def _acquire_single_instance_lock() -> bool:
    """True if this is the only --listen process running; False if another one
    already holds the lock, meaning this process should exit immediately
    without loading any models or touching the microphone."""
    global _single_instance_mutex
    _single_instance_mutex = win32event.CreateMutex(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


class _CommandGate:
    """A "one command at a time" gate like threading.Lock, except a holder that's
    been busy longer than `timeout` seconds is treated as abandoned and handed
    over to the next acquire instead of blocking forever. The abandoned
    command's thread is a daemon and keeps running in the background (it may
    still complete, speak its result, etc.) -- it just no longer blocks
    anything new."""

    def __init__(self, timeout: float):
        self._timeout = timeout
        self._busy_since: Optional[float] = None
        self._guard = threading.Lock()

    def try_acquire(self) -> bool:
        with self._guard:
            now = time.monotonic()
            if self._busy_since is not None:
                if now - self._busy_since < self._timeout:
                    return False
                log.warning(
                    "Previous command exceeded %.0fs -- abandoning it (it may "
                    "still finish in the background) and accepting new commands",
                    self._timeout,
                )
            self._busy_since = now
            return True

    def release(self) -> None:
        with self._guard:
            self._busy_since = None


def _load_wake_model():
    import openwakeword
    from openwakeword.model import Model

    openwakeword.utils.download_models()  # one-time; no-ops for files already present

    if CUSTOM_MODEL_PATH.exists():
        model_paths = [str(CUSTOM_MODEL_PATH)]
        if COMMAND_WORDS_DIR.exists():
            model_paths += [str(p) for p in sorted(COMMAND_WORDS_DIR.glob("*.onnx"))]

        model = Model(wakeword_models=model_paths, inference_framework="onnx")
        keyword_name = CUSTOM_MODEL_PATH.stem
        if keyword_name not in model.models:
            keyword_name = list(model.models.keys())[0]
        log.info("Loaded %d fast-lane command word models: %s", len(model_paths) - 1, sorted(set(model.models) - {keyword_name}))
        return model, keyword_name, True

    keyword_name = DEFAULT_KEYWORD if DEFAULT_KEYWORD in openwakeword.MODELS else "hey_jarvis"
    if keyword_name != DEFAULT_KEYWORD:
        print(
            f"'{DEFAULT_KEYWORD}' isn't an openWakeWord built-in and config/wake_word.onnx "
            f"doesn't exist yet -- falling back to '{keyword_name}' until you train it "
            "(see README 'Wake word + voice input setup')."
        )
        log.warning("WAKE_WORD='%s' has no matching built-in or .onnx file; falling back to '%s'", DEFAULT_KEYWORD, keyword_name)

    model = Model(inference_framework="onnx")  # loads all pretrained built-ins
    return model, keyword_name, False


def _show_indicator() -> bool:
    return os.environ.get("SHOW_INDICATOR", "1").lower() not in ("0", "false", "no")


def _build_vocabulary_prompt() -> str:
    """Builds a short natural-language prompt to bias Whisper's transcription
    toward this assistant's actual vocabulary (command verbs, your installed
    apps) -- Whisper only weighs the prompt's *trailing* context heavily, so
    this stays short and relevant rather than dumping every discovered app name
    in. Doesn't retrain anything; just nudges the decoder toward likely words."""
    from assistant.config import config

    common_apps = ["chrome", "spotify", "notepad", "vscode", "gmail", "explorer"]
    aliases = list(config.aliases.keys())
    apps = sorted(set(common_apps + aliases))[:20]

    return (
        "Voice commands for a personal assistant. Examples: open " + ", open ".join(apps) + ". "
        "Close spotify. Play a song on spotify. Open vscode in a folder. "
        "Open folder, open website, search mail for, open mail. Run script. List apps. Help. "
        # Whisper weighs the prompt's *trailing* context most heavily, and without
        # these phrases present anywhere it defaulted to nearest-sounding common
        # English instead -- "shut down" kept coming back as "showdown"/"show
        # down", never matching executor.py's shutdown regex. Listed last so they
        # get the strongest bias.
        "Shut down the system. Shut down my computer. Restart the computer. "
        "Hibernate the computer. Lock the screen. Turn off the screen. Screen off. "
        "Cancel the shutdown."
    )


def _process_command(audio, whisper_ready, whisper_holder, vocabulary_prompt, indicator=None, fast_text=None) -> None:
    """Transcribes (unless fast_text is already resolved by the fast lane) and
    executes one already-recorded command. Runs on its own background thread
    (see _listen_loop) so a slow or hung action -- a network call, an OAuth
    flow waiting on a browser that never completes, anything -- can never
    freeze wake-word detection. The indicator is already back to idle by the
    time this runs; nothing here touches it."""
    if not voiceprint.verify(audio):
        print("(voice not recognized -- ignoring)")
        log.info("Speaker verification failed; ignoring command")
        return

    if fast_text is not None:
        text = fast_text
        log.info("Fast-lane command: '%s'", text)
    else:
        if not whisper_ready.is_set():
            # Only reachable in the first few seconds after startup, if a full
            # (non-fast-lane) command is spoken before Whisper's background load
            # finishes -- wait for it here rather than on the wake-word thread, so
            # detection itself is never blocked, only this one command's response.
            log.info("Whisper still loading -- waiting before transcribing")
            whisper_ready.wait()
        whisper = whisper_holder["model"]
        transcribe_start = time.monotonic()
        segments, _ = whisper.transcribe(
            audio, language="en", beam_size=1, initial_prompt=vocabulary_prompt
        )
        text = " ".join(seg.text for seg in segments).strip()
        transcribe_elapsed = time.monotonic() - transcribe_start
        log.info(
            "Transcription took %.1fs (%.1fs of audio)",
            transcribe_elapsed, len(audio) / SAMPLE_RATE,
        )
        # Whisper routinely appends closing punctuation ("close spotify.") that
        # then breaks exact-match app-name lookups (close_app/minimize_app)
        # even though the fuzzy-matched open_app tolerates it fine -- strip it
        # here for every command.
        text = text.rstrip(".,!?;: ")

    if not text:
        print("(didn't catch anything)")
        tts.speak_no_match()
        return

    print(f"> {text}")
    if fast_text is None:
        log.info("Transcribed: '%s'", text)
    try:
        execute_start = time.monotonic()
        status, result = execute_with_status(text)
        log.info("Execution took %.1fs", time.monotonic() - execute_start)
        print(result)
        if status == ExecStatus.OK:
            tts.speak_success()
        elif status == ExecStatus.NO_MATCH:
            tts.speak_no_match()
        else:
            tts.speak_failed()
    except Exception as e:
        log.error("Command execution failed: %s", e)
        print(f"Error running that command: {e}")
        tts.speak_failed()


def _listen_loop(indicator=None) -> None:
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel
    from openwakeword.vad import VAD

    import queue

    from assistant import actions
    actions.VOICE_MODE = True

    wake_model, keyword_name, is_custom = _load_wake_model()
    vad = VAD()

    audio_q: "queue.Queue[bytes]" = queue.Queue()

    def _callback(indata, frames, time_info, status):
        audio_q.put(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, dtype="int16", channels=1, callback=_callback
    )

    # vocabulary_prompt only reads config aliases -- cheap, no reason to wait on Whisper for it.
    vocabulary_prompt = _build_vocabulary_prompt()

    # Whisper's load + one-time warm-up transcription is the slow part (the "base"
    # model still takes real time to page in cold, worse right after a fresh
    # boot/resume when disk cache is cold and other startup processes are competing
    # for CPU). Wake-word detection doesn't need Whisper at all -- it only needs
    # wake_model, already loaded above -- so load Whisper on its own thread and open
    # the mic immediately instead of making wake-word detection (and the fast lane)
    # wait minutes for a model it doesn't use. This is what was silently delaying
    # "mic activation" after every restart: the stream literally wasn't opened until
    # Whisper had finished loading AND warming up.
    whisper_holder: dict = {}
    whisper_ready = threading.Event()

    def _load_whisper() -> None:
        # "base" + greedy decoding (beam_size=1) instead of "small" + beam_size=5 --
        # this machine only has 4 logical cores, and beam search on "small" was
        # taking ~20s to transcribe a short command. Trades a little accuracy for a
        # lot of latency; override WHISPER_MODEL=small in config/.env to trade back
        # if the smaller model's accuracy isn't good enough.
        model_size = os.environ.get("WHISPER_MODEL", "base")
        w = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
        # One-time warm-up so the first real command isn't the one that eats
        # ctranslate2's cold-start cost (thread pool spin-up, weight paging).
        w.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en", beam_size=1)
        whisper_holder["model"] = w
        whisper_ready.set()
        log.info("Whisper model ready (%s)", model_size)

    threading.Thread(target=_load_whisper, daemon=True, name="whisper-loader").start()

    wake_label = "custom wake word" if is_custom else f"'{keyword_name}'"
    speaker_note = "voice-locked to you" if voiceprint.is_enrolled() else "responds to anyone -- run `python main.py --enroll` to lock it to your voice"
    print(f"Listening for the wake word ({wake_label}, {speaker_note})... Ctrl+C to stop.")
    log.info(
        "Wake word listener started (keyword=%s, custom=%s, enrolled=%s); Whisper loading in background",
        keyword_name, is_custom, voiceprint.is_enrolled(),
    )

    def _read_frame() -> "np.ndarray":
        return np.frombuffer(audio_q.get(), dtype=np.int16)

    # One combined threshold dict covers the wake word and every fast-lane
    # command word so openWakeWord's own debounce applies to all of them --
    # otherwise a command word could re-fire on its own residual state within
    # the same utterance the same way the wake word could before WAKE_DEBOUNCE_SECONDS.
    all_thresholds = {keyword_name: WAKE_THRESHOLD}
    for word in set(wake_model.models) - {keyword_name}:
        all_thresholds[word] = FAST_LANE_THRESHOLD

    # Guards against a pile-up: _process_command runs on its own thread so a
    # slow transcription/action never blocks wake-word detection, but without
    # this every new wake-word trigger spawns *another* concurrent Whisper
    # transcription on top of whatever's already running. On a
    # memory/CPU-constrained machine that's a death spiral -- each new
    # trigger competes with the still-running ones for the same starved
    # resources, so transcription gets slower, which means more triggers
    # pile up before the first one finishes, which makes it slower still.
    # Holding this means "ignore new wake-word triggers while a command is
    # still being processed" instead of queueing/stacking them -- except a
    # command stuck past COMMAND_TIMEOUT_SECONDS is abandoned instead of
    # blocking every future command too (see _CommandGate).
    command_gate = _CommandGate(COMMAND_TIMEOUT_SECONDS)

    def _run_and_release(*args) -> None:
        try:
            _process_command(*args)
        finally:
            command_gate.release()

    try:
        with stream:
            while True:
                frame = _read_frame()
                # debounce_time stops the model from firing again on its own
                # residual internal state for a few seconds after a detection --
                # openWakeWord's own fix for "prevent multiple detections of the
                # same wake word" (see its predict() docstring).
                prediction = wake_model.predict(
                    frame, threshold=all_thresholds, debounce_time=WAKE_DEBOUNCE_SECONDS
                )
                if prediction.get(keyword_name, 0.0) < WAKE_THRESHOLD:
                    continue

                if not command_gate.try_acquire():
                    log.info(
                        "Wake word heard (score=%.2f) but still processing a previous "
                        "command -- ignoring until it finishes",
                        prediction.get(keyword_name, 0.0),
                    )
                    continue

                print("Wake word detected -- listening for your command...")
                log.info("Wake word detected (score=%.2f)", prediction.get(keyword_name, 0.0))
                # Undoes actions.screen_off() -- turning off just the display (not
                # real Sleep) only works as a "wake it with my voice" feature if
                # every wake-word detection also turns the display back on, so
                # this runs unconditionally rather than only when a "screen off"
                # was actually issued. Harmless no-op when the display is already on.
                actions.wake_display()
                if indicator is not None:
                    indicator.activate()
                recording_start = time.monotonic()

                frames: List["np.ndarray"] = []
                silence_run = 0
                speech_frames_seen = 0
                fast_action = None
                fast_target = None
                fast_text = None
                for _ in range(MAX_RECORD_FRAMES):
                    chunk = _read_frame()
                    frames.append(chunk)
                    # Keep feeding the wake model during recording too -- its
                    # embedding pipeline keeps a rolling audio buffer, and going
                    # silent on it here would leave that buffer stale with the
                    # original trigger's audio, causing it to keep scoring as a
                    # match for a while after we resume listening. Its result is
                    # also how the fast lane spots a known command word inline,
                    # instead of waiting for the recording to end and transcribing.
                    fast_prediction = wake_model.predict(chunk, threshold=all_thresholds, debounce_time=WAKE_DEBOUNCE_SECONDS)
                    for word, score in fast_prediction.items():
                        if word == keyword_name or score < FAST_LANE_THRESHOLD:
                            continue
                        if word in STANDALONE_COMMANDS:
                            fast_text = STANDALONE_COMMANDS[word]
                        elif word in ACTION_WORDS:
                            fast_action = word
                        elif word in TARGET_WORDS:
                            fast_target = word

                    if fast_text is None and fast_action and fast_target:
                        fast_text = f"{fast_action} {fast_target}"

                    if fast_text is not None:
                        log.info("Fast lane matched: '%s'", fast_text)
                        break

                    voice_prob = vad.predict(chunk, frame_size=FRAME_SAMPLES)
                    if voice_prob >= SILENCE_THRESHOLD:
                        speech_frames_seen += 1
                        silence_run = 0
                    else:
                        silence_run += 1
                        # Gate the early-stop on frames of *actual detected speech*,
                        # not just elapsed time -- there's always a beat of silence
                        # right after the wake word before you start the command,
                        # and gating on elapsed frames alone (the old bug) meant
                        # that natural pause itself was misread as "command over,"
                        # cutting the recording before any command was even said.
                        if speech_frames_seen >= MIN_SPEECH_FRAMES and silence_run >= SILENCE_FRAMES_TO_STOP:
                            break

                # Recording has genuinely ended (fast-lane match, silence
                # timeout, or max length) -- shrink the indicator back now,
                # before transcription/execution.
                if indicator is not None:
                    indicator.deactivate()
                log.info("Recording took %.1fs (wake word end to end-of-speech)", time.monotonic() - recording_start)

                if not frames:
                    print("(didn't catch anything)")
                    command_gate.release()
                    continue

                # Trim the trailing silence that was only recorded to detect
                # end-of-speech -- whisper doesn't need to transcribe dead air,
                # and every second removed here is a second of latency saved on
                # every single command. Keep a small pad so words aren't clipped.
                # (Not relevant on a fast-lane match -- silence_run is whatever
                # it happened to be mid-utterance, so skip the trim there.)
                silence_pad_frames = 10  # ~0.3s
                if fast_text is None and silence_run > silence_pad_frames:
                    frames = frames[: len(frames) - (silence_run - silence_pad_frames)]

                audio = np.concatenate(frames).astype(np.float32) / 32768.0

                # Transcription + execution run on their own thread so a slow or
                # hung action (a network call, an OAuth flow stuck on a browser
                # that never redirects back, anything) can never block wake-word
                # detection -- the loop above keeps listening immediately.
                threading.Thread(
                    target=_run_and_release,
                    args=(audio, whisper_ready, whisper_holder, vocabulary_prompt, indicator, fast_text),
                    daemon=True,
                ).start()
    except KeyboardInterrupt:
        print("\nStopped listening.")


def run() -> None:
    if not _acquire_single_instance_lock():
        log.warning("Another --listen process is already running -- exiting instead of running a duplicate.")
        print("Voice assistant is already running -- exiting.")
        return

    if not _show_indicator():
        _listen_loop()
        return

    # The on-screen indicator (assistant/indicator.py) needs Tkinter's mainloop on
    # the main thread, so the audio/wake-word loop runs on a background thread
    # instead -- pulse() itself is thread-safe, only the drawing happens on the Tk
    # thread. Set SHOW_INDICATOR=0 in config/.env to skip this and run as before.
    from assistant.indicator import WakeIndicator

    indicator = WakeIndicator(corner=os.environ.get("INDICATOR_CORNER", "top-right"))
    thread = threading.Thread(target=_listen_loop, args=(indicator,), daemon=True)
    thread.start()
    try:
        indicator.run()
    except Exception:
        # indicator.run() owns the main thread and _listen_loop is a daemon
        # thread -- if Tk fails to create its window (seen right after a fresh
        # boot, when the Task Scheduler "at log on" trigger can fire before the
        # desktop/display config has actually finished settling) this exception
        # would otherwise propagate up and kill the whole process, taking wake
        # word detection down with it and leaving nothing running at all with
        # no trace in the log. Log it and keep the assistant alive without the
        # indicator instead.
        log.exception("On-screen indicator failed to start; continuing without it")
        thread.join()
