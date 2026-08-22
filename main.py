"""Entry point. `python main.py` starts the typed-command REPL (Phase 1).
`python main.py --listen` starts the wake-word + voice pipeline (Phase 2) instead --
either way, the front-end just hands text to assistant.executor.execute().
`python main.py --enroll` records your voice once (Phase 3) so --listen ignores
anyone else saying the wake word.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "config" / ".env")  # before anything reads os.environ

from assistant.cli import run as run_cli


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal voice assistant")
    parser.add_argument(
        "--listen",
        action="store_true",
        help="Start wake-word + voice listening (Phase 2) instead of the typed REPL",
    )
    parser.add_argument(
        "--enroll",
        action="store_true",
        help="Record your voice once to enable speaker verification (Phase 3)",
    )
    args = parser.parse_args()

    if args.enroll:
        from assistant.enroll import run as run_enroll

        run_enroll()
    elif args.listen:
        from assistant.listen import run as run_listen

        try:
            run_listen()
        except RuntimeError as e:
            print(f"Error: {e}")
    else:
        run_cli()


if __name__ == "__main__":
    main()
