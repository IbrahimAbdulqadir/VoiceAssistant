# Voice Assistant — Progress Log

This captures everything done, current state, and exactly what to pick up next.

## Where things stand right now (end of session)

The project is now a git repo pushed to
**https://github.com/IbrahimAbdulqadir/VoiceAssistant** (`main` branch).
`config/.env` and other secrets are gitignored and were never committed —
double check this stays true before any future commit that touches config/.

The listener runs hidden (no console window) via a self-healing Windows Task
Scheduler job (`VoiceAssistantListener`) instead of a plain Startup shortcut —
see "Autostart" below. `WHISPER_MODEL=base` is the deliberate final choice —
the user explicitly prioritized fast response over transcription accuracy
after experiencing both (see "Performance tuning").

**Biggest unfinished thing**: a "fast lane" instant-command-detection system
(see its own section below) is mid-rollout — a background batch process was
still training the remaining command words when this session ended, and the
listener has **not yet been restarted** to actually load the ones already
trained. Check `wakeword_trainer/batch_train_words2.log` for how far it got
(it's a detached OS process, so it kept running after the session ended)
before doing anything else with the fast lane.

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

## Fast lane: instant command-word detection (built this session, mid-rollout)

The user's core remaining complaint after all the tuning below was: response
is still slow because Whisper has to transcribe the *whole* open-ended
command. His proposal (and it's a good one): since the assistant's actual
vocabulary is small and known (app names, open/close/minimize, a few media
controls), train a dedicated instant openWakeWord-style detector for each of
those words -- exactly like "Spiderman" itself -- instead of transcribing
freeform speech. openWakeWord can run many such small classifiers **off one
shared embedding pipeline** at near-zero extra cost, which is what makes this
practical instead of just moving the slowness around.

**Architecture** (implemented in `assistant/listen.py`):
- `_load_wake_model()` now loads the main "Spiderman" model **plus every
  `.onnx` file in `config/command_words/`** into one `Model(wakeword_models=[...])`
  call. Each file's key is its filename stem (confirmed: openWakeWord keys by
  basename, e.g. `open.onnx` → key `"open"`).
- During the normal post-wake-word recording loop, every frame is also
  checked against all the command-word keys (`FAST_LANE_THRESHOLD=0.85`).
  Three categories, hardcoded as sets/dicts near the top of `listen.py`:
  - `ACTION_WORDS` = open, close, minimize (need a target to complete a command)
  - `TARGET_WORDS` = spotify, chrome, telegram, vscode, notepad (the apps this
    combines with an action word) -- **needs chess and day_one added once
    those are trained** (see below), and needs extending for every other app
    covered in the eventual full rollout
  - `STANDALONE_COMMANDS` = desktop/wifi/notifications/search/battery/play/
    pause/next/previous → maps straight to a complete command string (these
    don't need a second word)
- The instant an action+target pair or a standalone word clears threshold,
  the composed text (e.g. `"open spotify"`) is handed directly to
  `executor.execute()` on the existing background thread, skipping Whisper
  entirely. If nothing resolves before the **existing** silence-timeout logic
  ends the recording, it falls through to Whisper exactly as before -- the
  fast lane is strictly additive, it can only add speed, never remove the
  existing correctness fallback.
- New media-control actions were added for this too since they didn't exist
  yet: `actions.media_play_pause/media_next/media_previous` (standard
  Windows media virtual-keys) plus `play`/`pause`/`next`/`previous` intents
  in `executor.py`.

**Training pipeline** (new tooling in `wakeword_trainer/`, separate from the
VoiceAssistant repo):
- **Found and fixed a real pre-existing bug** in `train_openwakeword.py`:
  `WeightedRandomSampler` was imported but never actually wired into the
  training `DataLoader` (comment said "weighted sampling for imbalanced
  data", code just did `shuffle=True`). Harmless with "Spiderman"'s mild
  ~2.4:1 imbalance, but a single command word has far fewer natural positive
  phrasings (~30 TTS samples vs. the shared 310-file negative set, ~11:1) --
  without real class balancing the model just learned to always predict
  "not the word" (F1 stuck at exactly 0.0000 for 20 straight epochs on the
  first "open" attempt). Fixed by computing per-sample weights and using a
  real `WeightedRandomSampler` for the train loader. After the fix, "open"
  reached F1 0.86 / 97% acc; "close" reached F1 0.95 / 98.9% acc (better,
  since "close" is phonetically less common in ordinary speech than "open").
- **`wakeword_trainer/train_command_word.py`** (new): trains one word end to
  end -- generates TTS positives into `data_words/<word>/positive/`, links
  the shared `data/negative` and `data/confusable` in via Windows directory
  **junctions** (not copies -- `data_words/<word>/negative` and `.../confusable`
  are junctions to the main shared folders, so ~330 shared files aren't
  duplicated per word), trains, and auto-deploys the resulting `.onnx`
  straight to `VoiceAssistant/config/command_words/<word>.onnx`. Usage:
  `python train_command_word.py "spotify"` (add `--extra-phrase "vs code"`
  etc. for alternate phrasings of the same word).
- **`wakeword_trainer/record_command_word_samples.py`** (new): the
  per-word equivalent of the original `record_real_samples.py` -- lets the
  user record real voice takes for a specific command word (saved into the
  same `data_words/<word>/positive/`), since TTS-only training reproduces the
  same accent-recognition weakness the original Spiderman model had before
  real recordings fixed it. **Must be run interactively by the user in a real
  terminal** (needs live mic input synced to prompts) -- not something that
  can be automated. Re-run `train_command_word.py <word>` afterward to
  retrain including the new real samples (TTS generation step skips files
  that already exist, so it's cheap).
- **Batch rollout status at session end**: a batch script
  (`wakeword_trainer/batch_train_words.ps1`, running via a **detached**
  `Start-Process` so it survives past any single tool call/session) was
  training the full word list in sequence. ⚠️ **Caution for next time**: at
  one point during this session, the same batch got launched twice
  concurrently by accident (once as a backgrounded tool call, then again via
  Start-Process without killing the first) -- this caused a real failure (a
  directory-junction race on "close"). Always verify no earlier instance of
  a long-running background job is still alive (`Get-CimInstance Win32_Process`
  filtered on the script/command name) before relaunching one.
  - Done and deployed: `open` (F1 0.86), `close` (F1 0.95), `minimize`,
    `pause`, `next`. Check `wakeword_trainer/batch_train_words2.log` for
    anything that finished after the session ended.
  - Failed and needs a manual retry: `play` (a transient "Connection error"
    from the OpenAI TTS API killed all 30 attempts in one go -- not a real
    bug, just retry `python train_command_word.py "play"`).
  - Still queued in the batch: `previous`, `desktop`, `wifi`,
    `notifications`, `search`, `battery`, `spotify`, `chrome`, `telegram`,
    `vscode`, `notepad`.
  - **Not in the batch at all, but the user already recorded real voice
    samples for them** (found in `data_words/chess/positive/` and
    `data_words/day_one/positive/`, 15 real takes each) -- these need
    `python train_command_word.py "chess"` and `"day_one"` run manually, then
    added to `TARGET_WORDS` in `listen.py`. "day_one" is presumably the Day
    One journaling app; "chess" wasn't otherwise discussed.
  - `spotify` already has 15 real voice takes recorded too, sitting in
    `data_words/spotify/positive/` waiting for the batch to reach it (or a
    manual re-run) -- these get included automatically since
    `train_command_word.py` only regenerates TTS files that don't already
    exist and picks up everything else in the positive dir.
- **The listener has not been restarted since deploying any of these
  models** -- `_load_wake_model()` only picks up `config/command_words/*.onnx`
  at startup, so none of the fast lane is actually live yet even though
  several models are already deployed there. Restart the
  `VoiceAssistantListener` scheduled task once the rollout is far enough
  along to be worth testing.

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
- **"open vscode" piling up extra windows instead of reusing one**: the VS
  Code CLI opens a brand new window on every invocation unless told
  otherwise. Fixed by always passing `--reuse-window` in `actions.open_vscode`.
  Separately, the VS Code CLI (`code`) wasn't on PATH at all for the account
  running the listener -- found the real install
  (`C:\Users\SPIDER MAN\Microsoft VS Code\bin\code.cmd`) and added it to the
  user's permanent PATH.
- **New system commands added**: "show desktop" (toggles show-desktop via
  `Shell.Application.ToggleDesktop()`), "open notifications" / "open search"
  (simulated Win+N / Win+S), "show wifi" (opens `ms-settings:network-wifi`),
  "show battery" (reports actual `psutil.sensors_battery()` percentage, not
  just a UI), "mute/unmute speaker" (simulated volume-mute key). See
  `actions.py`/`executor.py` for all of these plus the media-control ones
  listed under "Fast lane" above.

## What's NOT done yet

- **Finish the fast-lane rollout** — see its section above for the exact
  state: retry `play`, train `chess`/`day_one`, work through the remaining
  queued words, decide how many need real-voice recordings, add new target
  words to `TARGET_WORDS` in `listen.py` as they're trained, and actually
  **restart the listener** to load whatever's been trained so far.
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
- **Rotate the OpenAI API key** — flagged for rotation multiple times now
  across multiple sessions and still not done. Go to
  platform.openai.com/api-keys, revoke/regenerate, paste the new one into
  `config/.env` directly (never through chat).

## Config file reference (`config/.env`)

Currently set: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REDIRECT_URI`, `WAKE_WORD=Spiderman`, `OPENAI_API_KEY`,
`WAKE_THRESHOLD=0.9`, `SILENCE_SECONDS=1.0`, `WHISPER_MODEL=base` (explicit
user choice — prioritizes speed over accuracy, see "Performance tuning").
Still commented out/unset: `SPEAKER_THRESHOLD`, `OPENAI_MODEL`,
`ANTHROPIC_API_KEY` (unset — using OpenAI instead), `INDICATOR_CORNER`,
`SHOW_INDICATOR`, `FAST_LANE_THRESHOLD` (code default 0.85).

## Full details

See `README.md` in this project for the original setup/architecture writeup —
it has not been updated with this session's changes (autostart mechanism,
performance tuning, action fixes, wake model retrain); this file is the
current source of truth until README is reconciled.
