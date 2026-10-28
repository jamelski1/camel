"""CAMEL Playground: a small web UI for watching two agents work on ANY task.

SENG 691 Group 3, Phase 1. Built on the unmodified upstream CAMEL
(`RolePlaying`, `ChatAgent`); this file is our own code.

What it adds over the plain demo:
  * type any vague task (not just stocks), optionally let a model suggest roles,
  * run the agents in small bursts (1 to 5 turns per click) instead of all at once,
  * a Goal panel: original task, the sharpened task, the roles, and the status,
  * a Narrator agent that explains each burst in plain English for a
    non-technical observer: what happened, how it is going, done or not,
  * two ways for the human to step in between bursts:
      - "Guide the agents": a note both agents see before the next turn,
      - "Ask the assistant": a direct question; the answer is shown and the
        agents then continue from it.

Run standalone:   python demo/playground.py [--stub] [--platform groq] [--model ...]
Run in notebook:  the last cell of demo/role_playing_demo.ipynb
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from camel.agents import ChatAgent, TaskSpecifyAgent
from camel.messages import BaseMessage
from camel.models import ModelFactory
from camel.societies import RolePlaying
from camel.types import ModelPlatformType, ModelType, TaskType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from role_playing_demo import PLATFORMS  # shared platform table, same folder
except ImportError:  # this file was copied on its own (e.g. uploaded to Colab)
    PLATFORMS = {
        "openai": ("OPENAI_API_KEY", "gpt-4.1-mini-2025-04-14"),
        "groq": ("GROQ_API_KEY", "openai/gpt-oss-120b"),
        "gemini": ("GEMINI_API_KEY", "gemini-2.5-flash"),
        "openrouter": ("OPENROUTER_API_KEY", "openrouter/free"),
        "cerebras": ("CEREBRAS_API_KEY", "gpt-oss-120b"),
        "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
        "ollama": (None, "llama3.1:8b"),
    }

DONE_TOKEN = "CAMEL_TASK_DONE"

NARRATOR_SYSTEM = """You narrate for a non-technical observer watching two AI agents work on a \
task. Never write code. Reply in exactly these four short lines, each one sentence:
**Just now:** what happened, in everyday words.
**Going well?** say plainly if they repeat themselves, drift from the goal, or stall.
**Done?** Yes / Partly / No, and a few words why.
**Nudge:** one short suggestion the human could give, or "none"."""

# Appended to the sharpened task when "concise" is on. The task text is quoted inside
# both agents' rulebooks, so both see it. CAMEL's own template asks for "detailed
# implementations"; this pulls the other way so the exchange stays readable.
STYLE_RULES = (
    "Style rules for both of us, which override any request for detail: every message is at "
    "most 80 words (about five short sentences), in plain language a non-expert can follow. "
    "Prefer a short list of concrete choices over explanations. Give code only if the "
    "instruction explicitly asks for code, and keep it minimal. Never repeat earlier content."
)

GUIDE_SYSTEM = """You coach a human who is playing the instruction-giving role in a two-agent \
session. Given the goal and the latest exchanges, propose exactly three different next moves \
the human could say to the assistant: short instructions or questions, at most 14 words each, \
each pushing the task forward in a different direction (deepen, broaden, check, decide, finish). \
Plain language, no numbering, no quotes, one per line, nothing else."""

ROLE_SUGGEST_SYSTEM = """You name the two roles for a CAMEL role-playing session. Given a \
task, reply with exactly two lines and nothing else:
ASSISTANT: <the expert who does the work, 2-4 words>
USER: <the person who wants it done and gives instructions, 2-4 words>"""


def friendly(exc: Exception) -> str:
    """Turn provider errors into one line of advice a non-developer can act on."""
    text = str(exc)
    low = text.lower()
    if "429" in low or "rate limit" in low or "rate_limit" in low or "quota" in low or "too many requests" in low:
        return ("The model provider's free-tier limit was reached. Wait a minute and try again, or "
                "switch provider under Advanced (free limits: OpenRouter 50 requests/day until $10 of "
                "credit is bought, then 1,000/day; Groq about 1,000/day). Details: " + text[:200])
    if "401" in low or "invalid api key" in low or "incorrect api key" in low or "unauthorized" in low:
        return "The API key was rejected. Check the key for the chosen provider. Details: " + text[:200]
    if "404" in low or "model not found" in low or "does not exist" in low or "no endpoints" in low:
        return ("That model name is not available on this provider; clear the Model box to use the "
                "provider default. Details: " + text[:200])
    if "timed out" in low or "timeout" in low or "connection" in low:
        return "The provider did not answer in time. Try again. Details: " + text[:200]
    return text


def make_model(platform: str | None, model_name: str | None, stub: bool = False):
    """Same selection rules as role_playing_demo.py, but raises instead of exiting."""
    if stub:
        return ModelFactory.create(
            model_platform=ModelPlatformType.STUB, model_type=ModelType.STUB
        ), "STUB MODEL (offline, canned replies, not an LLM run)"
    platform = (platform or os.getenv("MODEL_PLATFORM") or "openai").lower()
    key_var, default_model = PLATFORMS.get(platform, (f"{platform.upper()}_API_KEY", None))
    model_name = model_name or os.getenv("MODEL_NAME") or default_model
    if not model_name:
        raise ValueError(f"No default model known for '{platform}'. Enter a model name.")
    if key_var and not os.getenv(key_var, "").strip():
        raise ValueError(
            f"{key_var} is not set. Put it in .env, or tick 'offline stub' to try the UI without a key."
        )
    model = ModelFactory.create(model_platform=platform, model_type=model_name)
    return model, f"{model_name} via {platform}"


def choose_model(platform: str | None = None, model_name: str | None = None,
                 stub: bool = False, interactive: bool = True):
    """Resolve provider, model and key the same way everywhere (notebook, script).

    Precedence: arguments > MODEL_PLATFORM / MODEL_NAME / the key variable in the
    environment (.env, or Colab secrets of the same name) > ask in the console
    when interactive > defaults. Returns (model, mode_string). Keys are read
    with a hidden prompt and never printed.
    """
    if stub:
        return make_model(None, None, stub=True)

    def colab_secret(name):
        try:
            from google.colab import userdata
            return (userdata.get(name) or "").strip()
        except Exception:
            return ""

    names = list(PLATFORMS)
    platform = (platform or os.getenv("MODEL_PLATFORM") or colab_secret("MODEL_PLATFORM") or "").lower()
    if not platform and interactive:
        print("Which model provider do you want to use?")
        for i, name in enumerate(names, 1):
            key_var, default_model = PLATFORMS[name]
            print(f"  {i}. {name:<11} default model: {default_model:<28} key: {key_var or 'none needed'}")
        print("  (Groq, Gemini, Cerebras and OpenRouter's free models cost nothing; Ollama is local only.)")
        for _ in range(3):
            choice = input("Number or name [1]: ").strip() or "1"
            platform = (names[int(choice) - 1]
                        if choice.isdigit() and 1 <= int(choice) <= len(names) else choice.lower())
            if platform in names or platform in {p.value for p in ModelPlatformType}:
                break
            print(f"  '{choice}' is not a known platform, try again.")
        else:
            print("  Falling back to openai.")
            platform = "openai"
    platform = platform or "openai"

    key_var, default_model = PLATFORMS.get(platform, (f"{platform.upper()}_API_KEY", None))
    model_name = model_name or os.getenv("MODEL_NAME") or default_model
    if not model_name and interactive:
        model_name = input(f"Model name on {platform}: ").strip()

    if key_var and not os.getenv(key_var, "").strip():
        secret = colab_secret(key_var)
        if secret:
            os.environ[key_var] = secret
        elif interactive:
            from getpass import getpass
            os.environ[key_var] = getpass(f"Paste your {key_var} (hidden): ").strip()
    try:
        return make_model(platform, model_name, stub=False)
    except ImportError as exc:  # a few backends (mistral, aws-bedrock, ...) need their own SDK
        raise ImportError(
            f"The '{platform}' backend needs an extra package: {exc}. "
            "Install it, restart the kernel, and run again."
        ) from exc


@dataclass
class Playground:
    """Holds one role-playing session and drives it in bursts."""

    model: object = None
    mode: str = ""
    session: RolePlaying | None = None
    narrator: ChatAgent | None = None
    assistant_role: str = ""
    user_role: str = ""
    turn: int = 0
    done: bool = False
    terminated: str = ""
    pending_msg: BaseMessage | None = None  # next message to feed into session.step
    transcript: list = field(default_factory=list)  # list of (speaker, text)
    pending_guidance: str = ""
    task_original: str = ""
    specified_task: str = ""   # the task-specifier's version, without the style rules
    concise: bool = True
    cards: list = field(default_factory=list)  # the three suggested next moves for the human

    # ----- setup -----------------------------------------------------------
    def start(self, task: str, assistant_role: str, user_role: str,
              platform: str | None, model_name: str | None, stub: bool,
              specify: bool, model=None, mode: str | None = None,
              concise: bool = True) -> None:
        """Begin a session. Pass a prebuilt `model` (from choose_model) to skip selection.
        `concise` appends STYLE_RULES to the task so both agents keep messages short."""
        task = (task or "").strip()
        if not task:
            raise ValueError("Type a task first, for example 'plan a weekend trip to Montreal'.")
        if model is not None:
            self.model, self.mode = model, mode or str(getattr(model, "model_type", "custom model"))
        else:
            self.model, self.mode = make_model(platform, model_name, stub)
        self.task_original = task
        self.concise = concise
        self.assistant_role = (assistant_role or "Expert Assistant").strip()
        self.user_role = (user_role or "Person who wants the task done").strip()

        # Step 1 ourselves (same agent RolePlaying would use) so the style rules can be
        # added AFTER sharpening; otherwise the specifier would rewrite them away.
        if specify:
            specifier = TaskSpecifyAgent(model=self.model, task_type=TaskType.AI_SOCIETY)
            self.specified_task = str(specifier.run(
                task, meta_dict=dict(assistant_role=self.assistant_role, user_role=self.user_role)
            )).strip()
        else:
            self.specified_task = ""
        task_text = self.specified_task or task
        if concise:
            task_text = f"{task_text}\n\n{STYLE_RULES}"
        self.session = RolePlaying(
            assistant_role_name=self.assistant_role,
            user_role_name=self.user_role,
            task_prompt=task_text,
            with_task_specify=False,
            assistant_agent_kwargs=dict(model=self.model),   # per-agent, so CAMEL does not
            user_agent_kwargs=dict(model=self.model),        # log the "global model" warning
        )
        self.narrator = ChatAgent(system_message=NARRATOR_SYSTEM, model=self.model)
        self.turn, self.done, self.terminated = 0, False, ""
        self.transcript = []
        self.pending_guidance = ""
        self.pending_msg = self.session.init_chat()

    def suggest_roles(self, task: str, platform, model_name, stub) -> tuple[str, str]:
        model, _ = make_model(platform, model_name, stub)
        agent = ChatAgent(system_message=ROLE_SUGGEST_SYSTEM, model=model)
        text = agent.step(f"Task: {task.strip()}").msgs[0].content
        assistant, user = "Expert Assistant", "Person who wants the task done"
        for line in text.splitlines():
            if line.upper().startswith("ASSISTANT:"):
                assistant = line.split(":", 1)[1].strip() or assistant
            elif line.upper().startswith("USER:"):
                user = line.split(":", 1)[1].strip() or user
        return assistant, user

    # ----- running -----------------------------------------------------------
    def run_turns(self, n: int) -> list[tuple[str, str]]:
        """Run up to n exchanges; return the new transcript entries."""
        if self.session is None:
            raise ValueError("Press Start first.")
        if self.done or self.terminated:
            return []
        new: list[tuple[str, str]] = []
        for _ in range(int(n)):
            msg = self.pending_msg
            if self.pending_guidance:
                # Both agents see the note: the user agent reads it in this message,
                # the assistant agent sees it echoed in the user's reply context.
                # (The note itself was added to the transcript by guide().)
                msg = BaseMessage.make_assistant_message(
                    role_name=msg.role_name,
                    content=msg.content
                    + f"\n\n[Note from the human supervisor: {self.pending_guidance}]",
                )
                self.pending_guidance = ""
            assistant_response, user_response = self.session.step(msg)
            if assistant_response.terminated or user_response.terminated:
                who = self.assistant_role if assistant_response.terminated else self.user_role
                info = (assistant_response if assistant_response.terminated else user_response).info
                self.terminated = f"{who} stopped: {info.get('termination_reasons')}"
                new.append(("System", self.terminated))
                break
            self.turn += 1
            new.append((self.user_role, user_response.msg.content))
            new.append((self.assistant_role, assistant_response.msg.content))
            self.pending_msg = assistant_response.msg
            if DONE_TOKEN in user_response.msg.content:
                self.done = True
                new.append(("System", f"{self.user_role} declared the task done."))
                break
        self.transcript.extend(new)
        return new

    def ask_assistant(self, question: str) -> list[tuple[str, str]]:
        """Human asks the assistant agent directly; the agents continue from its answer."""
        if self.session is None:
            raise ValueError("Press Start first.")
        question = (question or "").strip()
        if not question:
            raise ValueError("Type a question first.")
        hint = " Answer in at most three sentences." if self.concise else ""
        reply = self.session.assistant_agent.step(
            f"[Question from the human supervisor] {question}{hint}"
        ).msgs[0]
        new = [("Human", question), (self.assistant_role, reply.content)]
        self.pending_msg = reply  # the user agent will react to this answer next
        self.transcript.extend(new)
        return new

    def suggest_next(self) -> list[str]:
        """Three short candidate next moves for the human holding the user role.
        A coaching agent proposes them from the goal and the latest exchanges."""
        if self.session is None:
            raise ValueError("Press Start first.")
        recent = "\n\n".join(
            f"{who}: {text}" for who, text in self.transcript[-4:]
            if who not in ("System", "Narrator")
        )
        coach = ChatAgent(system_message=GUIDE_SYSTEM, model=self.model)
        text = coach.step(
            f"Goal: {self.specified_task or self.task_original}\n"
            f"The human plays {self.user_role}; the assistant is {self.assistant_role}.\n"
            f"Turns so far: {self.turn}.\n\nMost recent exchanges:\n{recent or '(none yet)'}"
        ).msgs[0].content
        lines = [ln.strip().lstrip("-*0123456789.) ").strip().strip('"') for ln in text.splitlines()]
        lines = [ln for ln in lines if ln][:3]
        fallbacks = ["Summarise what we have so far", "What is the most important next step?",
                     "Give me three concrete options to choose from"]
        while len(lines) < 3:
            lines.append(fallbacks[len(lines)])
        self.cards = lines
        return lines

    def instruct(self, text: str, show_human: bool = True) -> list[tuple[str, str]]:
        """The human takes the user role for one turn. The instruction goes to the assistant
        and is recorded in the user agent's memory, so later autonomous turns stay coherent.
        show_human=False when the caller already put the human's line in the transcript."""
        if self.session is None:
            raise ValueError("Press Start first.")
        if self.done or self.terminated:
            raise ValueError("This conversation has finished. Press New task to start another.")
        text = (text or "").strip()
        if not text:
            raise ValueError("Pick a suggestion or type an instruction first.")
        content = f"Instruction: {text}\nInput: None"
        self.session.user_agent.record_message(
            BaseMessage.make_assistant_message(role_name=self.user_role, content=content)
        )
        reply = self.session.assistant_agent.step(content).msgs[0]
        self.turn += 1
        self.pending_msg = reply
        new = ([("Human", text)] if show_human else []) + [(self.assistant_role, reply.content)]
        self.transcript.extend(new)
        return new

    def guide(self, note: str) -> None:
        """Queue a note that both agents see before the next turn; shows in the chat now."""
        if self.session is None:
            raise ValueError("Press Start first.")
        note = (note or "").strip()
        if not note:
            raise ValueError("Type a note first.")
        self.pending_guidance = note
        self.transcript.append(("Human", note))

    # ----- reporting -----------------------------------------------------------
    def narrate(self, new_entries: list[tuple[str, str]]) -> str:
        if self.narrator is None or not new_entries:
            return ""
        recent = "\n\n".join(f"{who}: {text}" for who, text in new_entries)
        prompt = (
            f"Goal: {self.specified_task or self.task_original}\n"
            f"Roles: {self.assistant_role} (does the work), {self.user_role} (gives instructions)\n"
            f"Turns so far: {self.turn}. Task declared done: {self.done}.\n\n"
            f"Most recent exchanges:\n{recent}"
        )
        return self.narrator.step(prompt).msgs[0].content

    def goal_markdown(self) -> str:
        if self.session is None:
            return "_No session yet. Type a task and press Start._"
        if self.done:
            status = "Done: the user agent declared the task complete."
        elif self.terminated:
            status = f"Stopped: {self.terminated}"
        else:
            status = "In progress. Run more turns, or step in below."
        parts = [f"**Model:** {self.mode}", f"**Original task:** {self.task_original}"]
        if self.specified_task and self.specified_task != self.task_original:
            parts.append(f"**Sharpened by the task-specifier agent:** {self.specified_task}")
        parts += [
            f"**Roles:** {self.assistant_role} does the work; {self.user_role} gives one instruction per turn.",
            f"**Turns completed:** {self.turn}",
            f"**Status:** {status}",
        ]
        return "\n\n".join(parts)


def to_chat_messages(entries: list[tuple[str, str]], assistant_role: str) -> list[dict]:
    """Map transcript entries onto a two-sided chat: the assistant agent on the right,
    everyone else (user agent, human, narrator, system) on the left, each labelled."""
    out = []
    for who, text in entries:
        side = "assistant" if who == assistant_role else "user"
        label = {"Human": "You", "System": "System", "Narrator": "Narrator"}.get(who, who)
        out.append({"role": side, "content": f"**{label}**\n\n{text}"})
    return out


def build_ui(default_platform: str | None = None, default_model: str | None = None,
             default_stub: bool = False):
    import gradio as gr

    platform_names = list(PLATFORMS)

    # One Playground per browser session (gr.State deep-copies the default for
    # each visitor), so people sharing a hosted deployment do not see or steer
    # each other's conversations. Every callback receives it and hands it back.
    def _chat(pg):
        return to_chat_messages(pg.transcript, pg.assistant_role)

    def status_of(pg):
        """One strip above the chat: goal, who is who, turn count, state."""
        if pg.session is None:
            return ""
        if pg.done:
            badge = "**Done**: the task was declared complete"
        elif pg.terminated:
            badge = f"**Stopped**: {pg.terminated}"
        else:
            badge = "In progress"
        goal = pg.specified_task or pg.task_original
        strip = (
            f"**Goal:** {goal}\n\n"
            f"**{pg.assistant_role}** does the work &nbsp;·&nbsp; "
            f"**{pg.user_role}** gives the instructions &nbsp;·&nbsp; "
            f"Turn {pg.turn} &nbsp;·&nbsp; {badge} &nbsp;·&nbsp; <small>{pg.mode}</small>"
        )
        if not (pg.done or pg.terminated):
            strip += ("\n\n**Your move:** pick a suggested next step, type your own, "
                      "or let the AI User take a turn.")
        return strip

    # ----- the three cards: suggested next moves for the human -----------------
    def cards_of(pg):
        """Button updates for the three cards; hidden when there is nothing to choose."""
        if pg.session is None or pg.done or pg.terminated or len(pg.cards) < 3:
            return [gr.update(value="", visible=False)] * 3
        return [gr.update(value=c, visible=True) for c in pg.cards[:3]]

    def refresh_cards(pg):
        try:
            pg.suggest_next()
        except Exception as exc:
            pg.cards = []
            pg.transcript.append(("System", f"Could not suggest next moves: {friendly(exc)}"))

    def view(pg):
        return (pg, status_of(pg), _chat(pg), *cards_of(pg))

    hidden_cards = [gr.update(value="", visible=False)] * 3

    def on_start(pg, task, a_role, u_role, platform, model_name, stub, specify, concise):
        """Generator: switch screens at once so the tap visibly did something (setup can
        take a minute on free models), fill in the real state, then offer the first cards."""
        if not (task or "").strip():
            raise gr.Error("Type a task first.")
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               "**Setting up:** choosing roles and sharpening the task. "
               "This can take up to a minute on free models.",
               [{"role": "user", "content": "**System**\n\nStarting..."}], a_role, u_role, *hidden_cards)
        try:
            if not ((a_role or "").strip() and (u_role or "").strip()):
                a_role, u_role = pg.suggest_roles(task, platform, model_name, stub)
            pg.start(task, a_role, u_role, platform, model_name, stub, specify, concise=concise)
        except Exception as exc:
            # Back to the setup screen with the message, so the user can fix and retry.
            yield (pg, gr.update(visible=True), gr.update(visible=False), "", [], a_role, u_role, *hidden_cards)
            raise gr.Error(f"Could not start: {friendly(exc)}")
        pg.transcript.append((
            "System",
            f"Session started. **{pg.assistant_role}** does the work. You give the instructions, "
            f"or let the AI playing **{pg.user_role}** do it for a turn or three.",
        ))
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               status_of(pg), _chat(pg), a_role, u_role, *hidden_cards)
        refresh_cards(pg)
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               status_of(pg), _chat(pg), a_role, u_role, *cards_of(pg))

    def run(pg, n):
        """Autonomous burst: the AI User instructs. The chat updates after every turn, the
        Narrator sums up the burst, then fresh cards are offered to the human."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if pg.done or pg.terminated:
            raise gr.Error("This conversation has finished. Press New task to start another.")
        pg.cards = []                      # hide the cards while the agents work
        yield view(pg)
        burst = []
        for _ in range(n):
            try:
                new = pg.run_turns(1)
            except Exception as exc:
                raise gr.Error(friendly(exc))
            if not new:
                break
            burst += new
            yield view(pg)
            if pg.done or pg.terminated:
                break
        if burst:
            try:
                pg.transcript.append(("Narrator", pg.narrate(burst)))
            except Exception as exc:
                pg.transcript.append(("System", f"Narrator unavailable: {friendly(exc)}"))
        yield view(pg)
        if not (pg.done or pg.terminated):
            refresh_cards(pg)
            yield view(pg)

    def on_next(pg):
        yield from run(pg, 1)

    def on_three(pg):
        yield from run(pg, 3)

    def guided(pg, text):
        """One human-led turn: show the instruction, get the assistant's answer, offer new cards."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if pg.done or pg.terminated:
            raise gr.Error("This conversation has finished. Press New task to start another.")
        pg.cards = []
        pg.transcript.append(("Human", text.strip()))
        yield view(pg)
        try:
            pg.instruct(text, show_human=False)
        except Exception as exc:
            raise gr.Error(friendly(exc))
        yield view(pg)
        refresh_cards(pg)
        yield view(pg)

    def make_card_handler(i):
        def on_card(pg):
            if pg.session is None or len(pg.cards) <= i:
                raise gr.Error("Start a task first.")
            yield from guided(pg, pg.cards[i])
        return on_card

    def on_send(pg, text, how):
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if not (text or "").strip():
            raise gr.Error("Type something first.")
        if (how or "").startswith("Note"):
            try:
                pg.guide(text)
            except Exception as exc:
                raise gr.Error(str(exc))
            pg.transcript.append(("System", "Noted. Both agents will see this before the next turn."))
            yield (pg, status_of(pg), _chat(pg), "", *cards_of(pg))
            return
        for pg_, st, ch, *cs in guided(pg, text):
            yield (pg_, st, ch, "", *cs)

    def on_reset():
        """Fresh Playground and fresh fields: task and both roles are cleared so the
        model chooses roles for the next task instead of reusing the last ones."""
        return (Playground(), gr.update(visible=True), gr.update(visible=False), "", [],
                "", "", "", *hidden_cards)

    with gr.Blocks(title="CAMEL Playground") as ui:
        state = gr.State(Playground())
        gr.Markdown(
            "# CAMEL Playground\n"
            "Give two AI agents a task. Steer it yourself with suggested next moves, "
            "or let the agents run on their own for a few turns."
        )

        # Stage 1: one box and one button. Everything else is behind "Advanced".
        with gr.Group(visible=True) as setup:
            task = gr.Textbox(
                label="What should the agents work on?",
                placeholder=("Anything. Plan a weekend in Montreal on a student budget, write a bedtime "
                             "story about a shy robot, design a four-week running plan..."),
                lines=3,
            )
            with gr.Accordion("Advanced: roles and model", open=False):
                with gr.Row():
                    a_role = gr.Textbox(label="Who does the work",
                                        placeholder="leave blank and a model will choose")
                    u_role = gr.Textbox(label="Who gives the instructions",
                                        placeholder="leave blank and a model will choose")
                with gr.Row():
                    platform = gr.Dropdown(platform_names,
                                           value=default_platform or os.getenv("MODEL_PLATFORM") or "openai",
                                           label="Model provider", allow_custom_value=True)
                    model_name = gr.Textbox(label="Model (blank = provider default)", value=default_model or "")
                with gr.Row():
                    concise = gr.Checkbox(label="Concise replies (about 80 words, code only on request)", value=True)
                    specify = gr.Checkbox(label="Sharpen the task first (task-specifier agent)", value=True)
                    stub = gr.Checkbox(label="Offline test mode (no key, canned replies)", value=default_stub)
            start_btn = gr.Button("Start", variant="primary", size="lg")

        # Stage 2: status strip, the conversation (Narrator included), three cards with
        # suggested next moves, one box to type your own, and the autonomous options.
        with gr.Group(visible=False) as session:
            status = gr.Markdown("")
            chat = gr.Chatbot(show_label=False, height=520,
                              placeholder="The conversation will appear here.")
            with gr.Row():
                card_btns = [gr.Button("", visible=False, size="sm") for _ in range(3)]
            with gr.Row():
                text = gr.Textbox(show_label=False, scale=4,
                                  placeholder="Or type your own instruction or question for the assistant...")
                how = gr.Radio(["To the assistant", "Note for both agents"], value="To the assistant",
                               show_label=False, scale=2)
                send_btn = gr.Button("Send", variant="primary", scale=1)
            with gr.Row():
                auto1_btn = gr.Button("Let the AI User take one turn")
                auto3_btn = gr.Button("Let the AI User run 3 turns")
                reset_btn = gr.Button("New task")

        c1, c2, c3 = card_btns
        view_out = [state, status, chat, c1, c2, c3]
        start_inputs = [state, task, a_role, u_role, platform, model_name, stub, specify, concise]
        start_outputs = [state, setup, session, status, chat, a_role, u_role, c1, c2, c3]
        start_btn.click(on_start, start_inputs, start_outputs, api_name="start")
        task.submit(on_start, start_inputs, start_outputs)   # Enter in the task box also starts
        for i, btn in enumerate(card_btns):
            btn.click(make_card_handler(i), [state], view_out, api_name=f"card{i + 1}")
        auto1_btn.click(on_next, [state], view_out, api_name="run")
        auto3_btn.click(on_three, [state], view_out, api_name="run3")
        send_btn.click(on_send, [state, text, how], [state, status, chat, text, c1, c2, c3], api_name="send")
        text.submit(on_send, [state, text, how], [state, status, chat, text, c1, c2, c3])
        reset_btn.click(on_reset, None,
                        [state, setup, session, status, chat, task, a_role, u_role, c1, c2, c3],
                        api_name="reset")
    return ui


def main() -> None:
    parser = argparse.ArgumentParser(description="CAMEL Playground web UI")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--stub", action="store_true", help="start with the offline stub ticked")
    parser.add_argument("--host", default=None,
                        help="bind address (default 127.0.0.1; 0.0.0.0 when PORT is set by a host like Render)")
    parser.add_argument("--port", type=int, default=None,
                        help="port (default: $PORT if set by the host, else 7860)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    hosted = "PORT" in os.environ          # Render and similar hosts set PORT
    port = args.port or int(os.environ.get("PORT", 7860))
    host = args.host or ("0.0.0.0" if hosted else "127.0.0.1")

    # Optional login so a public URL cannot spend the team's API credits:
    # set PLAYGROUND_USER and PLAYGROUND_PASSWORD in the environment.
    auth = None
    if os.getenv("PLAYGROUND_PASSWORD"):
        auth = (os.getenv("PLAYGROUND_USER", "group3"), os.environ["PLAYGROUND_PASSWORD"])
    elif hosted:
        print("WARNING: no PLAYGROUND_PASSWORD set; the hosted Playground is open to anyone with the URL.")

    import gradio as gr
    ui = build_ui(args.platform, args.model, args.stub)
    ui.launch(server_name=host, server_port=port, auth=auth, theme=gr.themes.Soft(),
              inbrowser=not (args.no_browser or hosted), show_error=True)


if __name__ == "__main__":
    main()
