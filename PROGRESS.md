# Voice Assistant — Progress Log

This captures everything done, current state, and exactly what to pick up next.

## Follow-up session (2026-08-25, even later) — Notepad opened on its own; wake word was picking up background conversation and the LLM fallback acted on it as a real command

User restarted the listener (see entry below) and separately noticed Notepad
had opened with no idea why. Root-caused from `logs/assistant.log`, not
guessed:

```
12:05:31 Transcribed: 'Call in text and notepad. You want to go compromise.
          You do not play chess there. Its better to be positive and active'
12:05:37 LLM backend calling open_app({'name': 'notepad'})
12:05:38 Opened notepad (...\WindowsApps\notepad.exe)
```

The wake word had triggered on nearby background conversation (reads like a
chess discussion) that was never directed at the assistant at all. Whisper
transcribed it into that garbled sentence, which happened to contain the
word "notepad." No regex intent matched the full sentence, so it fell
through to the LLM fallback (`assistant/llm_backend.py`) -- whose system
prompt at the time just said "call the single most appropriate tool," with
no instruction to recognize "this isn't actually a command" and decline. It
picked "notepad" out of the noise and actually opened it. Other
transcriptions in the same log window were clearly background chatter too
(e.g. "Wait, do they teach campback opening for black"), just not ones that
happened to contain a tool-matching word, so they didn't cause a visible
action.

Asked the user how to handle it (declining vs. a wake-word confidence check
vs. leaving it) -- they chose declining. Fix: rewrote `SYSTEM_PROMPT` in
`assistant/llm_backend.py` to explicitly frame the input as a wake-word
voice transcription that may not have been directed at the assistant at
all, and instruct it to only call a tool when the text plausibly reads as a
direct imperative command ("open X" / "close Y" / "play Z"), not when it
merely contains a word that resembles one inside a conversational or
garbled sentence -- in that case it should respond with plain text instead
of guessing. No unit test possible for the LLM's actual judgement (this
backend calls the real OpenAI API and every existing test mocks
`llm_backend.handle` entirely rather than exercising it); all 56 existing
tests still pass unchanged. Worth relistening for whether this actually cuts
down false-trigger actions in practice, and revisiting the wake-word/VAD
false-positive rate itself (also visible in the same log window) if it
doesn't.

## Follow-up session (2026-08-25, later) — user's design for the app-vs-folder ambiguity, and a stale running process explaining "still same issue"

After the app-vs-folder fix below, user reported "still same issue" and
proposed the actual design going forward: when a name matches both an app/
system thing and a file/folder, default to opening the app/system, and let
the user force the folder explicitly by saying "open <x> in file explorer".

Two things, verified separately rather than assumed:

1. The default-to-app fix from the entry below *was* already correct --
   re-verified live (`execute("open telegram")` -> `open_app`, unchanged).
   The real reason it looked unfixed: `Get-CimInstance Win32_Process` showed
   a `pythonw.exe main.py --listen` process (PID 5808) that had been running
   since *before* today's fix was written -- Python doesn't hot-reload, so
   the live assistant the user was actually talking to was still running
   yesterday's code. Restarted it so today's fix takes effect; general
   lesson for next time this comes up: always check for (and restart) a
   stale long-running `--listen` process before assuming a code fix didn't
   work.
2. Implemented the user's explicit-override design for real, not just the
   default: added an `"open <x> in file explorer"` / `"in explorer"` intent
   in `assistant/executor.py`, registered ahead of both the named-location
   intent and the app catch-all so it always wins when spoken, no matter
   what `<x>` would otherwise resolve to. Delegates to `actions.open_folder`,
   which already resolves named locations and falls back to a recursive
   by-name search -- so this works for any name, not just the ones in
   `_NAMED_LOCATIONS` (verified live: `"open jumong in file explorer"` ->
   `open_folder("jumong")` even though "jumong" isn't a named location at
   all). Documented in `HELP_TEXT`. 2 new tests (plain "in file explorer"
   and the "in explorer" short form), all 56 tests pass.

## Follow-up session (2026-08-25) — "open telegram" opened Telegram Desktop's download folder instead of the app

User: after the previous session's fixes landed, "open telegram" started
opening Telegram Desktop's download folder in File Explorer instead of
launching the actual Telegram app. Flagged it as likely one of several
"contradictions" between the folder/location handling and app-opening that
hadn't surfaced yet.

Root-caused against `assistant/executor.py` and `assistant/actions.py`:

`actions._NAMED_LOCATIONS` (added in an earlier session so `find_file`/
`play_video`/`create_folder` could scope to `"telegram"` meaning Telegram
Desktop's download folder, without needing the full `"telegram desktop"`)
also backs `executor._open_named_location`, the `"open <location>"` intent —
and that intent is deliberately registered *ahead of* the generic `"open
<x>"` app catch-all (so `"open downloads"` resolves straight to the folder).
Since `"telegram"` is in both `_NAMED_LOCATIONS` (as a location alias) and is
a real installed app, `"open telegram"` always matched the location intent
first and never reached `open_app` at all — a structural conflict, not a typo,
so it'll recur for any future named-location key that happens to also be a
real app's name.

Fix: `_open_named_location` now checks `app_discovery.discover_apps()` --
genuinely discovered Start Menu/WindowsApps applications -- for the spoken
name before falling back to `open_folder`, and calls `open_app` instead when
it's a real app. Deliberately checks the *raw* `discover_apps()` dict, not
the full alias-merged `resolve_app_path()`: `config/apps.yaml`'s own
`downloads`/`documents` aliases (`"explorer.exe shell:Downloads"` etc., the
pre-existing workaround this named-location intent was built to replace) are
not real applications and must keep resolving to `open_folder`, confirmed by
a regression test (`test_named_location_alias_only_in_apps_yaml_still_opens_folder`)
that deliberately runs against the real (unmocked) `discover_apps()` rather
than a stubbed-empty one. Verified live against the real machine: `"open
telegram"` -> `open_app` (launches `Telegram.exe`), `"open telegram desktop"`
-> `open_folder`, `"open downloads"`/`"open documents"` unaffected -> still
`open_folder`, `"open spotify"` (not a named location at all) unaffected ->
still `open_app`. All 54 tests pass (2 new: the app-vs-folder precedence
case, and the apps.yaml-alias-isn't-a-real-app case).

## Follow-up session (2026-08-24, latest) — "close file explorer" refused forever + "open jumong" not recognized

User reported two things after using the new open-folder/search behavior:
1. Opened Telegram Desktop's folder ("in browser" = a File Explorer window,
   confirmed via `win32gui.EnumWindows` -- title was literally `"Telegram
   Desktop - File Explorer"`), then couldn't close it: "close file explorer"
   didn't work, and their theory was "it doesn't know how to close since I
   didn't open with open file explorer."
2. "open jumong" -- a real folder confirmed to exist
   (`C:\Users\SPIDER MAN\OneDrive\Pictures\Jumong`, found by `_find_folder`
   in 0.34s) -- came back as "didn't understand" instead of opening it.

Root-caused both against `assistant/actions.py`, not guessed:

1. **`close_app("file explorer")` real bug, unrelated to the user's theory**:
   "file explorer" resolves (via the `file explorer: "explorer"` alias) to
   the process name `explorer.exe`, which is *always* running (it's the one
   shared desktop-shell process) and is (correctly) in `protected_processes`.
   Because that process-name match was found, the code took the "protected,
   refuse" branch and never even tried the window-title-based close that
   already existed for the *other* branch (no process match at all, e.g.
   Chrome PWAs) -- so "close file explorer" / "close explorer" could
   *never* work, for any open Explorer window, regardless of how it was
   opened. Confirmed directly: `actions.close_app("telegram desktop", ...)`
   (naming the window by its content, not "file explorer") already worked
   correctly and closed the real window -- so the user's theory about *how*
   it was opened wasn't the actual cause.
   - Fix: extracted the existing window-title-close logic into
     `_close_windows_by_title()` and call it from the protected-process
     branch too, before refusing -- "close file explorer" now closes the
     matching Explorer window(s) (verified for real: opened Telegram
     Desktop's folder, ran `close_app("file explorer")`, confirmed via
     `win32gui.EnumWindows` that the window was actually gone), while
     `explorer.exe` itself still can never be terminated as a process.
2. **`open_app`/the generic "open <x>" catch-all had no folder/file
   fallback at all** -- it only ever tried `resolve_app_path` (installed
   apps), so any name that wasn't a recognized app -- a downloaded show's
   folder, "jumong" -- went straight to "Couldn't find an app matching
   'jumong'" even though `_find_folder`/`_find_file` (added earlier this
   session for `open_vscode`/`open_folder`) would have found it instantly.
   Fix: `open_app` now tries `_find_folder` then `_find_file` before
   refusing. Verified end-to-end (only `os.startfile` mocked):
   `execute_with_status('open jumong')` now opens
   `C:\Users\SPIDER MAN\OneDrive\Pictures\Jumong` directly.
3. All 52 tests pass (4 new: `close_app` protected-but-has-a-window case
   closes the window instead of refusing, protected-with-no-window-either
   still refuses, `open_app` folder-search and file-search fallbacks, and
   refusing when nothing matches at all). Updated the two pre-existing
   `close_app` tests that weren't previously mocking `_hwnds_for_title` --
   with the new fallback in place they would otherwise have made real
   `win32gui.PostMessage` calls against whatever windows happen to be open
   on the machine running the test suite.

## Follow-up session (2026-08-24, later) — "Spiderman isn't responding" root-caused: a hung Spotify OAuth permanently locked the listener

User: "spiderman isn't responding again." Root-caused from `logs/assistant.log`
and the live process, not guessed:
- The real `\VoiceAssistantListener` scheduled task's `pythonw.exe` process
  (`Task To Run: pythonw.exe main.py --listen`, "Keeps the Spiderman voice
  assistant listener... always running, hidden, and self-restarting") was
  still alive but had gone completely silent after `2026-08-24 18:15:14`,
  when it logged `Executing intent for: 'Play gca by shaevice on spotify'`
  and then never logged anything again -- no `Execution took`, nothing.
  Every wake-word trigger since then logged "still processing a previous
  command -- ignoring until it finishes" (checked: this repeated for 27+
  minutes straight, 18:15 through past 18:42).
- Found the actual hang: a live Chrome tab was still sitting open at
  `accounts.spotify.com/authorize?...&redirect_uri=http://127.0.0.1:8888/callback`.
  `config/.env` has real `SPOTIFY_CLIENT_ID`/`SECRET` configured and no
  `config/.spotify_cache` existed yet, so `spotify_client.play()` (via
  spotipy's `SpotifyOAuth(open_browser=True)`) opened that consent page for a
  first-time login and then blocked forever inside spotipy's local
  callback-server wait -- the user never completed the login in the browser
  (voice-triggered in the background), so it just hung indefinitely. This is
  exactly the failure mode `listen.py`'s own comments already named ("an
  OAuth flow stuck on a browser that never redirects back") -- but the
  existing mitigation (run `_process_command` on its own thread) only kept
  *wake-word detection* alive, not future *commands*: `command_lock` (a plain
  `threading.Lock`) was held for the entire hang, so once one command got
  stuck, every subsequent command was silently ignored forever, with zero
  spoken or logged indication of *why* -- until the process was killed and
  restarted by hand. "Again" in the user's report means this had already
  happened before.
- **Immediate recovery**: `schtasks /end /tn VoiceAssistantListener` (the
  process itself outlived that -- it was blocked in a C-level socket wait,
  not something a task-scheduler stop signal alone kills) confirmed the PID
  was gone, then `schtasks /run /tn VoiceAssistantListener` brought up a
  fresh instance. Verified fully healthy via the log: custom wake word
  loaded, all 16 fast-lane command word models loaded, mic stream open,
  Whisper model ready -- confirmed end-to-end restart, not just "process
  exists."
- **Root fix (`assistant/listen.py`)**: replaced the plain `command_lock =
  threading.Lock()` with a new `_CommandGate` class that behaves like a lock
  for mutual exclusion, except a holder that's been busy longer than
  `COMMAND_TIMEOUT_SECONDS` (env-configurable, default 60s) is treated as
  abandoned and handed over to the next wake-word trigger instead of blocking
  forever -- logs a warning when this happens. The abandoned command's thread
  is already a daemon and keeps running in the background (so a very-late
  OAuth completion, if it ever comes, still speaks its own result via the
  existing `tts.speak_*` calls in `_process_command` -- untouched). This is a
  systemic fix, not a Spotify-specific patch: bounds the outage from *any*
  future hung integration (a network call with no timeout, another OAuth
  flow, etc.), not just this one.
- New `tests/test_listen.py` (`TestCommandGate`, 5 tests): acquire-when-free,
  second-acquire-fails-while-busy, acquire-after-release,
  stale-holder-abandoned-after-a-short-timeout, release-is-a-safe-no-op.
  All 48 tests pass.
- **Not done / user follow-up needed**: the dangling Spotify login tab was
  left open in Chrome (not touched -- completing OAuth logins isn't something
  to do on the user's behalf). Until the user finishes that login once (or
  it's abandoned), the *next* "play X on spotify" will still hit the same
  first-time-auth browser flow and block for up to `COMMAND_TIMEOUT_SECONDS`
  before being abandoned (bounded now, but still a real wait each time) --
  completing the login once will cache a token to `config/.spotify_cache` and
  this stops happening entirely.

## Follow-up session (2026-08-24, even later) — "open downloads" now opens the real folder deterministically

User: "something like open downloads, it should show downloads folder on file
explorer." This already happened to work for "downloads" and "documents"
specifically, but only because `config/apps.yaml` hardcodes them as app
aliases to `explorer.exe shell:Downloads`/`shell:Personal` (flagged as a gap
in an earlier session -- `open_folder({'path': 'downloads'})` "isn't even a
real path on its own"). Every *other* named location (`desktop`, `pictures`,
`music`, `videos`, `home`, `telegram`/`telegram desktop`) had no such alias,
so "open pictures" etc. would fall to `open_app`'s fuzzy app-name matching
and could misfire against an unrelated installed app.

Fixed properly instead of patching per-location yaml aliases:
- `actions.open_folder` now resolves through `_resolve_location` first (the
  same named-location table `create_folder`/`create_file` already used) --
  covers every key in `_NAMED_LOCATIONS` plus an already-existing literal
  path -- before falling back to the recursive `_find_folder` fuzzy search.
- `executor.py`: new intent `open (the )?<location>( folder)?` built directly
  from `actions._NAMED_LOCATIONS.keys()` (so there's one source of truth, not
  two lists to keep in sync), registered ahead of the generic `open <x>` app
  catch-all. Verified end-to-end (only `os.startfile` mocked):
  `execute_with_status('open downloads')` now calls
  `os.startfile('C:\\Users\\SPIDER MAN\\Downloads')` directly -- deterministic,
  no fuzzy app matching involved -- and "open pictures"/"open the desktop
  folder"/"open telegram desktop" all resolve the same way.
- All 43 tests pass (6 new: named-location routing beats the app catch-all,
  multi-word location, non-location app names still route to `open_app`,
  `open_folder` resolving a named location directly, and refusing an
  unresolvable name).

## Follow-up session (2026-08-24, latest) — whole-file-system search, not just Telegram Desktop

User feedback: file/folder awareness was effectively hardcoded to Telegram
Desktop (via `_DEFAULT_VIDEO_SEARCH_LOCATIONS`/`_NAMED_LOCATIONS`), and
`"open <file> in vscode"` didn't actually work for a bare filename -- it was
passed straight through to the VS Code CLI as a literal path, which silently
opened/created a bogus path relative to cwd instead of finding the real file.

Fixed in `assistant/actions.py`:
- New `_find_file(name, location=None)` / `_find_folder(name, location=None)`:
  generic, recursive (via `os.walk`, unlike the video search which only
  scanned one folder's top level) fuzzy-match search. With no `location`
  given, searches the user's *entire home directory*, not one hardcoded app
  folder -- this is the general "explore the file system" capability that was
  missing. `_SEARCH_EXCLUDE_DIRS` (`AppData`, `node_modules`, `.venv`,
  `Program Files`, etc.) and skipping dot-directories keep it fast (~2s over
  this user's real home directory, confirmed by timing `_find_file`).
- `open_vscode`: when `path` isn't an exact existing path (and no `goto`),
  falls back to `_find_file` before invoking the VS Code CLI -- this is the
  direct fix for "open a particular file in VS Code" not working.
- `open_folder`: same fallback via `_find_folder`, so "open folder <name>"
  works from a bare name too, not just an exact path.
- New actions `find_file`/`open_file` (search + report / search + launch with
  default app), wired into both `executor.py` (new `find file <name>` /
  `open file <name>` intents) and `llm_backend.py`'s TOOLS/DISPATCH so the
  Phase 4 LLM fallback has the same capability.
- Verified end-to-end (not just unit tests): `_find_file('actions.py')` with
  no location found the real `assistant/actions.py` in this repo by walking
  the whole home directory in ~2s; `execute_with_status('open vscode in
  actions.py')` (only `subprocess.Popen` mocked) resolved to the real full
  path and built the correct `code --reuse-window <path>` command.
- All 37 tests pass (added `TestFindFile`, `TestFindFolder`,
  `TestOpenFolderSearchFallback`, `TestOpenVscodeSearchFallback` to
  `tests/test_actions.py`).
- **Not done**: no depth/result cap or "multiple matches" disambiguation --
  a home directory with several files of the same name will just silently
  pick whichever fuzzy-scores highest score-first-found; fine for now since
  this mirrors how `_find_video_file` already behaves, but worth revisiting
  if it turns out to matter in practice.

## Follow-up session (2026-08-24) — proved out against the real library, found and fixed 3 real bugs

The user asked directly: would `"...from telegram desktop in file explorer,
play the diplomat season 1 episode 3"` actually work? Answer at the time was
**no**, for three separate, now-fixed reasons, found by testing against this
user's *actual* `Downloads\Telegram Desktop\` folder (286 real video files,
confirmed via a real directory listing -- not assumed):

1. **`"telegram desktop"` wasn't a location the assistant knew about at
   all.** Fixed: added it (and bare `"telegram"`) to `_NAMED_LOCATIONS` in
   `assistant/actions.py`, pointed at `Downloads\Telegram Desktop` --
   confirmed that's genuinely where this user's real TV/movie downloads live.
   Also added it to `_DEFAULT_VIDEO_SEARCH_LOCATIONS`, so it's searched even
   when no location is spoken at all -- "play the diplomat season 1 episode 3
   on vlc" now works with zero location clause needed.
2. **Real, previously-undetected bug: the season/episode tag regex silently
   failed on this user's actual files.** `tag_pattern` used `\b` (word
   boundary) right after the episode number, expecting to match
   `"...S01E03."` (dot-separated, like the synthetic test data from the
   previous section). This user's real files are underscore-separated
   (`"...S01E03_720p..."`), and `_` counts as a `\w` character in regex just
   like a digit does -- so there's never a boundary between `3` and `_`, and
   the match silently returned nothing on every single real file. Fixed:
   swapped `\b` for `(?!\d)` (only rules out a following digit, so "e1"
   still can't false-match inside "e10", but doesn't care what character, if
   any, comes after) -- confirmed via a direct regex test before and after
   the fix using the real filename.
3. **Real bug: the fuzzy-match confidence threshold discarded a match that
   was already correctly picked as the best of the field.** With the tag bug
   fixed, `The_Diplomat_S01E03_720p_Cinemagic_HD.mp4` correctly scored
   highest among all 10 files sharing an `S01E03` tag in that folder (13
   Reasons Why, Gotham, Mr. Robot, etc. all have their own episode 3) -- but
   only 64.9/100, below `VIDEO_MATCH_THRESHOLD = 70`, because `_clean_title`'s
   old regex-substitution approach didn't recognize "Cinemagic" (this
   library's dominant release-group tag) as junk to strip, so it stayed in
   the cleaned title and diluted the score. Two-part fix:
   - Rewrote `_clean_title` from "regex-substitute known junk words" to
     "keep tokens only up to the first season/episode tag, year, or
     resolution/source/codec marker" (new `_QUALITY_MARKER_RE`,
     `_YEAR_TOKEN_RE`) -- scene-release naming conventions always put the
     release group *after* all of those, so truncating there drops
     arbitrary group names ("Cinemagic_HD", "SeriesLand4U", "GalaxyTV",
     "HETeam", "PSA"...) for free, without having to enumerate every group
     that exists. Confirmed: `The_Diplomat_S01E03_720p_Cinemagic_HD` now
     cleans to exactly `"The Diplomat"`.
   - Separately: once multiple files share the *same* season/episode tag,
     that tag match already proves it's the right episode -- the only open
     question is which show. Now always returns the best-scoring one from
     that narrowed set instead of applying `VIDEO_MATCH_THRESHOLD`, which
     was designed for the very different "no season/episode info at all,
     fuzzy-search the whole library" case and was wrongly gating a
     comparison that had already been narrowed to near-certainty.
- **Verified for real, end to end, no shortcuts**: ran
  `execute_with_status('play the diplomat season 1 episode 3 on vlc')`
  against the actual executor/intent-matching/action pipeline (with only
  `subprocess.Popen` mocked, so no real VLC window popped up) -- matched the
  fast regex intent (no LLM round-trip), found
  `The_Diplomat_S01E03_720p_Cinemagic_HD.mp4` in the real
  `Telegram Desktop` folder, and built the correct VLC launch command. Also
  re-confirmed `"the diplomat season one episode three"` (spoken numbers),
  `"13 reasons why season 1 episode 1"`, and `"gotham season 1 episode 3"`
  all resolve to the correct real file in that same folder.
- All 30 tests still pass after these fixes.
- **Still open**: the user's exact original phrasing (location clause
  *before* "play", and no "vlc" mentioned) still wouldn't match the fast
  regex intent -- it would only work today if the LLM fallback correctly
  guessed `play_video(name=..., location="telegram desktop")` on its own,
  which is possible but not fast (~15-20s round trip) and not guaranteed.
  Since Telegram Desktop is now in the default search order, the location
  clause isn't even necessary anymore for the fast path to work -- so the
  practical fix for the user is to just say "play the diplomat season 1
  episode 3 on vlc" without the location preamble. Could still add a
  location-first regex ordering (same pattern as `create_folder`'s two
  orderings) if this phrasing turns out to be how the user naturally talks;
  not done yet since it wasn't clear this was really needed once the
  location became unnecessary.
- Listener restarted and confirmed live with all of this.

## Follow-up session (2026-08-24, even later still) — real title matching for `play_video`

Immediately after `play_video` shipped (see section right below), the user
pointed out it shouldn't require reading out the whole scene-release
filename (e.g. `Gotham.S01E01.1080p.WEB.x264-GROUP.mkv`) -- it should learn
to extract the real title (and season/episode, for TV) the way a person
would say it, so "gotham season 1 episode 1" or just "the batman" is enough.

- **`assistant/actions.py`**: `_find_video_file` no longer only does exact/
  substring matching against the raw filename. New pieces:
  - `_clean_title(raw)` -- strips season/episode tags, year, and scene-release
    junk (resolution, source, codec, release group) from a filename stem down
    to just the show/movie title, normalizing dots/underscores to spaces.
    Verified: `"Gotham.S01E01.1080p.WEB.x264-GROUP"` -> `"Gotham"`,
    `"The.Batman.2022.1080p.BluRay.x264-SPARKS"` -> `"The Batman"`.
  - `_parse_spoken_episode(name)` -- recognizes "<title> season X episode Y"
    (also the glued short form "s1e1"), returning `(title, season, episode)`
    or `None` if the spoken name isn't a TV-episode-shaped request at all (a
    plain movie title correctly returns `None`). Also normalizes small spoken
    number words ("season one episode one") to digits first via
    `_words_to_digits`, since Whisper doesn't reliably write those as digits
    on its own.
  - `_find_video_file` now: if the spoken name parses as season/episode,
    matches files by an `SxxEyy`-style regex tag first (narrowing by fuzzy
    title if more than one file shares that tag, e.g. two different shows in
    the same folder); otherwise falls back to fuzzy-matching the spoken title
    against every candidate's `_clean_title()` (`rapidfuzz.fuzz.token_sort_ratio`,
    threshold `VIDEO_MATCH_THRESHOLD = 70`) after the existing exact/substring
    check still gets first try for speed.
  - Verified end-to-end against real temp files matching actual scene-release
    naming: "gotham season 1 episode 1"/"...season one episode two" both
    correctly picked their exact `SxxEyy`-tagged file out of two candidates;
    "the batman" and bare "batman" both correctly matched
    `The.Batman.2022.1080p.BluRay.x264-SPARKS.mkv`.
- New tests in `tests/test_actions.py`: `TestVideoTitleExtraction` (junk
  stripping, spoken season/episode parsing incl. word-numbers, plain-title
  non-match, and two `_find_video_file` end-to-end cases against realistic
  filenames). 30 tests total, all passing.
- Updated `play_video`'s LLM tool description (`llm_backend.py`) and the CLI
  help text (`executor.py`) to make clear it matches by title, not filename
  -- matters for the LLM fallback path especially, so it doesn't try to
  reconstruct an exact filename itself.
- **Checked against this user's actual video library and confirmed a real
  gap, not yet fixed**: everything currently in `Videos` is screen recordings
  in a `Screen Recordings` subfolder, not movies/shows directly in `Videos` --
  so the pre-existing "only searches one level deep, not subfolders"
  limitation (see section below) is the thing that will actually block this
  feature working today for this user, more than title-matching was. Revisit
  making the search recursive once there's real downloaded content to test
  against.
- Listener restarted and confirmed live with this change; all tests still
  passing.

## Follow-up session (2026-08-24, even later) — VLC video playback + create-file bug

User asked for a "play a video on VLC" command, and reported "create a file"
wasn't working / felt slow. Root-caused the second one against
`logs/assistant.log` before touching anything: `'Create a file named spider
in downloads'` was transcribed correctly (not a hearing problem) but there
was no `create_file` action or intent at all — only `create_folder` existed.
It fell through to the LLM fallback, which took ~15s just to pick a tool,
guessed wrong (`open_folder({'path': 'downloads'})`, which isn't even a real
path on its own), and the whole round trip took 22.9s. That slowness *was*
the bug — not a mishear.

- **New `actions.create_file(location, name)`** (`assistant/actions.py`) —
  same `_resolve_location` named-location resolution as `create_folder`.
  Defaults to a `.txt` extension if the spoken name doesn't include one
  (voice almost never says an extension), respects one if given (e.g.
  "notes.py"). New executor intents mirroring `create_folder`'s two
  orderings ("create a file in downloads, name it X" / "create a file called
  X in downloads"), registered ahead of the generic `open <x>` catch-all so
  it doesn't fall to the LLM anymore — verified instant (`ExecStatus.OK`, no
  LLM round-trip) via a real end-to-end run against the actual Downloads
  folder, confirmed on disk, then cleaned up. Also exposed as an LLM tool for
  oddly-phrased requests, same as `create_folder`.
  - **Found and fixed a real pre-existing bug while in there**:
    `create_folder` had no success-message return at all — it called
    `target.mkdir()` and fell through to an implicit `return None`, unlike
    every other action in the file. Harmless in practice (the voice front-end
    still said "Done" since that's driven by `ExecStatus`, not the return
    value) but the CLI/log would have shown literal "None" as the result.
    Fixed to return a proper `"Created folder '<name>' in <base>."` message.
- **New `actions.play_video(name, location=None)`** + `_find_video_file()`
  (`assistant/actions.py`) — resolves VLC via the existing `resolve_app_path`
  (same mechanism every other app uses), searches for a matching video file
  by name (extension optional, common video extensions checked) in a given
  named location, or across Videos/Downloads/Desktop/Documents/home in that
  order if none is given -- voice commands essentially never include a full
  path. New executor intent: `play/open <name> on/in vlc [in/from
  <location>]`, registered ahead of the generic catch-all. Also exposed as an
  LLM tool.
  - **Added an explicit `vlc` alias in `config/apps.yaml`** pinned to the
    real `C:\Program Files\VideoLAN\VLC\vlc.exe` — discovered while checking
    this that a bare "vlc" was fuzzy-matching to the wrong Start Menu
    shortcut ("VLC media player - reset preferences and cache files"),
    confirmed in the log from an earlier command this session. Same fix
    pattern as the chess/day_one aliases from a previous session.
  - **Known limitation, not fixed**: `_find_video_file` only checks files
    directly inside a location, not subfolders — this user's actual videos
    live in `Videos\Screen Recordings\`, one level down, so "play X on vlc"
    won't find them as-is. Flagged to the user rather than guessing whether
    to make the search recursive (a full recursive walk of "home" specifically
    could be slow) — revisit if it comes up as a real complaint.
  - **Known risk, not confirmed one way or the other**: the fast-lane system
    (see its own section below) has a standalone `"play"` word-detector for
    media play/pause that runs concurrently with normal listening. It's
    architecturally possible for it to fire the instant it hears "play" at
    the start of "play spiderman on vlc" (or the pre-existing "play X on
    spotify") and cut the command short as a play/pause toggle before the
    rest of the sentence is spoken. Not observed in the log so far (checked:
    every historical `Fast-lane command: 'play'`/`'pause'` entry was a
    standalone word, not the start of a longer sentence), but a real
    possibility now that "play" is the first word of two different full
    commands. If this ever actually misfires, the fix is in the fast-lane
    debounce logic in `listen.py`, not in the new action code.
- New tests in `tests/test_actions.py`: `TestCreateFile` (extension
  defaulting, extension preservation, unknown-location refusal) and
  `TestPlayVideo` (no-match refusal, VLC-not-found refusal, and a
  mocked-`subprocess.Popen` success case using a real temp directory + real
  temp video file). 25 tests total, all passing. Didn't add a live
  `play_video` test that actually launches VLC — didn't want to pop a real
  VLC window open unexpectedly during an automated test run.
- Listener restarted and confirmed live with all of this session's changes
  (mic-activation fix from earlier + these two new capabilities); confirmed
  clean startup in the log with no import/syntax errors.

## Follow-up session (2026-08-24, later) — mic-activation delay + invisible indicator

User reported two things noticed after resuming from a shutdown: the mic takes
too long to start responding, and the on-screen indicator icon doesn't appear
until *another* window (VS Code) gets opened. Both were root-caused against
`logs/assistant.log` timestamps, not guessed at, and both were real
pre-existing architectural bugs.

- **Mic activation delay (`assistant/listen.py`)**: `_listen_loop` opened the
  audio stream (`with stream:`) only *after* Whisper had both loaded and run
  its one-time warm-up transcription — even though wake-word detection and
  the fast lane need nothing from Whisper at all, only the (much lighter)
  wake model. Confirmed from the log: on one restart, `Indicator window
  placed` fired at 15:58:00 but `Wake word listener started` (mic actually
  live) didn't fire until 16:01:47 — a ~3.5 minute gap where nothing was
  listening, worse right after a cold boot when disk cache is cold and other
  startup processes are competing for CPU.
  - Fixed by loading Whisper on its own background thread
    (`threading.Event` + a holder dict) while the wake model loads and the
    mic stream opens immediately after. `_process_command` only blocks on
    `whisper_ready.wait()` if a full (non-fast-lane) command actually needs
    transcription before Whisper's background load finishes — wake-word
    detection itself is never blocked.
  - Verified live after restart: `Wake word listener started` at 16:12:12,
    `Whisper model ready (base)` only 16s later at 16:12:28 — running in
    parallel instead of stacked sequentially.
- **Indicator icon invisible until another window opens (`assistant/indicator.py`)**:
  the borderless (`overrideredirect`) always-on-top Tk window only set
  `-topmost` once, at creation. On Windows this can silently fail to actually
  composite the window above the desktop if DWM/explorer.exe hasn't fully
  finished starting yet — the window exists and Tkinter considers it
  topmost, but nothing forces Windows to actually recompute the z-order
  until something else does (e.g. opening VS Code), which matches exactly
  what was reported. This is the same early-boot race already worked around
  with sleep delays in a previous session — those delays alone weren't
  always enough.
  - Fixed by reasserting `-topmost` + `root.lift()` once a second for the
    first 30 seconds after the window is created (`TOPMOST_REASSERT_SECONDS`),
    instead of a single one-shot attribute set. Not directly confirmed
    visually this session (needs a real reboot to fully verify), but the
    mechanism is a well-established Windows/Tk workaround for exactly this
    failure mode.
  - **Still not fully closed out**: this fix hasn't been confirmed against
    an actual reboot/shutdown-resume cycle yet, only against a manual
    scheduled-task restart mid-session (where the icon reliably shows either
    way, since the process wasn't literally starting at Windows boot). If it
    still goes missing after a real shutdown+startup, the 30s window is the
    first thing to extend, or switch to reasserting indefinitely rather than
    just for a fixed startup window.
- Committed and pushed this session's prior pending work first (spoken
  feedback, folder creation, fast-lane models) before starting on these two —
  see the section below for what that covered. All 19 tests still pass after
  these two fixes; listener restarted and confirmed live with both.

## Follow-up session (2026-08-24) — spoken feedback + folder creation

Tackled the first two items off the NEXT AGENDA below, in the priority order the
user asked for: audio feedback first (folded in a new complaint that arrived
this session — the assistant needed to say when it *doesn't* understand or
*can't* carry out a command, not just confirm success), then folder creation.
Power actions (shutdown/restart/hibernate) and "refresh system" were
deliberately **not** touched this session — the user only asked to solve audio
+ folder creation "right now"; power actions still needs the safety design
called out below, and refresh-system still needs a clarifying answer from the
user before building anything.

- **Spoken feedback (`assistant/tts.py`, new)** — the assistant now speaks a
  short response after every voice command instead of only printing/logging
  it, using `pyttsx3` (offline Windows SAPI5, no account/API key, consistent
  with the rest of the project running fully local). Three response
  categories, each with a few randomly-varied phrasings addressing the user by
  name (`ASSISTANT_USER_NAME` env var, defaults to "Ibrahim"):
  - **Success** — "Done." / "Done, Ibrahim." / "Got it." (this was also the
    "audio done confirmation" deferred earlier in the session below — now
    done).
  - **Didn't understand** (no intent matched and the LLM fallback didn't
    resolve it either, or nothing was transcribed at all) — "I don't
    understand, Ibrahim." / "Sorry Ibrahim, I didn't catch that. Can you speak
    a little clearer?" / "I'm not sure what you mean, Ibrahim." — this is the
    exact scenario the user flagged this session: previously a failed/unclear
    command just sat silently in the log with no signal at all that anything
    had gone wrong.
  - **Understood but couldn't do it** (app not found, nothing running to
    close, bad path, script not whitelisted, etc.) — "I can't carry that out,
    Ibrahim." / "Sorry Ibrahim, I wasn't able to do that." / "That didn't
    work, Ibrahim."
  - New env vars, all optional (see `config/.env.example`): `SPEAK_RESPONSES`
    (set `0` to disable), `ASSISTANT_USER_NAME`, `TTS_RATE`.
  - Only wired into the voice path (`assistant/listen.py`) — the typed CLI
    (`assistant/cli.py`) deliberately doesn't speak, since audio feedback is
    for a hands-off voice session and the CLI already shows the same text on
    screen.
- **The real problem this required fixing first**: `executor.execute()`
  collapsed every outcome — genuine success, "didn't understand", and
  "understood but failed" — into one plain string, with no way for a caller to
  tell them apart. That's fine for the CLI/log (a human reads the sentence),
  but it meant the voice front-end had no reliable signal to decide *which*
  spoken response to give. Fixed with two changes:
  1. New `actions.ActionError` exception — every action function's genuine
     failure path (`open_app`, `close_app`, `minimize_app`, `open_vscode`,
     `run_script`, `open_folder`) now `raise`s this instead of `return`ing the
     failure message as if it were a normal result. A plain `return` from an
     action function now always means "succeeded" (even a purely informational
     one, like "no battery on this machine" or "already open, switching to
     it") — only a genuine miss raises.
  2. New `executor.ExecStatus` enum (`OK` / `NO_MATCH` / `FAILED`) and
     `executor.execute_with_status(text)`, which returns `(status, message)`.
     `executor.execute(text)` (the original string-returning function, still
     used by `cli.py` and the test suite unchanged) is now a thin wrapper over
     it. `listen.py` calls `execute_with_status` directly and picks the
     spoken response off `status`.
  - Updated `tests/test_actions.py`'s three failure-path tests
    (`test_protected_process_refused`, `test_no_match`,
    `test_unwhitelisted_script_refused`) to `assertRaises(ActionError)`
    instead of asserting on a returned string — the old assertions no longer
    matched the new contract. Full suite (19 tests) passes.
- **Folder creation (`actions.create_folder(location, name)`, new)** — handles
  the exact example the user gave: "create a new folder in downloads, name it
  spiderman." Two intent patterns in `executor.py` cover both natural
  orderings ("create a folder called X in Y" and "create a new folder in Y,
  name it X"; the location-first pattern is registered first since its " in "
  would otherwise also match the name-first pattern's shape with the groups
  swapped wrong — verified both route correctly with no cross-matching).
  - New `actions._NAMED_LOCATIONS` / `_resolve_location()` resolves common
    named locations (downloads, documents, desktop, pictures, music, videos,
    home) straight to the real filesystem path via `Path.home()` — these are
    *not* the same as the `shell:`-namespace aliases already in
    `config/apps.yaml` (those only work for launching Explorer at a virtual
    folder, not for creating a real file inside one). Falls back to treating
    the location as a literal path (with the same `%USERNAME%`-style
    env-var expansion `open_folder` already got) if it isn't a named one.
  - Also exposed as an `create_folder` tool to the Phase 4 LLM fallback
    (`llm_backend.py`) so oddly-phrased folder requests still work.
  - Verified end-to-end: both phrasings create the folder and return
    `ExecStatus.OK`; a real "create a new folder in downloads, name it
    spiderman_test_delete_me" was run against the actual Downloads folder,
    confirmed on disk, then cleaned up.
- **`requirements.txt`**: added `pyttsx3` (pulls in `comtypes`/`pypiwin32`;
  `pywin32` was already a dependency). Installed and confirmed working with a
  real spoken test on this machine.
- **Listener restarted and confirmed live** with all of this session's changes
  — spoken feedback, folder creation, and the fast-lane models from
  `batch_train_words3.log`. Two real gotchas hit along the way, worth knowing
  about next time:
  1. **Killing the listener from the agent's own shell tool silently no-op's**
     — `Stop-Process`/`taskkill` against the scheduled task's PID report
     success but the process's actual start time never changes. This is a
     sandboxing limitation specific to the agent's Bash/PowerShell tool
     (same user, same session ID, so not a permissions issue) — it can kill a
     process it spawned itself (e.g. one started via `run_in_background`), just
     not the Task-Scheduler-launched listener. **The user has to run
     `Stop-Process`/`Start-ScheduledTask` themselves** from their own terminal;
     the agent can't do this part.
  2. **After the user did kill the old process, `Start-ScheduledTask` then
     failed** with error `0x800710E0` ("the operator or administrator has
     refused the request"). Cause: Task Scheduler's own internal bookkeeping
     (`schtasks /query` showed `Status: Running`) hadn't noticed the process
     was gone, and `MultipleInstances = IgnoreNew` refuses to start a new one
     while it still thinks one's active. **Fix: `Stop-ScheduledTask
     -TaskName "VoiceAssistantListener"` first** (tells Task Scheduler itself
     to clear its tracked instance) **then** `Start-ScheduledTask`. Worth
     trying this first if a future restart ever gets refused the same way.
  3. **Then it looked hung** (only reached `~0.03s` of CPU time after several
     minutes, one thread instead of the expected two) but wasn't — a parallel
     foreground run (`python main.py --listen`, not `pythonw.exe` via the
     task, so its output was actually visible) confirmed it was just genuinely
     slow to load this one time (~7 minutes end to end vs. the usual 1-2),
     not deadlocked — CPU time was climbing, just very slowly. No code change
     needed; if this happens again, checking whether CPU time is climbing at
     all (even slowly) over a couple of checks a minute apart is the way to
     tell "slow" from "actually stuck" before assuming something's broken.
     (The foreground diagnostic process was killed afterward once confirmed
     — running two listener instances at once would have fought over the
     microphone.)
  - Confirmed via `logs/assistant.log`: `Loaded 16 fast-lane command word
    models: ['battery', 'chess', 'chrome', 'close', 'day_one', 'desktop',
    'minimize', 'next', 'notifications', 'open', 'pause', 'play', 'previous',
    'search', 'spotify', 'wifi']` followed by `Wake word listener started`.
- **Fast-lane rollout status, checked while here (not otherwise touched this
  session)**: `batch_train_words3.ps1` finished (stale log, no process still
  running). Deployed to `config/command_words/`: `battery`, `chess`,
  `chrome`, `close`, `day_one`, `desktop`, `minimize`, `next`, `notifications`,
  `open`, `pause`, `play`, `previous`, `search`, `spotify`, `wifi`. Missing
  (never got a model): `telegram`, `vscode`, `notepad`, `vm`,
  `file_explorer`, `terminal`, `unigram` — `unigram` failed outright (every
  OpenAI TTS generation attempt hit "Connection error", so it had 0 positive
  samples to train on); the rest simply weren't in this particular batch run
  or need investigation. Left `TARGET_WORDS` in `listen.py` as-is (already
  lists all of these) — harmless per the existing comment there, since
  `_load_wake_model()` only scores words it actually finds a `.onnx` file for.

## Follow-up session (2026-08-23, later) — real bugs found and fixed

After the fast-lane rollout below got resumed, the user reported several things
broken after a full system power-off/restart: minimize "lost its skill", the
on-screen indicator icon didn't appear at all, apps still duplicate-opened
instead of focusing the existing window, and close kept refusing to work. All
four were investigated against `logs/assistant.log` rather than guessed at,
and turned out to be real pre-existing architectural gaps, not training
issues and not new regressions from this session's own changes (confirmed:
`Minimized chrome.`/`Minimized code.`/`Closed: Telegram.exe.` all worked fine
in the very same log window where chess/vscode were failing) --

- **Root cause (`assistant/actions.py`)**: `close_app`/`minimize_app`, and
  `open_app`'s own "already open, focus it instead of duplicating" dedupe
  check, all matched a running process by the **literal spoken/alias name**
  (e.g. "vscode", "chess") -- never by resolving through
  `resolver.resolve_app_path` the way `open_app`'s *launch* path already did.
  That breaks in two different ways:
  - **Normal apps whose alias doesn't equal their real process name** -- e.g.
    "vscode" is an alias for the bare command "code", but the actual running
    process is `Code.exe`; matching literally on "vscode" never finds it, so
    `close vscode`/`minimize vscode` always failed ("No running process
    matching 'vscode'") even though `open vscode` worked fine. This was
    real and present since 2026-08-22, unrelated to any fast-lane work.
  - **Apps with no process name of their own at all** -- Chrome PWAs (chess,
    unigram, whatsapp web) all run as `chrome.exe`/`chrome_proxy.exe`, shared
    with the entire browser. There is no safe process-name match here: matching
    literally on "chess" finds nothing (hence "No running process matching
    'chess'" no matter how many times you asked), and matching loosely on
    "chrome" would be actively dangerous -- it'd close/minimize *every* Chrome
    window and tab, not just the PWA.
  - This combination is also what looked like "duplicate app opening": since
    the dedupe check couldn't recognize chess as already running either, every
    repeated "open chess" (after "close chess" kept silently failing) may have
    kept happening without ever being recognized as a repeat.
- **Fix, two parts**:
  1. New `_process_name_candidates(name)` in `actions.py`: resolves the name
     through `resolve_app_path` (same as `open_app` already did for itself),
     and if the resolved target is a bare command rather than a path (e.g.
     "code"), follows it through `shutil.which()` to recover the real launcher
     file and its stem -- this is what makes `vscode` → `code` → `Code.exe`
     resolve correctly without hardcoding a per-app exception. `close_app`,
     `minimize_app`, and `open_app`'s dedupe check all use this now instead of
     matching the raw literal name.
  2. New `_hwnds_for_title(substring)`: a window-title-based fallback (not
     process-based) used only when process-name matching finds nothing --
     this is what correctly handles Chrome PWAs. `minimize_app` uses it to
     find the window to minimize; `close_app` uses it too, but sends `WM_CLOSE`
     to just that window (`win32gui.PostMessage`) instead of killing a shared
     process, so it can never take down other Chrome windows/tabs by mistake;
     `open_app`'s dedupe check uses it to correctly recognize an already-open
     PWA and focus it instead of opening a duplicate.
  - Verified against the log post-restart: `minimize vscode` → `Minimized
    vscode.` (previously always failed).
- **Indicator icon missing after a full power-off/restart**: found a real,
  previously-silent failure mode in `assistant/listen.py`'s `run()` --
  `indicator.run()` (Tkinter's mainloop) was called unguarded on the main
  thread, with the audio/wake-word loop on a **daemon** thread. If `tk.Tk()`
  ever raises (plausible right at a fresh boot, since the Task Scheduler "at
  log on" trigger can fire before Windows has finished detecting monitors/the
  desktop has fully settled), that exception would propagate up, the process
  would exit, and the daemon audio thread would die with it -- silently
  killing the *entire* assistant, not just the indicator, with zero trace in
  the log (which is why nothing pointed at this until the code was actually
  read). Fixed three ways:
  1. `indicator.run()` is now wrapped in try/except in `listen.py`; a failure
     is logged (`log.exception(...)`) and falls back to `thread.join()` so
     voice commands keep working even if the indicator itself can't start.
  2. `indicator.py`'s `run()` now sleeps 3s before creating the Tk window (head
     start for display/monitor detection to settle) and logs the computed
     window position + detected screen size on every start
     (`assistant.indicator: Indicator window placed at (x, y) on a WxH
     screen`) -- confirmed working post-restart: `(1270, 24) on a 1366x768
     screen`, correctly on-screen. If it goes missing again, that log line is
     now the first thing to check (was previously silent either way).
  3. Added a 20-second `Delay` to the `VoiceAssistantListener` scheduled
     task's "at log on" trigger (`Set-ScheduledTask`/`MSFT_TaskLogonTrigger`),
     as extra insurance against the same early-boot race independent of the
     code fix. (Confirmed the task's `LogonType` is already `Interactive`, not
     a Session-0-isolated "whether user is logged on or not" task, so that
     more common Task Scheduler GUI gotcha was ruled out first.)
  - Not fully proven root-caused (no direct log evidence existed *before*
    this session's logging was added, by definition), but this closes the one
    concrete silent-failure path found by reading the code, and next
    occurrence will now be diagnosable from the log instead of a total mystery.
- **"Open recycle bin" didn't work either** ("Couldn't find an app matching
  'recycle bin'", confirmed in the log) -- same root category of bug as
  above, but at the discovery level instead of the process-matching level:
  Recycle Bin (like This PC, Downloads, Documents, Network) is a *virtual*
  Windows shell folder with no real `.exe` and no Start Menu `.lnk`, so
  `app_discovery.py` can never find it no matter how good its scanning gets.
  Fixed by adding explicit `config/apps.yaml` aliases that launch them via
  their `shell:` namespace path (e.g. `explorer.exe shell:RecycleBinFolder`)
  -- added `recycle bin`/`bin`, `this pc`/`my computer`, `downloads`,
  `documents` this way. The same pattern (an `explorer.exe shell:<Name>`
  alias) is the fix for any other special shell folder that comes up later.
- **`open_folder` didn't expand `%USERNAME%`-style env-var placeholders** --
  found while chasing an unrelated "take me to desktop" attempt: Whisper
  mis-transcribed it as "Pick me to desktop" (the exact phrase *is* already a
  recognized intent in `executor.py`, matching `take me to|go to|show
  (the )?desktop` -- this was a transcription-accuracy miss, not a missing
  feature), which fell through to the LLM fallback, which guessed
  `open_folder("C:\Users\%USERNAME%\Desktop")` -- a placeholder it can't
  know the real value of. `open_folder` only called `.expanduser()` (handles
  a leading `~`), not `os.path.expandvars()`; fixed by adding that too.
- **Deferred, user's own call**: an audio/TTS "done" confirmation after
  executing a command -- explicitly requested but the user said "we will do
  that later", so intentionally not implemented this session.

## NEXT AGENDA -- attend to this first next session

The user was explicit that this is what gets picked up first, before anything
else, next time. In their own words: "I don't want to come back and have to
say it should be able to do this again." Everything below is a **capability
gap** (a real, missing feature), not a bug in something that already exists --
distinct from everything fixed above.

**Items 2 (folder creation) and 4 (audio confirmation, expanded to also cover
failure/didn't-understand feedback) are done** as of the 2026-08-24 session
above — kept in numbering below for reference to the original request, but
see that section for what shipped. **Items 1 (power actions) and 3 (refresh
system) are still outstanding** and still need the safety design / clarifying
question called out below before building, not a guess.

**Standing preference driving all of this** (said directly, applies beyond
just this list): the user does not want the assistant's capabilities to grow
primarily through one-off voice-model training for individual command words.
Training a fast-lane word only ever makes speech *recognition* instant for
that one word -- it does nothing to build the underlying capability itself.
Anything that's really a normal OS/system action (power state, file/folder
management, standard Windows locations) should be built as a real, general
`actions.py` function first, the same way `open_app`/`close_app`/`show_desktop`
already are -- fast-lane training is an optional speed layer on top of a
capability that already exists via the normal Whisper+regex/LLM path, never
the thing that makes the capability exist in the first place.

1. **System power actions -- shutdown / restart / hibernate.** Don't exist in
   `actions.py` at all right now. The user's bar for these: saying the
   command should be enough that "all I need to do is just click OK" --
   i.e. it should actually trigger the real Windows action (`shutdown /s
   /t 0`, `shutdown /r /t 0`, `shutdown /h`), not just open the Settings
   power menu and stop there. Needs real thought on safety before
   building, not just wiring it up:
   - These are destructive/hard-to-reverse (unsaved work lost) in a way
     open/close/minimize aren't -- probably want a short cancellable delay
     (`shutdown /t 30` + a spoken/logged "say cancel to stop it" window,
     or at minimum route through VOICE_MODE-style confirmation the same
     way `close_app` already does for destructive actions) rather than
     firing instantly and irreversibly on a single mis-transcription.
   - Should land as new `actions.shutdown_system()` /
     `actions.restart_system()` / `actions.hibernate_system()`, wired into
     `executor.py` intents, not just a `run_script` whitelist entry --
     this is core functionality, not a user script.
2. **Folder/file creation and general file management.** Concrete example
   the user gave: "create a new folder in downloads, name it spiderman."
   Nothing like this exists yet -- `actions.py` can only *open* an existing
   folder (`open_folder`), never create/rename/move one. Needs a new
   `actions.create_folder(location, name)` at minimum (with the same
   `%USERNAME%`/env-var expansion fix as `open_folder` just got), a new
   executor intent/regex for "create a folder called X in Y", and probably
   `open_wifi_settings`/`downloads`-style named-location resolution (reuse
   the `shell:` alias work above) so "in downloads" resolves the same way
   voice already resolves app names. Whether to extend this further (rename,
   move, delete a file/folder by voice) is worth a real design conversation
   given the blast radius of "delete" specifically -- start with create only.
3. **"Refresh system"** -- requested but ambiguous; needs a clarifying
   question with the user before building anything, not a guess. Candidate
   interpretations to raise: (a) refresh the desktop/Explorer view (the F5
   equivalent -- `ie4uinit.exe -show` or restarting `explorer.exe`), (b)
   force `app_discovery.discover_apps(force=True)` to re-scan newly
   installed apps without a full listener restart, (c) something else
   entirely. Don't build against a guess here.
4. **Audio "done" confirmation** (carried over from earlier this session,
   still explicitly deferred by the user, not forgotten -- see above).

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

**Update**: as of the 2026-08-24 session, the listener has been restarted and
all of the above (spoken feedback, folder creation, all trained fast-lane
models) is confirmed live — see that section above for the restart gotchas
hit along the way (Task Scheduler stale-state refusal, and a one-off slow
~7-minute load that looked hung but wasn't).

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
- **Batch rollout status, updated in the follow-up session (2026-08-23)**:
  the original batch (`batch_train_words.ps1` / `batch_train_words2.log`)
  had actually **died silently** right as it started `previous` -- confirmed
  by checking for a live process (none found) and the log's real mtime
  (2026-08-22 18:52, over a day stale despite the file being read as
  "recent" at first glance -- always check `stat`'s *Modify* time, not
  *Access* time, which updates just from reading/grepping the file).
  ⚠️ **Caution for next time** (carried over): verify no earlier instance of
  a long-running background job is still alive (`Get-CimInstance
  Win32_Process` filtered on the script/command name) before relaunching one
  -- a prior session double-launched the batch by accident and caused a
  directory-junction race.
  - Done and deployed before the stall: `open` (F1 0.86), `close` (F1 0.95),
    `minimize`, `pause`, `next`.
  - Failed before the stall: `play` (transient OpenAI TTS "Connection error").
  - **New combined batch launched** (`wakeword_trainer/batch_train_words3.ps1`,
    detached via `Start-Process`, log at
    `wakeword_trainer/batch_train_words3.log`) covers: retry `play`, resume
    the original queue (`previous`, `desktop`, `wifi`, `notifications`,
    `search`, `battery`, `spotify`, `chrome`, `telegram`, `vscode`,
    `notepad`), plus new target words the user picked after reviewing a full
    installed-apps dump: `chess`, `day one` (extra phrase none, real voice
    samples already existed in `data_words/day_one/positive/`), `vm` (extra
    phrases "virtual machine", "virtualbox"), `file explorer`, `terminal`,
    `unigram`. Check `batch_train_words3.log` for how far it got.
  - **Real app-mapping bugs found and fixed while picking those new words**
    (in `config/apps.yaml`, not just training data):
    - `assistant/app_discovery.py` only captures a Start Menu shortcut's
      *target path*, not its *arguments*. Chess ("Chess - Play & Learn"),
      Unigram, and WhatsApp Web are all installed as Chrome PWAs and share
      the exact same target, `chrome_proxy.exe` -- they're only
      distinguished by a `--app-id=...` argument that discovery silently
      drops. Without a fix, "open chess" would have launched bare
      `chrome_proxy.exe` with no arguments and done nothing useful. Fixed by
      adding explicit `chess`/`unigram`/`whatsapp web` aliases in
      `apps.yaml` with the full command (path + `--profile-directory` +
      `--app-id`, read directly off each `.lnk`'s `Arguments` property via
      `WScript.Shell`). This same blind spot likely affects any other
      Chrome-PWA-installed app on this machine, not just these three.
    - Day One has **no** Start Menu `.lnk` and no WindowsApps execution-alias
      stub at all (it's an MSIX/Store app), so discovery can't find it by
      any name. Fixed by aliasing `day one` / `day_one` to its AppsFolder
      shell path (`explorer.exe
      shell:AppsFolder\22490Automattic.DayOneJournalPrivateDiary_9h07f78gwnchp!DayOne`
      -- PackageFamilyName + AppId pulled via
      `Get-AppxPackage`/`Get-AppxPackageManifest`).
    - `vm` and `file_explorer` added as aliases pointing at the existing
      `oracle virtualbox` / `explorer` targets, since a bare "vm" doesn't
      fuzzy-match "oracle virtualbox" and the fast-lane word key
      (`file_explorer`, underscored -- `train_command_word.py`'s
      `safe_name()`) didn't match the pre-existing `file explorer` alias
      (space).
  - `TARGET_WORDS` in `listen.py` updated to include `chess`, `day_one`,
    `vm`, `file_explorer`, `terminal`, `unigram` (in addition to the
    existing `spotify`, `chrome`, `telegram`, `vscode`, `notepad`) --
    harmless to add before training finishes, since `_load_wake_model()`
    only scores words it actually finds `.onnx` files for.
  - `spotify` already has 15 real voice takes recorded too, sitting in
    `data_words/spotify/positive/` -- picked up automatically since
    `train_command_word.py` only regenerates TTS files that don't already
    exist and picks up everything else in the positive dir.
- **The listener has still not been restarted since deploying any of these
  models** -- `_load_wake_model()` only picks up `config/command_words/*.onnx`
  at startup, so none of the fast lane is actually live yet even though
  several models are already deployed there. Restart the
  `VoiceAssistantListener` scheduled task once `batch_train_words3.ps1`
  finishes (or far enough along to be worth testing).
- **Also still true from before**: the user has a full 132-entry
  installed-apps dump (Start Menu + WindowsApps discovery) available if more
  fast-lane target words get picked later -- see chat history rather than
  re-running discovery from scratch, most of it is Windows-internals clutter
  already filtered out (uninstallers, `.msc` snap-ins, help/manual files,
  MSIX stub aliases).

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
