# Voice Assistant

A wake-word-triggered, speaker-verified automation layer for Windows (see
`voice-assistant-scope.md` for the full 4-phase plan). All four phases are
implemented: **Phase 1** (the action layer), **Phase 2** (wake word + local
speech-to-text), **Phase 3** (speaker verification, so it only acts on your
voice), and Phase 4's LLM tool-calling fallback for open-ended phrasing.

## Run it

```
pip install -r requirements.txt
python main.py            # typed REPL (no microphone needed)
python main.py --enroll   # one-time voice enrollment (Phase 3)
python main.py --listen   # wake word + voice (no account needed -- see below)
```

Type commands at the `>` prompt. Type `help` to see the list, `exit` to quit.

## What it can do right now

| Say/type | Does |
|---|---|
| `open chrome` / `open spotify` / `open <anything>` | Launches an app -- auto-discovered from your Start Menu shortcuts and Microsoft Store aliases, fuzzy-matched so near-misses still resolve |
| `close <app>` | Finds the running process(es) and asks to confirm before terminating |
| `open <x> in chrome` | Opens a URL, or a Google search, specifically in Chrome (not whatever your OS default browser is) |
| `play <x> on spotify` | Opens Spotify's desktop app straight to search results for a song/artist |
| `open mail` / `search mail for <x>` | Opens Gmail in your browser, optionally scoped to a search |
| `open vscode [in <path>]` | Opens VS Code via its CLI, optionally at a folder |
| `open file <path>:<line>` | Opens VS Code jumped straight to a line |
| `open folder <path>` | Opens a folder in File Explorer |
| `open url <url>` | Opens a URL in the default browser |
| `run <script>` | Runs a command from the whitelist in `config/apps.yaml` -- nothing not listed there is runnable |
| `list apps` | Lists every app name the assistant currently knows |
| *anything else* | Falls back to an LLM (OpenAI, if `OPENAI_API_KEY` is set) that picks the right action itself -- e.g. "can you open up notepad for me please" still works even though no regex above matches that exact phrasing |

## Why it's structured this way

- **`assistant/app_discovery.py`** scans Start Menu `.lnk` shortcuts and the
  Microsoft Store's app-execution-alias folder, so most installed apps are openable
  by name with zero manual configuration.
- **`assistant/resolver.py`** does fuzzy matching (rapidfuzz) on top of that, so
  slightly-off names still resolve -- this matters a lot now that Phase 2 feeds it
  Whisper transcriptions instead of exact typed text.
- **`assistant/listen.py`** is Phase 2's front-end: openWakeWord listens for the
  wake word doing nothing else (no transcription, no cloud calls) until triggered;
  its bundled Silero VAD then gates the recording so it stops on silence instead of
  a fixed duration; faster-whisper transcribes locally, no audio ever leaves the
  machine. Fully open-source, no account of any kind -- this replaced an earlier
  Picovoice-based design after their signup started rejecting personal emails. The
  transcribed text goes to the exact same `executor.execute()` the typed REPL uses.
- **`assistant/actions.py`** is the leaf-level "do the thing" layer: open, close,
  run, browse. Every action returns a string rather than printing, so a future
  voice/TTS front-end can read results aloud instead of printing them.
- **`assistant/integrations.py`** holds the app-specific tricks for apps that
  support more than a plain launch: Chrome via its own binary, and Spotify/Gmail via
  their real APIs (**`spotify_client.py`**, **`gmail_client.py`**) when credentials
  are configured (see setup section below) -- with a no-auth fallback (Spotify's
  `spotify:` URI scheme, opening Gmail in the browser) when they aren't, so the
  assistant works before you've registered either app.
- **`assistant/intents.py`** + **`assistant/executor.py`** are a tiny regex-based
  router: text in, handler out. It handles the fast, common cases with zero API
  calls; anything it can't match falls through to **`assistant/llm_backend.py`**,
  which exposes the same `actions`/`integrations` functions as tools to an LLM
  (OpenAI's Responses API) so open-ended phrasing still resolves to a real action.
  This only runs on the regex router's misses, which is also what keeps it cheap in
  practice -- see the cost note below.
- **`assistant/voiceprint.py`** + **`assistant/enroll.py`** are Phase 3: one
  averaged voice embedding built from several `--enroll` recordings, checked via
  cosine similarity against every command `--listen` records. `verify()` defaults
  to `True` when nothing's enrolled yet, so this is opt-in hardening on top of
  Phase 2, never a lockout by default.
- **`config/apps.yaml`** is the one file meant to be hand-edited: fix a
  mis-discovered app path, add a script to the run-whitelist, or extend the
  protected-processes list.

### Safety rails
- A **protected-process list** (`explorer.exe`, `lsass.exe`, etc.) that `close`
  refuses to touch no matter what.
- **Confirmation before closing anything** -- shown exactly which process(es)
  would be killed before it happens.
- **No arbitrary shell execution** -- `run <x>` only works for commands explicitly
  whitelisted in `config/apps.yaml`.
- Every command executed is logged to `logs/assistant.log` (rotating, so it won't
  grow forever) for later auditing once voice, not typing, is driving this.

## Tests

```
python -m unittest discover -s tests -v
```

Covers intent routing (right text -> right handler) and the safety rails (protected
processes refused, confirmation respected, unwhitelisted scripts refused) without
actually opening or closing anything on the machine running them.

## Wake word + voice input setup (Phase 2)

**No account or API key needed at all** -- this runs on
[openWakeWord](https://github.com/dscripka/openWakeWord), a fully open-source wake
word engine (an earlier version of this used Picovoice, but their signup started
rejecting personal/non-company emails, so this was swapped out entirely).

**Run it:**
```
python main.py --listen
```
The first run downloads openWakeWord's pretrained models and faster-whisper's
speech-to-text weights automatically (no signup -- just a one-time download,
cached afterward). Say **"hey jarvis"** (the default wake word), then speak your
command once you see "Wake word detected". It stops recording automatically about
1.3 seconds after you stop talking, transcribes locally with Whisper, and runs it
through the same executor as the typed REPL.

**Picking a different built-in wake word:** openWakeWord ships these pretrained --
drop any into `WAKE_WORD=` in `config/.env`: `alexa`, `hey_mycroft`, `hey_jarvis`,
`hey_rhasspy`, `timer`, `weather`.

**Training a custom wake word ("Spiderman") -- already done for this project,
kept here for reproducing it or training a different word:**

The official openWakeWord Colab notebook turned out to be broken against Colab's
current Python 3.13 runtime (multiple unrelated dependency bugs, confirmed by other
users hitting the same thing with no fix upstream yet) -- so this project instead
uses [bbarrick/wakeword_trainer](https://github.com/bbarrick/wakeword_trainer), a
local, fully command-line-drivable tool that generates training audio via OpenAI's
TTS API (reusing the same `OPENAI_API_KEY` already in `config/.env`) and exports
straight to ONNX.

1. Needs **Python 3.10-3.12** specifically (3.13+ has issues) -- if you only have a
   newer Python, install one with the Python Launcher: `py install 3.12`.
2. `git clone https://github.com/bbarrick/wakeword_trainer.git`, then inside it:
   `py -3.12 -m venv venv312` to create an isolated environment.
3. `winget install ffmpeg` (needed for MP3->WAV conversion; open a new terminal
   afterward so PATH picks it up).
4. Edit `config.py` in that repo: set `WAKE_WORD = "Spiderman"` and
   `MODEL_NAME = "spiderman"`.
5. `.\venv312\Scripts\python.exe setup.py` -- installs dependencies and downloads
   openWakeWord's base models.
6. Generate training audio (30 positive TTS samples of the wake word, plus
   confusable/negative/silence samples) -- either through the GUI (`python app.py`)
   or headlessly:
   ```
   $env:OPENAI_API_KEY = "<your key>"
   .\venv312\Scripts\python.exe generate_negatives.py --wake-word "Spiderman"
   ```
   (Positive-sample generation is GUI-only in the upstream tool; this project adds
   a small `generate_positives.py` companion script that calls the same
   `generate_with_openai()` function headlessly with the GUI's default 6 voices x
   5 speeds.)
7. `.\venv312\Scripts\python.exe train_openwakeword.py` -- trains and
   auto-exports to `output/<model_name>.onnx`. Took under 2 minutes locally; our
   run hit F1 0.96 / 97.6% validation accuracy / 0% false-positive rate on held-out
   synthetic data.
8. Copy the result to `config/wake_word.onnx` in this project. Once that file
   exists it takes priority over `WAKE_WORD` automatically -- nothing else to
   change. (Until it exists, the assistant safely falls back to `"hey_jarvis"` and
   tells you why, rather than crashing.)
9. `python main.py --listen`, say **"Spiderman"**, then your command.

**Training on your actual accent (recommended if it's struggling to hear you):**
A model trained only on OpenAI's TTS voices (alloy, echo, fable, nova, onyx,
shimmer) has never heard anything like your real voice/accent -- no amount of
synthetic samples fixes that. Fix it with real recordings of yourself instead:
1. `cd wakeword_trainer`, then:
   ```
   .\venv312\Scripts\python.exe record_real_samples.py
   ```
2. It walks you through 20 takes each of every phrase in `PHRASES`
   (`Spiderman`, `Hi Spiderman` by default) -- press Enter, say the phrase, repeat.
   Vary tone/pace/volume naturally across takes rather than trying to sound
   consistent; the variation is what generalizes.
3. Saves into `data/positive/` alongside the synthetic samples (doesn't replace
   them, just adds real examples to the same positive class).
4. `.\venv312\Scripts\python.exe train_openwakeword.py` to retrain on the
   combined set, then copy `output/spiderman.onnx` to `config/wake_word.onnx` as
   in step 8 above.

**Note on false positives:** a model trained only on synthetic TTS negatives (no
real ambient room noise) can trigger occasionally on background sound/conversation
-- we saw one such trigger at score 0.56 in testing. `WAKE_THRESHOLD` in
`config/.env` defaults to `0.6` for this reason (higher than openWakeWord's usual
0.5) -- raise it further if false triggers are frequent, or retrain with more/
noisier negative samples for a more robust model.

**Whisper model size:** defaults to `base` (good balance of speed/accuracy on CPU).
Set `WHISPER_MODEL=tiny` in `config/.env` for faster/less accurate, or `small`/
`medium` for slower/more accurate. The first run of a given size downloads its
weights once (cached afterward) -- expect a pause the very first time you speak,
longer on a slow connection since it's roughly 150MB for `base`.

## Speaker verification setup (Phase 3)

Without this, `--listen` reacts to *anyone* saying the wake word. Enrolling your
voice locks it to you specifically -- everyone else's command gets silently ignored.

1. `python main.py --enroll`.
2. It records 6 short (4s) clips. What you say doesn't matter -- only your voice
   does -- but vary the *conditions* between takes if you can: quiet room, some
   background noise, tired voice, rushed voice. This matches how the voiceprint
   actually generalizes (per `voice-assistant-scope.md`): vocal characteristics,
   not vocabulary.
3. It saves one averaged voiceprint to `config/voiceprint.npy`. That's it --
   `python main.py --listen` picks it up automatically next run and starts
   gating on it; you'll see "voice-locked to you" in the startup message instead
   of "responds to anyone."
4. If it's rejecting *you* too often (or accepting others too easily), tune
   `SPEAKER_THRESHOLD` in `config/.env` (default `0.72`) -- lower is more lenient,
   higher is stricter. Re-run `--enroll` any time to replace the voiceprint (e.g.
   after a long illness changes your voice, or if you skipped varying conditions
   the first time).

## Getting real Spotify playback + Gmail search (optional)

Without these, `play X on spotify` opens the desktop app's search results (you press
play yourself) and `search mail for X` / `open mail` opens Gmail in your browser.
Setting these up makes both actually happen with no further clicking. Each is a
one-time registration step on that company's site, then the code you already have
handles everything else automatically.

### Spotify Web API setup

**Get credentials:**
1. Open [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   and log in with your regular Spotify account (free or Premium both work for
   getting credentials; actually *starting playback* requires Premium).
2. Click **Create app**.
3. Fill in the form:
   - **App name** / **App description** -- anything, e.g. "My Voice Assistant".
   - **Redirect URI** -- type `http://127.0.0.1:8888/callback` exactly, then click
     **Add**. This must match `SPOTIFY_REDIRECT_URI` in your `.env` character-for-
     character, including the port number.
   - **Which API/SDKs are you planning to use?** -- check **Web API**.
   - Check the terms checkbox, click **Save**.
4. You're now on the app's page. Click **Settings** (top right).
5. You'll see **Client ID** right away. Click **View client secret** to reveal the
   **Client Secret**. Copy both.

**Wire them into the project:**
6. In `config/`, copy `.env.example` to a new file named `.env`.
7. Open `config/.env` and paste in:
   ```
   SPOTIFY_CLIENT_ID=<paste Client ID>
   SPOTIFY_CLIENT_SECRET=<paste Client Secret>
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
   ```
8. Save the file. Nothing else to configure -- `assistant/spotify_client.py` reads
   these automatically on next run.

**First run:**
9. Have Spotify open and playing (or paused) on some device -- phone, this PC,
   a speaker -- so there's an *active device* to send playback to.
10. `python main.py`, then type `play <a song> on spotify`.
11. A browser window opens once asking you to log in and click **Agree** to
    authorize the app. After that one approval, a token is cached in
    `config/.spotify_cache` and it won't ask again.

**Notes:**
- Your own account works immediately -- new Spotify apps start in "Development
  mode," which is fully functional for the account that created the app, no review
  needed. (Development mode only *blocks* other people's accounts until you
  allowlist them under **Users and Access** in the app dashboard -- irrelevant if
  it's just for you.)
- If playback silently does nothing, it's almost always "no active device" --
  open the Spotify app somewhere first.

### Gmail API setup

**Create the project and enable the API:**
1. Open [console.cloud.google.com](https://console.cloud.google.com/) and sign in.
2. Top left, click the project dropdown -> **New Project**. Name it anything
   (e.g. "voice-assistant") -> **Create**. Wait for it to switch to that project
   (check the dropdown at top shows it selected).
3. Left sidebar (or hamburger menu) -> **APIs & Services** -> **Library**.
4. Search `Gmail API` -> click it -> click **Enable**.

**Configure the OAuth consent screen:**
5. **APIs & Services** -> **OAuth consent screen**.
6. User type: **External** -> **Create**.
7. Fill in the required fields: **App name** (anything), **User support email**
   (your email), **Developer contact email** (your email). Click **Save and
   Continue** through Scopes (skip, leave default) and Test users.
8. On the **Test users** step, click **+ Add Users**, type your own Gmail address,
   click **Add**, then **Save and Continue**.
9. Leave **Publishing status** as **Testing** -- this is sufficient for personal
   use and requires no Google review, since only test users (you) can use it.

**Create the OAuth client credentials:**
10. **APIs & Services** -> **Credentials** -> **+ Create Credentials** ->
    **OAuth client ID**.
11. **Application type**: **Desktop app**. Name it anything -> **Create**.
12. A popup shows your client ID/secret -- click **Download JSON**.
13. Rename the downloaded file to exactly `gmail_credentials.json` and move it
    into this project's `config/` folder (`config/gmail_credentials.json`).

**First run:**
14. `python main.py`, then type `search mail for <something>`.
15. A browser window opens once for consent -- log in with the *same account* you
    added as a test user, click through the "unverified app" warning (**Advanced**
    -> **Go to \<app name\> (unsafe)** -- this warning only appears because the app
    is in Testing mode under your own project; it's expected, not a real risk here)
    -> **Continue** -> **Continue** to grant access.
16. A refresh token is cached in `config/gmail_token.json` -- it won't ask again
    after this.

**Notes:**
- Only the `gmail.readonly` scope is requested -- it can search/read mail, not
  send, delete, or modify anything.
- Because the app stays in "Testing" status (the right choice for personal use --
  publishing to "Production" with this scope requires a Google verification
  review), Google may periodically expire the cached token and ask you to
  re-consent. If that happens, just repeat step 15 -- delete
  `config/gmail_token.json` first if it doesn't prompt automatically.

## LLM fallback setup (Phase 4, already wired in)

Set `OPENAI_API_KEY` in `config/.env` and any command that doesn't match a regex
pattern above is handed to `assistant/llm_backend.py`, which exposes every action
as a tool and lets the model pick one. No other setup needed -- `is_configured()`
just checks whether the key is present, and the executor falls through to the old
"Didn't understand" message if it isn't.

- **Model**: defaults to `gpt-5-nano` (cheapest OpenAI model with function calling,
  plenty capable for picking the right tool from a short command). Override with
  `OPENAI_MODEL` in `config/.env` if you want more accuracy on ambiguous phrasing
  (e.g. `gpt-5-mini`).
- **Cost**: this only runs on the regex router's misses, and each call is a short
  prompt plus a handful of tool definitions -- expect a small fraction of a cent
  per fallback command, not per command overall.
- **Safety**: the LLM calls the exact same `actions.py`/`integrations.py` functions
  the regex router calls -- `close_app` still refuses protected processes and asks
  for confirmation, `run_script` still refuses anything not in the
  `config/apps.yaml` whitelist. The LLM can pick which tool to call; it can't
  bypass what those tools refuse to do.

## Roadmap (from voice-assistant-scope.md)

- **Phase 2 (done)** -- openWakeWord wake word detection + its bundled Silero VAD +
  faster-whisper transcription, feeding straight into `executor.execute()` (see
  setup above). Built-in keyword by default; custom-trained wake word supported.
  Fully open-source, no account needed for any of it.
- **Phase 3 (done)** -- Resemblyzer speaker-verification gate between wake word and
  execution (see setup above), so it only acts on your voice. Opt-in: until you
  run `--enroll`, `--listen` responds to anyone, matching the scope doc's intended
  rollout ("no speaker verification yet" is the starting state, not a bug).
- **Phase 4 (partially done)** -- LLM tool-calling fallback is implemented (see
  above); still open: true multi-step chained commands ("open the repo and run the
  tests" as one command spanning several tool calls), and extending Spotify/Gmail
  to sending mail and playlist control.

Every dependency in `requirements.txt` is active -- nothing left commented out.

## Prior art consulted

Patterns borrowed from existing open-source projects while designing this:
- [garbit/whisper-voice-assistant](https://github.com/garbit/whisper-voice-assistant)
  -- Porcupine wake word + Cobra VAD + Whisper pipeline shape (Phase 2 reference).
- [frymanofer/Python_WakeWordDetection](https://github.com/frymanofer/Python_WakeWordDetection)
  -- speaker-verification-as-a-gateway placed between wake word and STT (Phase 3
  reference).
- [Yuvakunaal/AI-Voice-Desktop-Assistant](https://github.com/Yuvakunaal/AI-Voice-Desktop-Assistant)
  -- deep app/file discovery instead of hardcoded paths, which is why
  `app_discovery.py` exists instead of a static app list.
