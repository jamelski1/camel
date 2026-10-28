"""Environment readiness check for the CAMEL Phase 1 setup.

Prints the interpreter, the installed CAMEL version and location, whether an
OpenAI key is configured (only yes/no, the key itself is never printed), and
whether CAMEL's core classes import. Exit code 0 means the package is ready;
LLM-backed demos additionally need the key.

Run:  python demo/check_env.py
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(ROOT / ".env")

    print(f"Python        : {platform.python_version()} ({sys.executable})")

    try:
        import camel
        from camel.agents import ChatAgent  # noqa: F401
        from camel.models import ModelFactory  # noqa: F401
        from camel.societies import RolePlaying  # noqa: F401
    except Exception as exc:  # pragma: no cover - diagnostic path
        print(f"CAMEL import  : FAILED -> {exc!r}")
        return 1

    print(f"CAMEL version : {camel.__version__}")
    print(f"CAMEL path    : {Path(camel.__file__).parent}")

    try:
        commit = subprocess.check_output(
            ["git", "-C", str(ROOT / "camel"), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = "unknown (not a git checkout)"
    print(f"CAMEL commit  : {commit}")

    # Which platform the demos will use (MODEL_PLATFORM in .env, default openai)
    # and whether its key is present. Table kept in sync with role_playing_demo.py.
    key_vars = {
        "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
        "cerebras": "CEREBRAS_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
        "ollama": None,
    }
    chosen = (os.getenv("MODEL_PLATFORM") or "openai").lower()
    key_var = key_vars.get(chosen, f"{chosen.upper()}_API_KEY")
    key_present = key_var is None or bool(os.getenv(key_var, "").strip())
    print(f"Platform      : {chosen}  (MODEL_PLATFORM in .env; default openai)")
    print(f"Key           : {key_var or 'none needed'}: "
          f"{'set' if key_present else 'NOT SET'}")
    print()
    if key_present:
        print("Ready: run  python demo/role_playing_demo.py --turns 5")
    else:
        print("Package is ready, but LLM-backed demos are BLOCKED until a key")
        print("is placed in .env (see .env.example). The offline smoke test")
        print("still works:  python demo/role_playing_demo.py --stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
