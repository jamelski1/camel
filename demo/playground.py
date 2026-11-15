"""CAMEL Playground: a small web UI for watching two agents work on ANY task.

SENG 691 Group 3, Phases 1 and 2. Built on the unmodified upstream CAMEL
(`RolePlaying`, `ChatAgent`); this file is our own code.

What it adds over the plain demo:
  * type any vague task (not just stocks), optionally let a model suggest roles,
  * run the agents in small bursts instead of all at once, or hand the whole
    run to the AI User in agent mode,
  * a Goal panel: original task, the sharpened task, the roles, and the status,
  * a Narrator agent that explains each burst in plain English for a
    non-technical observer: what happened, how it is going, done or not,
  * two ways for the human to step in between bursts:
      - "Guide the agents": a note both agents see before the next turn,
      - "Ask the assistant": a direct question; the answer is shown and the
        agents then continue from it.
  * Phase 2: sessions are saved to disk and can be reopened after a restart
    (session_store.py); a local progress monitor flags repeats, stalls, and
    drift, with a configurable turn limit and pause/resume
    (progress_monitor.py); and the model setup is checked before a run while
    transient provider errors are retried (model_config.py).

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

try:
    from session_store import SessionStore
    from progress_monitor import ProgressMonitor
    from model_config import preflight as run_preflight, call_with_retry
except ImportError:  # playground.py copied on its own: Phase 2 features stay off
    SessionStore = None
    ProgressMonitor = None
    run_preflight = None
    call_with_retry = None

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
    # Phase 2 fields. The store is never held open on the object (gr.State
    # deep-copies it per visitor); only its path is kept, and each write opens
    # a short-lived connection. See session_store.py, progress_monitor.py,
    # and model_config.py.
    db_path: str = ""
    session_id: int | None = None   # this session's row in the store
    monitor: object = None          # ProgressMonitor for this session
    platform_name: str = ""         # resolved provider, saved for reopen
    model_label: str = ""           # resolved model name, saved for reopen
    max_turns: int = 12             # Enhancement 2: configurable turn limit
    paused: bool = False            # Enhancement 2: pause / resume
    busy: str = ""                  # what the app is doing right now, shown in the UI

    # ----- setup -----------------------------------------------------------
    def start(self, task: str, assistant_role: str, user_role: str,
              platform: str | None, model_name: str | None, stub: bool,
              specify: bool, model=None, mode: str | None = None,
              concise: bool = True, max_turns: int = 12) -> None:
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
        # Phase 2: a fresh progress monitor, the resolved provider and model
        # labels, and a saved row so this session can be reopened after a
        # restart (Enhancements 1 and 2).
        self.max_turns = max(1, int(max_turns))
        self.paused = False
        self.busy = ""
        self.monitor = (ProgressMonitor(task_text=self.specified_task or task)
                        if ProgressMonitor is not None else None)
        if stub:
            self.platform_name, self.model_label = "stub", "STUB"
        else:
            self.platform_name = (platform or os.getenv("MODEL_PLATFORM") or "openai").lower()
            self.model_label = (model_name or os.getenv("MODEL_NAME")
                                or PLATFORMS.get(self.platform_name, (None, ""))[1] or "")
        self.session_id = None
        self._save_new_session()

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
            if self.paused:
                break
            if self.turn >= self.max_turns:
                self.terminated = f"Turn limit reached ({self.max_turns} turns)"
                new.append(("System", f"Turn limit reached ({self.max_turns} turns). "
                                      "Raise the limit under Advanced, or start a new task."))
                break
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
            assistant_response, user_response = self._call(lambda: self.session.step(msg))
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
        if not (self.done or self.terminated or self.paused) \
                and self.turn >= self.max_turns:
            # The burst ended exactly on the limit, so the in-loop check never
            # fired. Mark the run stopped here, or the status strip would keep
            # saying In progress and the saved session would stay in_progress.
            self.terminated = f"Turn limit reached ({self.max_turns} turns)"
            new.append(("System", f"Turn limit reached ({self.max_turns} turns). "
                                  "Raise the limit under Advanced, or start a new task."))
        self._check_progress(new)
        self._record(new)
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
        self._record(new)
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
            raise ValueError("This conversation has finished. Press New session to start another.")
        text = (text or "").strip()
        if not text:
            raise ValueError("Pick a suggestion or type an instruction first.")
        content = f"Instruction: {text}\nInput: None"
        self.session.user_agent.record_message(
            BaseMessage.make_assistant_message(role_name=self.user_role, content=content)
        )
        reply = self._call(lambda: self.session.assistant_agent.step(content)).msgs[0]
        self.turn += 1
        self.pending_msg = reply
        new = ([("Human", text)] if show_human else []) + [(self.assistant_role, reply.content)]
        self._check_progress(new)
        self._record(new)
        return new

    def guide(self, note: str) -> None:
        """Queue a note that both agents see before the next turn; shows in the chat now."""
        if self.session is None:
            raise ValueError("Press Start first.")
        note = (note or "").strip()
        if not note:
            raise ValueError("Type a note first.")
        self.pending_guidance = note
        self._record([("Human", note)])
        store = self._store()
        if store is not None and self.session_id is not None:
            try:
                store.add_guidance(self.session_id, self.turn, note)
            finally:
                store.close()

    # ----- Phase 2: persistence, progress controls, recovery -------------------
    def _db_path(self) -> str:
        return self.db_path or str(
            Path(os.getenv("PLAYGROUND_DB", ROOT / "data" / "sessions.db")))

    def _store(self):
        """A short-lived SessionStore, or None when storage is unavailable."""
        if SessionStore is None:
            return None
        return SessionStore(self._db_path())

    def _save_new_session(self) -> None:
        store = self._store()
        if store is None:
            return
        try:
            self.session_id = store.create_session(
                task_original=self.task_original,
                specified_task=self.specified_task,
                assistant_role=self.assistant_role, user_role=self.user_role,
                platform=self.platform_name, model_name=self.model_label,
                mode=self.mode, concise=self.concise)
        finally:
            store.close()

    def _record(self, entries: list) -> None:
        """Append entries to the transcript and, write-through, to the store,
        so the saved session on disk is always the current one."""
        self.transcript.extend(entries)
        if self.session_id is None:
            return
        store = self._store()
        if store is None:
            return
        try:
            for who, text in entries:
                store.append_message(self.session_id, who, text)
            store.update_state(
                self.session_id, turn=self.turn,
                status="done" if self.done else
                       ("stopped" if self.terminated else "in_progress"))
        finally:
            store.close()

    def _call(self, fn):
        """One model call, retried on transient provider errors (Enhancement 3).
        Each retry leaves a System note in the transcript saying what happened."""
        if call_with_retry is None:
            return fn()

        def note(attempt, category, exc):
            self._record([("System",
                           f"The model call hit a {category.replace('_', ' ')} "
                           f"problem and is being retried "
                           f"(attempt {attempt + 1} of 3).")])

        return call_with_retry(fn, on_retry=note)

    def _check_progress(self, entries: list) -> None:
        """Run the local progress monitor over a finished turn and append any
        flags to the same entries, as Narrator progress checks (Enhancement 2)."""
        if self.monitor is None:
            return
        for flag in self.monitor.observe(self.turn, entries,
                                         self.assistant_role, self.user_role):
            entries.append(("Narrator", flag.line()))

    def set_paused(self, paused: bool) -> None:
        """Pause or resume autonomous running (Enhancement 2)."""
        self.paused = bool(paused)
        self._record([("System", "Run paused by the human supervisor."
                       if self.paused else "Run resumed by the human supervisor.")])

    @classmethod
    def reopen(cls, session_id: int, db_path: str) -> "Playground":
        """Rebuild a Playground from a saved session (Enhancement 1): the task,
        roles, transcript, guidance, model configuration, turn count, and
        completion status come back from the store, and the agents' memories
        are rebuilt from the transcript so the run can continue."""
        if SessionStore is None:
            raise ValueError("Session storage is unavailable.")
        store = SessionStore(db_path)
        try:
            data = store.get_session(session_id)
        finally:
            store.close()
        if data is None:
            raise ValueError(f"No saved session with id {session_id}.")

        pg = cls()
        pg.db_path = db_path
        stub = data["platform"] == "stub"
        pg.model, pg.mode = make_model(
            None if stub else (data["platform"] or None),
            None if stub else (data["model_name"] or None), stub=stub)
        pg.task_original = data["task_original"]
        pg.specified_task = data["specified_task"]
        pg.assistant_role = data["assistant_role"]
        pg.user_role = data["user_role"]
        pg.concise = data["concise"]
        pg.platform_name = data["platform"]
        pg.model_label = data["model_name"]
        task_text = pg.specified_task or pg.task_original
        if pg.concise:
            task_text = f"{task_text}\n\n{STYLE_RULES}"
        pg.session = RolePlaying(
            assistant_role_name=pg.assistant_role,
            user_role_name=pg.user_role,
            task_prompt=task_text,
            with_task_specify=False,
            assistant_agent_kwargs=dict(model=pg.model),
            user_agent_kwargs=dict(model=pg.model),
        )
        pg.narrator = ChatAgent(system_message=NARRATOR_SYSTEM, model=pg.model)
        pg.turn = data["turn"]
        pg.done = data["status"] == "done"
        pg.terminated = ""
        pg.transcript = list(data["messages"])
        pg.session_id = session_id
        pg.monitor = (ProgressMonitor(task_text=pg.specified_task or pg.task_original)
                      if ProgressMonitor is not None else None)
        pg.pending_msg = pg.session.init_chat()
        pg._replay(data["messages"])
        return pg

    def _replay(self, messages: list) -> None:
        """Feed a restored transcript back into the two agents' memories: each
        agent remembers its own lines as its own and the other side's lines as
        incoming, the same split CAMEL keeps while a session runs. Human
        instructions are replayed the way instruct() records them. The monitor
        is also re-fed the agent messages so its flag history is rebuilt."""
        last_assistant = None
        for who, text in messages:
            if who == self.assistant_role:
                msg = BaseMessage.make_assistant_message(role_name=who, content=text)
                self.session.assistant_agent.record_message(msg)
                self.session.user_agent.record_message(
                    BaseMessage.make_user_message(role_name=who, content=text))
                last_assistant = msg
            elif who == self.user_role:
                self.session.user_agent.record_message(
                    BaseMessage.make_assistant_message(role_name=who, content=text))
                self.session.assistant_agent.record_message(
                    BaseMessage.make_user_message(role_name=who, content=text))
            elif who == "Human":
                self.session.user_agent.record_message(
                    BaseMessage.make_assistant_message(
                        role_name=self.user_role,
                        content=f"Instruction: {text}\nInput: None"))
        if last_assistant is not None:
            self.pending_msg = last_assistant
        if self.monitor is not None:
            agent_msgs = [(w, t) for w, t in messages
                          if w in (self.assistant_role, self.user_role)]
            for i in range(0, len(agent_msgs), 2):
                self.monitor.observe(i // 2 + 1, agent_msgs[i:i + 2],
                                     self.assistant_role, self.user_role)

    # ----- reporting -----------------------------------------------------------
    def narrate(self, new_entries: list[tuple[str, str]]) -> str:
        """Ask the Narrator agent for its four-line plain-English summary of a
        burst (what just happened, whether it is going well, whether it is
        done, and a suggested nudge). The wording contract is NARRATOR_SYSTEM."""
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
        """The session's goal panel as Markdown: model, original task, the
        sharpened task when it differs, roles, turn count, and status. The
        session screen shows the compact status_of() strip instead; this is
        the fuller form, kept from Phase 1's Goal panel."""
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
    # Only two providers are actually wired up in this app (keys configured
    # and runs verified); the rest stay in the table for later but are not
    # offered in the UI, where picking one could only fail for lack of a key.
    ui_platforms = [p for p in platform_names if p in ("openai", "openrouter")]
    default_ui_platform = default_platform or os.getenv("MODEL_PLATFORM") or "openai"
    if default_ui_platform not in ui_platforms:
        default_ui_platform = ui_platforms[0]

    # One Playground per browser session (gr.State deep-copies the default for
    # each visitor), so people sharing a hosted deployment do not see or steer
    # each other's conversations. Every callback receives it and hands it back.
    def _chat(pg):
        """The whole transcript as chat messages, ready for the Chatbot."""
        return to_chat_messages(pg.transcript, pg.assistant_role)

    def status_of(pg):
        """One strip above the chat: goal, who is who, turn count, state."""
        if pg.session is None:
            return ""
        if pg.done:
            badge = "**Done**: the task was declared complete"
        elif pg.terminated:
            badge = f"**Stopped**: {pg.terminated}"
        elif pg.paused:
            badge = "**Paused** by you"
        else:
            badge = "In progress"
        goal = pg.specified_task or pg.task_original
        strip = (
            f"**Goal:** {goal}\n\n"
            f"**{pg.assistant_role}** does the work &nbsp;·&nbsp; "
            f"**{pg.user_role}** gives the instructions &nbsp;·&nbsp; "
            f"Turn {pg.turn} &nbsp;·&nbsp; {badge} &nbsp;·&nbsp; <small>{pg.mode}</small>"
        )
        if pg.specified_task and pg.specified_task != pg.task_original:
            strip += f"\n\n**You asked:** {pg.task_original}"
        phase2 = f"Turn limit: {pg.max_turns}"
        if pg.monitor is not None:
            phase2 += f" &nbsp;·&nbsp; {pg.monitor.summary_line()}"
        if pg.session_id is not None:
            phase2 += f" &nbsp;·&nbsp; Saved session #{pg.session_id}"
        strip += f"\n\n<small>{phase2}</small>"
        if pg.busy:
            strip += (f"\n\n**Working:** {pg.busy} "
                      "Free models can take a minute; the chat updates after each turn.")
        return strip

    # ----- the three cards: suggested next moves for the human -----------------
    def cards_of(pg):
        """Button updates for the three cards; hidden when there is nothing to choose."""
        if pg.session is None or pg.done or pg.terminated or len(pg.cards) < 3:
            return [gr.update(value="", visible=False)] * 3
        return [gr.update(value=c, visible=True) for c in pg.cards[:3]]

    def refresh_cards(pg):
        """Refill the three suggestion cards. A failure is not fatal: it is
        recorded as a System line and the cards stay hidden."""
        try:
            pg.suggest_next()
        except Exception as exc:
            pg.cards = []
            pg._record([("System", f"Could not suggest next moves: {friendly(exc)}")])

    def view(pg):
        """The tuple nearly every session-screen handler returns: the state,
        the status strip, the chat, and the three card updates, matching
        view_out below."""
        return (pg, status_of(pg), _chat(pg), *cards_of(pg))

    # The cards in their hidden state, for handlers whose output list
    # includes the card buttons but has nothing to offer yet.
    hidden_cards = [gr.update(value="", visible=False)] * 3

    def on_start(pg, task, a_role, u_role, platform, model_name, stub, specify, concise, max_turns,
                 agent_mode=False, progress=gr.Progress()):
        """Generator: switch screens at once so the tap visibly did something (setup can
        take a minute on free models), fill in the real state, then offer the first cards."""
        if not (task or "").strip():
            raise gr.Error("Type a task first.")
        progress(0, desc="Setting up the session...")
        pg.busy = "Setting up: choosing roles and sharpening the task."
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               "**Setting up:** choosing roles and sharpening the task. "
               "This can take up to a minute on free models.",
               [{"role": "user", "content": "**System**\n\nStarting..."}], a_role, u_role, *hidden_cards)
        try:
            if not ((a_role or "").strip() and (u_role or "").strip()):
                a_role, u_role = pg.suggest_roles(task, platform, model_name, stub)
            pg.start(task, a_role, u_role, platform, model_name, stub, specify,
                     concise=concise, max_turns=int(max_turns))
        except Exception as exc:
            # Back to the setup screen with the message, so the user can fix and retry.
            pg.busy = ""
            yield (pg, gr.update(visible=True), gr.update(visible=False), "", [], a_role, u_role, *hidden_cards)
            raise gr.Error(f"Could not start: {friendly(exc)}")
        pg._record([(
            "System",
            f"Session started. **{pg.assistant_role}** does the work. You give the instructions, "
            f"or let the AI playing **{pg.user_role}** do it for a turn or three.",
        )])
        pg.busy = ("Handing over to the AI User." if agent_mode
                   else "Thinking of your first suggested moves.")
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               status_of(pg), _chat(pg), a_role, u_role, *hidden_cards)
        if agent_mode:
            # Agent mode: no suggested moves, no waiting for the human. The same
            # burst the run buttons drive, for every remaining turn, adapted to
            # this handler's wider output set.
            pg.cards = []
            yield (pg, gr.update(visible=False), gr.update(visible=True),
                   status_of(pg), _chat(pg), a_role, u_role, *hidden_cards)
            if not (pg.done or pg.terminated):
                remaining = pg.max_turns - pg.turn
                if remaining > 0:
                    for pg_, st, ch, *cs in run(pg, remaining, progress):
                        yield (pg_, gr.update(visible=False), gr.update(visible=True),
                               st, ch, a_role, u_role, *cs)
            return
        refresh_cards(pg)
        pg.busy = ""
        progress(1, desc="Ready")
        yield (pg, gr.update(visible=False), gr.update(visible=True),
               status_of(pg), _chat(pg), a_role, u_role, *cards_of(pg))

    def run(pg, n, progress=gr.Progress()):
        """Autonomous burst: the AI User instructs. The status strip says what is
        happening throughout, the chat updates after every turn, the Narrator sums
        up the burst, then fresh cards are offered to the human."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if pg.done or pg.terminated:
            raise gr.Error("This conversation has finished. Press New session to start another.")
        if pg.paused:
            raise gr.Error("This run is paused. Press Resume first.")
        pg.cards = []                      # hide the cards while the agents work
        burst = []
        for i in range(n):
            pg.busy = f"The AI User is taking turn {pg.turn + 1}."
            progress(i / max(n, 1), desc=f"Working on turn {i + 1} of {n}...")
            yield view(pg)
            try:
                new = pg.run_turns(1)
            except Exception as exc:
                pg.busy = ""
                raise gr.Error(friendly(exc))
            if not new:
                break
            burst += new
            yield view(pg)
            if pg.done or pg.terminated:
                break
        if burst:
            pg.busy = "The Narrator is summing up."
            yield view(pg)
            try:
                pg._record([("Narrator", pg.narrate(burst))])
            except Exception as exc:
                pg._record([("System", f"Narrator unavailable: {friendly(exc)}")])
            yield view(pg)
        if not (pg.done or pg.terminated):
            pg.busy = "Thinking of suggested next moves."
            yield view(pg)
            refresh_cards(pg)
        pg.busy = ""
        progress(1, desc="Done")
        yield view(pg)

    def on_run_all(pg, progress=gr.Progress()):
        """Agent mode mid-session: every remaining turn up to the limit, no human input."""
        if pg.session is not None and not (pg.done or pg.terminated) \
                and pg.turn >= pg.max_turns:
            raise gr.Error("The turn limit has already been reached. "
                           "Press New session, or raise the limit there.")
        yield from run(pg, max(1, pg.max_turns - pg.turn), progress)

    def guided(pg, text, progress=gr.Progress()):
        """One human-led turn: show the instruction, get the assistant's answer, offer new cards."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if pg.done or pg.terminated:
            raise gr.Error("This conversation has finished. Press New session to start another.")
        pg.cards = []
        pg._record([("Human", text.strip())])
        pg.busy = f"{pg.assistant_role} is answering."
        progress(0, desc="The assistant is answering...")
        yield view(pg)
        try:
            pg.instruct(text, show_human=False)
        except Exception as exc:
            pg.busy = ""
            raise gr.Error(friendly(exc))
        yield view(pg)
        pg.busy = "Thinking of suggested next moves."
        yield view(pg)
        refresh_cards(pg)
        pg.busy = ""
        progress(1, desc="Done")
        yield view(pg)

    def make_card_handler(i):
        """Build the click handler for card i: a card is just a pre-written
        instruction, so clicking one runs the same guided turn as typing it."""
        def on_card(pg, progress=gr.Progress()):
            if pg.session is None or len(pg.cards) <= i:
                raise gr.Error("Start a task first.")
            yield from guided(pg, pg.cards[i], progress)
        return on_card

    def on_send(pg, text, how, progress=gr.Progress()):
        """The message box's Send button (and Enter in the box). Three paths:
        while a run is busy, or with an empty box, it warns gently and changes
        nothing (a red error here punished what is usually a stray tap on a
        phone); 'Note for both agents' queues guidance for the next turn;
        anything else runs one guided turn and clears the box."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        if pg.busy:
            gr.Warning("The agents are still working. Wait for this step to finish, then send.")
            yield (pg, status_of(pg), _chat(pg), text, *cards_of(pg))
            return
        if not (text or "").strip():
            gr.Warning("Type a message first, or use the turn buttons below.")
            yield (pg, status_of(pg), _chat(pg), text, *cards_of(pg))
            return
        if (how or "").startswith("Note"):
            try:
                pg.guide(text)
            except Exception as exc:
                raise gr.Error(str(exc))
            pg._record([("System", "Noted. Both agents will see this before the next turn.")])
            yield (pg, status_of(pg), _chat(pg), "", *cards_of(pg))
            return
        for pg_, st, ch, *cs in guided(pg, text, progress):
            yield (pg_, st, ch, "", *cs)

    def on_reset():
        """Fresh Playground and fresh fields: task and both roles are cleared so the
        model chooses roles for the next task instead of reusing the last ones."""
        return (Playground(), gr.update(visible=True), gr.update(visible=False), "", [],
                "", "", "", *hidden_cards)

    def on_new_session():
        """The sidebar's New session button: the same fresh start as the session
        screen's own button, plus clearing the sidebar selection. It also closes
        the sidebar and leaves a toast: on a phone the sidebar is a drawer lying
        over the page, so without those two signals the click looked like a
        no-op even though the setup screen had already changed behind it."""
        gr.Info("New session started. The setup screen is ready for a new task.")
        # The list update must carry the choices as well as the cleared
        # selection: a bare value update made the frontend drop the choices,
        # and the saved list rendered empty until the app was restarted.
        return (*on_reset(), gr.update(choices=saved_choices(), value=None),
                gr.update(open=False))

    # ----- Phase 2 handlers: preflight, saved sessions, pause/resume -----------
    db_path = str(Path(os.getenv("PLAYGROUND_DB", ROOT / "data" / "sessions.db")))

    def saved_choices():
        """The sidebar list as (label, session id) pairs, newest first. The
        label is what a person scans: id, date, the task as typed, the turn
        count, and the status; the id is the value the handlers receive."""
        if SessionStore is None:
            return []
        store = SessionStore(db_path)
        try:
            rows = store.list_sessions()
        finally:
            store.close()
        return [(f"#{r['session_id']} · {r['created_at'][:16].replace('T', ' ')} · "
                 f"{r['task_original'][:45]} · turn {r['turn']} · {r['status']}",
                 r["session_id"]) for r in rows]

    def on_check(platform, model_name, stub):
        """The preflight button: validate the setup screen's provider, model,
        and key (and that the model object constructs) before anyone presses
        Start, and render the checklist (Enhancement 3)."""
        if run_preflight is None:
            return "Model setup checks are unavailable in this copy of the app."
        result = run_preflight(
            PLATFORMS, platform, model_name, stub,
            create=lambda p, m: ModelFactory.create(model_platform=p, model_type=m))
        return result.render()

    def on_refresh():
        """Reload the sidebar list from the store. Chained after every event
        that can change a session, so turn counts and statuses stay current."""
        return gr.update(choices=saved_choices())

    def on_open_selected(pg, session_id):
        """The sidebar's Open selected button. Selecting an entry in the list
        only marks it now; opening is this separate step, so a session can be
        selected for deletion without being opened first."""
        if session_id is None:
            gr.Warning("Pick a saved session in the list first, then press Open.")
            yield (pg, gr.update(), gr.update(), status_of(pg), _chat(pg),
                   gr.update(), gr.update(), *cards_of(pg), gr.update())
            return
        yield from on_reopen(pg, session_id)

    def on_reopen(pg, session_id):
        if session_id is None:
            raise gr.Error("Pick a saved session first.")
        try:
            pg2 = Playground.reopen(int(session_id), db_path)
        except Exception as exc:
            raise gr.Error(f"Could not reopen: {friendly(exc)}")
        # Shown in the chat but not written into the saved history: it is a note
        # about this viewing, not part of the conversation, and keeping it out of
        # the store means reopening the same session twice can never duplicate it.
        pg2.transcript.append(("System", f"Session #{int(session_id)} reopened from its "
                                         "saved history. The task, roles, transcript, "
                                         "and settings were restored."))
        yield (pg2, gr.update(visible=False), gr.update(visible=True),
               status_of(pg2), _chat(pg2), pg2.assistant_role, pg2.user_role,
               *hidden_cards, gr.update(open=False))
        if not (pg2.done or pg2.terminated):
            refresh_cards(pg2)
            yield (pg2, gr.update(visible=False), gr.update(visible=True),
                   status_of(pg2), _chat(pg2), pg2.assistant_role, pg2.user_role,
                   *cards_of(pg2), gr.update(open=False))

    def on_delete(pg, session_id):
        """Delete the sidebar's selected session from the store for good. If the
        deleted session is the one on screen, the app goes back to a fresh setup;
        otherwise the current view is left exactly as it was."""
        keep = (pg, gr.update(), gr.update(), status_of(pg), _chat(pg),
                gr.update(), gr.update(), gr.update(), *cards_of(pg))
        if session_id is None:
            gr.Warning("Pick a saved session in the list first, then press Delete.")
            return (*keep, gr.update(), gr.update())
        sid = int(session_id)
        if SessionStore is not None:
            store = SessionStore(db_path)
            try:
                store.delete_session(sid)
            finally:
                store.close()
        gr.Info(f"Session #{sid} deleted.")
        refreshed = gr.update(choices=saved_choices(), value=None)
        if pg.session_id == sid:
            return (*on_reset(), refreshed, gr.update(open=False))
        # The view did not change, so leave the panel open: the user can see
        # the entry is gone and act on another session without reopening it.
        return (*keep, refreshed, gr.update())

    def on_pause(pg):
        """Pause button: autonomous runs stop after the current turn."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        pg.set_paused(True)
        return view(pg)

    def on_resume(pg):
        """Resume button: clears the pause so the run buttons work again."""
        if pg.session is None:
            raise gr.Error("Start a task first.")
        pg.set_paused(False)
        return view(pg)

    with gr.Blocks(title="CAMEL Playground") as ui:
        state = gr.State(Playground())
        gr.Markdown(
            "# CAMEL Playground\n"
            "Give two AI agents a task. Steer them yourself, or let them run."
        )

        # Saved sessions live in a sidebar, the way chat apps list past
        # conversations: always visible. Selecting an entry only marks it;
        # Open and Delete are separate buttons, so selecting a session to
        # delete it never opens it first.
        with gr.Sidebar(open=True) as side_bar:
            new_btn = gr.Button("New session", variant="primary")
            gr.Markdown(
                "### Saved sessions\n"
                "Tap a session to select it, then open or delete it."
            )
            side_sessions = gr.Radio(choices=[], label="Saved sessions",
                                     show_label=False)
            with gr.Row():
                open_btn = gr.Button("Open selected", size="sm")
                delete_btn = gr.Button("Delete selected", size="sm")
            side_refresh = gr.Button("Refresh list", size="sm")
            gr.Markdown(
                "<small>Hosted list clears when the service restarts. "
                "Run locally to keep sessions for good.</small>"
            )

        # Stage 1: one box and one button. Everything else is behind "Advanced".
        with gr.Group(visible=True) as setup:
            gr.Markdown("## Start a new session")
            task = gr.Textbox(
                label="What should the agents work on?",
                placeholder=("Plan a weekend in Montreal on a student budget, write a bedtime "
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
                    platform = gr.Dropdown(ui_platforms,
                                           value=default_ui_platform,
                                           label="Model provider")
                    model_name = gr.Textbox(label="Model (blank = provider default)", value=default_model or "")
                with gr.Row():
                    concise = gr.Checkbox(label="Concise replies (about 80 words, code only on request)", value=True)
                    specify = gr.Checkbox(label="Sharpen the task first (task-specifier agent)", value=True)
                    stub = gr.Checkbox(label="Offline test mode (no key, canned replies)", value=default_stub)
                max_turns = gr.Slider(4, 40, value=12, step=1,
                                      label="Turn limit for autonomous runs")
                agent_mode = gr.Checkbox(
                    label="Agent mode: run all turns with no human input, up to the turn limit",
                    value=False)
            start_btn = gr.Button("Start", variant="primary", size="lg")
            with gr.Accordion("Phase 2: model check", open=False):
                check_btn = gr.Button("Check model setup (preflight)")
                preflight_out = gr.Markdown("")

        # Stage 2: status strip, the conversation (Narrator included), three cards with
        # suggested next moves, one box to type your own, and the autonomous options.
        with gr.Group(visible=False) as session:
            status = gr.Markdown("")
            chat = gr.Chatbot(show_label=False, height=520,
                              placeholder="The conversation will appear here.")
            # The default flow is the long run: one big primary button under
            # the chat. Steering by hand (cards, the message box) sits below
            # it as the alternative, and Send is deliberately not primary.
            autoall_btn = gr.Button("▶ AI User: run all turns", variant="primary", size="lg")
            # Steering by hand starts minimized: the cards and the message
            # box live in a closed accordion under the run button, so the
            # first thing a user sees is the long run, not Send.
            with gr.Accordion("Or steer it yourself: suggestions and your own instruction",
                              open=False):
                with gr.Row():
                    card_btns = [gr.Button("", visible=False, size="sm") for _ in range(3)]
                with gr.Row():
                    text = gr.Textbox(show_label=False, scale=4,
                                      placeholder="Type your own instruction or question for the assistant...")
                    how = gr.Radio(["To the assistant", "Note for both agents"], value="To the assistant",
                                   show_label=False, scale=2)
                    send_btn = gr.Button("Send", scale=1)
            with gr.Row():
                pause_btn = gr.Button("Pause", size="sm")
                resume_btn = gr.Button("Resume", size="sm")
                reset_btn = gr.Button("New session", size="sm")

        # ----- event wiring ---------------------------------------------------
        # Each button is bound to its handler with the input and output
        # component lists it needs; api_name gives the endpoint a stable name
        # in the API. Handlers are generators, so the screen updates as work
        # happens rather than only at the end. Every event that can change a
        # session (start, a card, a run, a send, a reset) chains
        # .then(on_refresh) so the sidebar list never shows stale turn counts.
        c1, c2, c3 = card_btns
        view_out = [state, status, chat, c1, c2, c3]
        start_inputs = [state, task, a_role, u_role, platform, model_name, stub, specify, concise, max_turns, agent_mode]
        start_outputs = [state, setup, session, status, chat, a_role, u_role, c1, c2, c3]
        start_event = start_btn.click(on_start, start_inputs, start_outputs, api_name="start")
        task_submit_event = task.submit(on_start, start_inputs, start_outputs)   # Enter in the task box also starts
        start_event.then(on_refresh, None, [side_sessions])
        task_submit_event.then(on_refresh, None, [side_sessions])
        for i, btn in enumerate(card_btns):
            btn.click(make_card_handler(i), [state], view_out, api_name=f"card{i + 1}").then(
                on_refresh, None, [side_sessions])
        autoall_btn.click(on_run_all, [state], view_out, api_name="runall").then(
            on_refresh, None, [side_sessions])
        send_btn.click(on_send, [state, text, how], [state, status, chat, text, c1, c2, c3], api_name="send").then(
            on_refresh, None, [side_sessions])
        text.submit(on_send, [state, text, how], [state, status, chat, text, c1, c2, c3]).then(
            on_refresh, None, [side_sessions])
        reset_event = reset_btn.click(on_reset, None,
                        [state, setup, session, status, chat, task, a_role, u_role, c1, c2, c3],
                        api_name="reset")
        reset_event.then(on_refresh, None, [side_sessions])
        new_event = new_btn.click(on_new_session, None,
                        [state, setup, session, status, chat, task, a_role, u_role, c1, c2, c3, side_sessions, side_bar],
                        api_name="new_session")
        new_event.then(on_refresh, None, [side_sessions])
        pause_btn.click(on_pause, [state], view_out, api_name="pause")
        resume_btn.click(on_resume, [state], view_out, api_name="resume")
        check_btn.click(on_check, [platform, model_name, stub], [preflight_out],
                        api_name="preflight")
        side_refresh.click(on_refresh, None, [side_sessions], api_name="saved_list")
        delete_btn.click(on_delete, [state, side_sessions],
                         [state, setup, session, status, chat, task, a_role, u_role,
                          c1, c2, c3, side_sessions, side_bar],
                         api_name="delete_session")
        open_btn.click(on_open_selected, [state, side_sessions], start_outputs + [side_bar],
                       api_name="reopen")
        ui.load(on_refresh, None, [side_sessions])
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
    # Theme and CSS go to launch(), not Blocks: in Gradio 6 the Blocks
    # arguments are silently ignored. The CSS hides Gradio's footer and
    # centers the page in a readable column.
    ui.launch(server_name=host, server_port=port, auth=auth,
              theme=gr.themes.Soft(primary_hue="amber", neutral_hue="slate"),
              css=("footer {display: none !important}\n"
                   ".gradio-container {max-width: 1040px !important; "
                   "margin: 0 auto !important}"),
              inbrowser=not (args.no_browser or hosted), show_error=True)


if __name__ == "__main__":
    main()
