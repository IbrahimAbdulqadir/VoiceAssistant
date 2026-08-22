# Voice Assistant — Progress Log

This captures everything done, current state, and exactly what to pick up next.

## Where things stand right now

The listener runs hidden (no console window) via a self-healing Windows Task
Scheduler job (`VoiceAssistantListener`) instead of a plain Startup shortcut —
see "Autostart" below. It's currently up and running with all fixes below
applied, including a freshly retrained wake word model. **Not yet confirmed
live by the user**: whether the retrained model actually stops false-triggering
on ordinary background speech in practice — that's the next thing to verify.

To manually restart it after a config/code change:
```powershell
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*main.py*listen*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName "VoiceAssistantListener"
```
Then tail `logs/assistant.log` and wait for `Wake word listener started` (can
take ~1-2 min: whisper model load + a warm-up transcription pass).

**Gotcha on this machine**: a plain `python` on PATH doesn't resolve from every
shell (some environments here lack the WindowsApps alias folder on PATH). Use
the real interpreter directly if `python` isn't found:
`C:\Users\SPIDER MAN\AppData\Local\Python\pythoncore-3.14-64\python.exe` (or
`pythonw.exe` for the hidden/no-console variant).

## What's fully built and working

- **Phase 1 (command executor)** — `python main.py` typed REPL. Open/close/
  minimize apps (auto-discovered from Start Menu + WindowsApps aliases,
  fuzzy-matched), VS Code control, folder/URL opening, whitelisted script
  running.
- **Phase 2 (wake word + voice)** — `python main.py --listen`. openWakeWord
  (fully local) + bundled Silero VAD + faster-whisper (local transcription,
  `base` model — see "Performance tuning" below for why).
- **Phase 3 (speaker verification)** — `python main.py --enroll` records your
  voice and creates `config/voiceprint.npy`. **Still not run** — `--listen`
  currently responds to anyone whose speech passes the wake word, not just you.
- **Phase 4 (LLM fallback)** — anything the regex router in `executor.py`
  doesn't match gets handed to OpenAI (`gpt-5-nano` by default) via
  `assistant/llm_backend.py`, which can call any of the same actions.
- **Custom wake word "Spiderman" / "Hi Spiderman"** — trained locally via
  `bbarrick/wakeword_trainer` (cloned to
  `C:\Users\SPIDER MAN\Downloads\wakeword_trainer`, own Python 3.12 venv at
  `wakeword_trainer\venv312`).
  - **Retrained this session** to fix false-triggering on ordinary background
    speech (it was firing on real conversational audio, not just silence/room
    noise — evidence: log showed 15+ wake detections in ~2 min with no command
    following any of them, scores 0.75-0.90, same range as genuine
    detections — threshold-tuning alone couldn't separate the two
    distributions). Fix: added ~150 new negative-sample phrases to
    `augmentation.py`'s `get_common_phrases()` — full conversational sentences
    and narration-style text, not just short discrete phrases — then generated
    204 new TTS negative clips (`generate_negatives.py`, 2 speed variants each)
    and retrained. Negative set grew from 106 → 310 files. New model:
    F1 0.97, accuracy 98.3%, FPR ~1.2% (evaluated against the harder negative
    set, so not directly comparable to the old model's F1 0.98). Deployed to
    `config/wake_word.onnx`; **the pre-retrain model is backed up at
    `config/wake_word.onnx.backup`** in case the new one needs to be rolled
    back.
  - Original training data mix (still the bulk of the positive set): 90
    synthetic OpenAI TTS positive samples + 40 real recordings of the user's
    voice + 45 real ambient room-noise negatives. See git history in
    `wakeword_trainer` for that original session's details.
- **On-screen indicator** (`assistant/indicator.py`) — small tilted spider-web
  icon, top-right corner by default, widens/reddens while actively listening
  for a command, shrinks back when recording ends.
- **Command execution runs on its own thread** (`_process_command` in
  `listen.py`) — a slow/hung action can't freeze wake-word detection.
- **Spotify**: real desktop app installed and working for "open spotify".
  Web API OAuth for real playback control (`spotify_play`) still **not
  confirmed working end-to-end** — was failing with a redirect URI mismatch
  error, not revisited this session.
- **Autostart — now hidden + self-healing (changed this session)**:
  - Runs via `pythonw.exe` (no console window at all — the old visible-console
    Startup shortcut could be, and once was, accidentally closed by the user,
    killing the whole process incl. the indicator).
  - Registered as Windows Task Scheduler job **`VoiceAssistantListener`** with
    two triggers: *at log on*, and a *repeating 5-minute heartbeat* with
    "ignore new instance if already running" — so if the process ever dies
    mid-session (crash, killed, etc.) it's back within 5 minutes without
    needing a fresh Windows login. Also configured to auto-restart up to 5
    times, 1 min apart, on task failure.
  - The old plain Startup-folder shortcut was renamed (not deleted) to
    `VoiceAssistant.bat.disabled` in
    `C:\Users\SPIDER MAN\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`
    — restore by renaming it back if the scheduled task setup is ever reverted.
  - Caveat: a lock-screen unlock does **not** re-trigger the "at log on"
    trigger (only a full sign-out/sign-in does) — the 5-minute heartbeat is
    what recovers it after a mid-session crash, not the unlock itself.

## Performance tuning done this session

- **Whisper model**: was `small` + `beam_size=5` — measured from logs at
  ~20-25 seconds between end-of-speech and command execution on this 4-core
  machine. Switched to `base` + `beam_size=1` (greedy) + explicit
  `cpu_threads=os.cpu_count()`, plus a one-time warm-up transcription at
  startup so the first real command doesn't eat ctranslate2's cold-start cost.
  Tried `base.en` (English-only variant, normally a free speed/accuracy win)
  but it hung trying to download fresh weights over this machine's flaky
  network — reverted to plain `base`, which was already cached and confirmed
  working. If accuracy with `base` isn't good enough, set
  `WHISPER_MODEL=small` in `config/.env` to trade speed back for accuracy.
- **Silence timeout**: `SILENCE_SECONDS` lowered from 5.0 → 1.0 in
  `config/.env`. **Important bug fixed alongside this**: the recording loop in
  `listen.py` originally gated "can I stop early" on *total elapsed frames*
  since the wake word, not on frames of *actual detected speech*. Combined
  with a low silence timeout, this meant the natural pause after saying "Hi
  Spiderman" — before you even start the command — was being misread as "the
  command is over," cutting recording off before anything was captured. Fixed
  by tracking `speech_frames_seen` (only incremented when the VAD actually
  detects voice) and gating the early-stop on that instead. This was a real
  regression introduced and then fixed within this same session — worth
  remembering if response timing ever gets tuned again.
- **Wake threshold**: `WAKE_THRESHOLD` raised 0.6 → 0.75 → 0.85 → 0.9 over the
  course of this session while chasing false triggers, before concluding
  threshold-tuning alone couldn't fix it (see retraining above).

## Bugs fixed this session

- **`close <app>` / `minimize <app>` failing even when the app was clearly
  running**: Whisper transcribes with trailing punctuation ("close spotify.")
  which broke the exact/substring match against running process names (the
  fuzzy-matched `open_app` tolerated it fine, which is why "open" always
  worked but "close" didn't). Fixed by stripping trailing punctuation both at
  the transcription step in `listen.py` and defensively inside
  `close_app`/`minimize_app`'s name matching in `actions.py`.
- **Voice-issued "close app" silently hanging forever**: `close_app` asked for
  a typed y/N confirmation via `input()` — voice has no way to answer that,
  and under hidden `pythonw.exe` there isn't even a console to show the
  prompt. Added `actions.VOICE_MODE` (set `True` once by `listen.py` at
  startup) which skips confirmation for voice-issued close/run-script
  commands; the typed CLI still asks as before. Speaker verification (once
  enrolled) is the real gate on who can issue a command at all.
- **Duplicate app instances on "open X" when X was already running**: added
  a check in `open_app` (`actions.py`) — if the target process is already
  running, it brings the existing window to the foreground (via
  `win32gui`/`win32process`, with the Alt-tap foreground-lock workaround)
  instead of launching a second copy.
- **"minimize \<app\>" didn't exist at all**: added `actions.minimize_app` +
  a matching intent in `executor.py`.

## What's NOT done yet

- **Confirm the retrained wake model actually behaves** in real use — say
  "Spiderman"/"Hi Spiderman" only, and separately have a normal conversation
  nearby, and see if it stays quiet during the latter. If it's still
  triggering on speech, the next lever is either raising `WAKE_THRESHOLD`
  further (diminishing returns — real/false scores already overlap) or a
  second retraining round with negatives specific to whatever's actually
  triggering it.
- **Gmail API** — `config/gmail_credentials.json` was never set up. `open
  mail` / `search mail for X` just opens Gmail in the browser.
- **Speaker verification enrollment** — run `python main.py --enroll`.
- **Confirm Spotify OAuth playback control** — "play \<song\> on spotify" via
  the real Web API, not just launching the app.
- **Rotate the OpenAI API key** — it's been pasted in plain text into chat
  sessions more than once now (including this one, incidentally, via a config
  file read). Go to platform.openai.com/api-keys, revoke/regenerate, paste
  the new one into `config/.env` directly.

## Config file reference (`config/.env`)

Currently set: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REDIRECT_URI`, `WAKE_WORD=Spiderman`, `OPENAI_API_KEY`,
`WAKE_THRESHOLD=0.9`, `SILENCE_SECONDS=1.0`. Still commented out/unset:
`WHISPER_MODEL` (code default now `base`, was `small`), `SPEAKER_THRESHOLD`,
`OPENAI_MODEL`, `ANTHROPIC_API_KEY` (unset — using OpenAI instead),
`INDICATOR_CORNER`, `SHOW_INDICATOR`.

## Full details

See `README.md` in this project for the original setup/architecture writeup —
it has not been updated with this session's changes (autostart mechanism,
performance tuning, action fixes, wake model retrain); this file is the
current source of truth until README is reconciled.
