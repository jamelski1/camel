"""CAMEL role-playing demo for SENG 691 Group 3 (Phase 1).

Adapted from upstream examples/ai_society/role_playing.py (Apache-2.0,
Copyright 2023-2026 CAMEL-AI.org). Changes from upstream:
  * loads OPENAI_API_KEY from the project .env file (python-dotenv),
  * fails early with a clear message if no key is configured,
  * --turns caps the conversation (upstream default is 50) to limit API cost,
  * --stub runs the same pipeline on CAMEL's built-in offline StubModel,
    which returns canned text; it proves the agent machinery works but is
    NOT an LLM run,
  * --platform / --model (or MODEL_PLATFORM / MODEL_NAME in .env) pick any
    model backend CAMEL supports, so each team member can use the provider
    they have a key for (OpenAI, Groq, Gemini, OpenRouter, Ollama, ...),
  * plain print() instead of animated printing so logs capture cleanly.

Usage:
  python demo/role_playing_demo.py                  # OpenAI (default), needs key
  python demo/role_playing_demo.py --turns 5
  python demo/role_playing_demo.py --stub           # offline smoke test
  python demo/role_playing_demo.py --platform groq  # free tier, needs GROQ_API_KEY
  python demo/role_playing_demo.py --platform ollama --model llama3.1:8b  # local, no key
  python demo/role_playing_demo.py --list-platforms
"""

import argparse
import os
import sys
from pathlib import Path

from colorama import Fore, Style
from dotenv import load_dotenv

from camel.models import ModelFactory
from camel.societies import RolePlaying
from camel.types import ModelPlatformType, ModelType

ROOT = Path(__file__).resolve().parents[1]
TASK_PROMPT = "Develop a trading bot for the stock market"

# Platforms we have documented: name -> (env var holding the key, default model).
# Any other CAMEL platform name (see `python demo/role_playing_demo.py
# --list-platforms`) also works if you pass --model; CAMEL checks its own key.
PLATFORMS = {
    "openai": ("OPENAI_API_KEY", "gpt-4.1-mini-2025-04-14"),
    "groq": ("GROQ_API_KEY", "openai/gpt-oss-120b"),           # free tier (Llama 3.x retired 2026-08)
    "gemini": ("GEMINI_API_KEY", "gemini-2.5-flash"),          # free tier
    "openrouter": ("OPENROUTER_API_KEY", "openrouter/free"),   # auto-routes to a free model
    "cerebras": ("CEREBRAS_API_KEY", "gpt-oss-120b"),          # 30-day trial credits only
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
    "ollama": (None, "llama3.1:8b"),                           # local, no key
}


def build_model(stub: bool, platform: str | None, model_name: str | None):
    """Pick the backend. Precedence: CLI flag > .env/environment > default."""
    if stub:
        return ModelFactory.create(
            model_platform=ModelPlatformType.STUB, model_type=ModelType.STUB
        )
    platform = (platform or os.getenv("MODEL_PLATFORM") or "openai").lower()
    key_var, default_model = PLATFORMS.get(platform, (None, None))
    model_name = model_name or os.getenv("MODEL_NAME") or default_model
    if model_name is None:
        sys.exit(
            f"ERROR: no default model known for platform '{platform}'; "
            "pass --model <name> (or MODEL_NAME in .env)."
        )
    if key_var and not os.getenv(key_var, "").strip():
        sys.exit(
            f"ERROR: {key_var} is not set (needed for platform '{platform}').\n"
            "  Copy .env.example to .env and add your key, choose another "
            "platform with --platform, or run with --stub for the offline test."
        )
    try:
        return ModelFactory.create(model_platform=platform, model_type=model_name)
    except ValueError as exc:  # unknown platform name, or a backend's own key check
        sys.exit(f"ERROR: {exc}\n  Run with --list-platforms to see valid names.")
    except ImportError as exc:  # some backends (mistral, aws-bedrock, ...) need their own SDK
        sys.exit(
            f"ERROR: the '{platform}' backend needs an extra package: {exc}\n"
            "  Install it into .venv (uv pip install --python .venv/bin/python <package>) "
            "and run again."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--turns", type=int, default=10, help="max chat turns")
    parser.add_argument("--stub", action="store_true", help="offline stub model")
    parser.add_argument("--platform", default=None,
                        help="model backend: " + ", ".join(PLATFORMS) +
                             ", or any CAMEL platform name (default: openai)")
    parser.add_argument("--model", default=None,
                        help="model name for the platform (default: see PLATFORMS)")
    parser.add_argument("--list-platforms", action="store_true",
                        help="print every platform name CAMEL knows and exit")
    args = parser.parse_args()

    if args.list_platforms:
        for p in ModelPlatformType:
            print(p.value)
        return

    load_dotenv(ROOT / ".env")
    model = build_model(args.stub, args.platform, args.model)
    mode = "STUB MODEL (offline, canned replies)" if args.stub else (
        f"{model.model_type} via {args.platform or os.getenv('MODEL_PLATFORM') or 'openai'}"
    )
    print(Fore.MAGENTA + f"Mode: {mode}" + Style.RESET_ALL)

    session = RolePlaying(
        assistant_role_name="Python Programmer",
        assistant_agent_kwargs=dict(model=model),
        user_role_name="Stock Trader",
        user_agent_kwargs=dict(model=model),
        task_prompt=TASK_PROMPT,
        with_task_specify=True,
        task_specify_agent_kwargs=dict(model=model),
    )

    print(Fore.GREEN + f"AI Assistant sys message:\n{session.assistant_sys_msg}\n")
    print(Fore.BLUE + f"AI User sys message:\n{session.user_sys_msg}\n")
    print(Fore.YELLOW + f"Original task prompt:\n{TASK_PROMPT}\n")
    print(Fore.CYAN + f"Specified task prompt:\n{session.specified_task_prompt}\n")
    print(Fore.RED + f"Final task prompt:\n{session.task_prompt}\n")

    input_msg = session.init_chat()
    for n in range(1, args.turns + 1):
        assistant_response, user_response = session.step(input_msg)

        if assistant_response.terminated:
            print(Fore.GREEN + "AI Assistant terminated. Reason: "
                  f"{assistant_response.info['termination_reasons']}.")
            break
        if user_response.terminated:
            print(Fore.GREEN + "AI User terminated. Reason: "
                  f"{user_response.info['termination_reasons']}.")
            break

        print(Fore.BLUE + f"[turn {n}] AI User:\n\n{user_response.msg.content}\n")
        print(Fore.GREEN + f"[turn {n}] AI Assistant:\n\n"
              f"{assistant_response.msg.content}\n")

        if "CAMEL_TASK_DONE" in user_response.msg.content:
            print(Fore.YELLOW + "Task marked done by AI User.")
            break

        input_msg = assistant_response.msg

    print(Style.RESET_ALL + f"Finished after {n} turn(s).")


if __name__ == "__main__":
    main()
