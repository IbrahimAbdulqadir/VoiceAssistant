"""Phase 1 test harness: type commands, watch the executor act on them. This is the
"prove the executor works before adding a microphone" step the scope calls for.
Once Phase 2 adds wake word + Whisper, that pipeline calls executor.execute() too --
this file stays as a handy text-only debug mode.
"""

from assistant.executor import execute, HELP_TEXT
from assistant.logger import get_logger

log = get_logger(__name__)


def run() -> None:
    print("Voice Assistant -- Phase 1 (typed command executor)")
    print("Type 'help' for commands, 'exit' to quit.\n")

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            print("Bye.")
            break

        result = execute(text)
        print(result)


if __name__ == "__main__":
    run()
