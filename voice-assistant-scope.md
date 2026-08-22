# Personal Voice Assistant — Project Scope

## What this actually is
A wake-word-triggered, speaker-verified automation layer for Windows. It listens for its name, confirms the voice is yours, transcribes what follows, figures out what you want, and executes it, opening apps, closing them, driving VS Code, running scripts.

This is buildable. Not in a weekend, but in stages, each one usable on its own.

## The four layers

**1. Wake word detection**
Runs constantly, listening only for its name. Doesn't transcribe or send audio anywhere until triggered. Porcupine (Picovoice) is the standard choice here, it can train a custom wake word from a handful of samples and runs locally with near-zero latency.

**2. Speaker verification**
This is the part that makes it respond to your voice alone. Not the same as wake word detection, wake word just catches the phrase, speaker verification confirms it's *you* saying it. You enroll by recording a set of samples (different times of day, different moods, background noise on and off), and a voice embedding model (SpeechBrain's ECAPA-TDNN or Resemblyzer) builds a voiceprint. Every trigger gets checked against that print before anything executes.

You don't need to say "every possible" phrase. Voiceprints are built from vocal characteristics, not specific words. What actually matters is variety in *conditions*, not vocabulary: quiet room, noisy room, tired voice, rushed voice, 20-30 short clips is plenty.

**3. Speech-to-text + intent routing**
Once wake word + speaker check pass, it starts recording your command and transcribes it. Whisper (OpenAI, runs locally) is the standard here since it doesn't need internet and handles accents well. The transcribed text then gets routed to an intent handler, either simple pattern matching for early commands ("open chrome" → launch chrome.exe) or, once you want it doing real tasks, an LLM with tool-calling that decides what action to take.

**4. Action execution**
The actual doing. On Windows this is a mix of:
- `subprocess`/`os.startfile` for opening apps
- `psutil` to find and kill processes for closing them
- `pyautogui` or `pywinauto` for UI interaction (clicking, typing into windows)
- Direct API/CLI calls for things like VS Code, which has a command-line interface (`code .`, `code --goto file:line`) that's far more reliable than simulating keystrokes

## Build order (each phase works standalone)

**Phase 1 — Command executor, no voice yet**
Build the action layer first: a script that takes text commands and does things (open Chrome, close Chrome, open VS Code in a folder, run a command). Test it by typing commands. This is the foundation everything else plugs into, and it's the part where "it does what I say" actually gets proven out.

**Phase 2 — Wake word + push-to-verify**
Add Porcupine listening for your custom name. On trigger, record a few seconds, transcribe with Whisper, feed the text into Phase 1's executor. No speaker verification yet, so it'll respond to anyone, but you already have "say the name, thing happens."

**Phase 3 — Speaker verification**
Enroll your voiceprint, add the verification check between wake word and execution. Now it only acts on you.

**Phase 4 — Expand the task set**
This is where it grows from "open/close apps" into "carry out tasks." Give it an LLM backend with tool access (file operations, running your scripts, git commands, maybe controlling browser tabs) so commands like "enter VS Code and run the tests" become a chain of actions instead of one hardcoded case.

## What "huge" actually costs
Being upfront: the wake word and speaker verification pieces are the easy, well-solved part, a weekend each. Phase 4, the part where it handles arbitrary spoken tasks reliably, is the part that never really finishes. Every new capability is new code. Scope it as a living project, not a thing with an end date.

## Stack summary
| Layer | Tool |
|---|---|
| Wake word | Porcupine |
| Speaker verification | SpeechBrain ECAPA-TDNN or Resemblyzer |
| STT | Whisper (local) |
| Intent/task logic | Python rules → LLM tool-calling as it grows |
| App control | subprocess, psutil, pywinauto |
| VS Code control | VS Code CLI (`code`) |

## Next step
Phase 1 is pure Python, no audio yet, just proving the executor works. Want to start there?
