# Setup notes – SENG 691 Group 3 – CAMEL Phase 1

Chronological record of the Phase 1 environment setup performed on
2026-10-24 on a Linux (x86_64) machine. Everything below was actually run;
outputs are in `docs/demo_output/`.

## 1. Machine state before setup

```
$ python3 --version
Python 3.13.17
$ ls /usr/bin/python3.*          # 3.11, 3.12, 3.13 present; 3.10 absent
$ uv --version
uv 0.11.32
$ echo "KEY SET? ${OPENAI_API_KEY:+yes}${OPENAI_API_KEY:-no}"
KEY SET? no
```

Decision: CAMEL's `pyproject.toml` says `requires-python = ">=3.10,<3.15"`,
so 3.13 is *in range*, but every workflow in `camel/.github/workflows/` pins
`python-version: "3.10"`. We chose 3.10 to match the tested configuration.

## 2. Commands run, in order

```bash
cd ~/camel
git clone --depth 1 https://github.com/camel-ai/camel.git camel
#   -> upstream HEAD 797296a718ce856d9c9476e4bb69fedb71ab6382, version 0.2.91a7

uv python install 3.10
#   -> Installed Python 3.10.20 (cpython-3.10.20-linux-x86_64-gnu)

uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -e ./camel
#   -> editable install from source; resolved ~70 packages (openai 2.54.0,
#      pydantic, tiktoken 0.12.0, colorama, python-dotenv, httpx, ...)

.venv/bin/python -c "import camel, sys; print(camel.__version__, sys.version)"
#   -> 0.2.91a7 3.10.20

git submodule add https://github.com/camel-ai/camel.git camel
#   -> "Adding existing repo at 'camel' to the index"; creates .gitmodules
```

Install route used: **source (editable)**. The PyPI fallback
(`uv pip install camel-ai`) was not needed.

## 3. Demo runs

```bash
# (a) unmodified upstream example, no key  -> docs/demo_output/01_*.txt
cd camel && ../.venv/bin/python examples/ai_society/role_playing.py
#   ValueError: Missing or empty required API keys in environment variables:
#   OPENAI_API_KEY.   (exit code 1)

# (b) environment check               -> docs/demo_output/02_*.txt
.venv/bin/python demo/check_env.py

# (c) our demo, no key                -> docs/demo_output/03_*.txt
.venv/bin/python demo/role_playing_demo.py --turns 2     # exit 1, clear message

# (d) our demo, offline stub model    -> docs/demo_output/04_*.txt
.venv/bin/python demo/role_playing_demo.py --stub --turns 3   # exit 0
```

Run (d) proves the agent machinery works end to end (task specifier agent,
assistant agent, user agent, three full turns, termination checks). The
replies are the stub's canned "Lorem Ipsum"; this is **not** an LLM run.

A real LLM run requires a team member to create `.env` from `.env.example`
with their own `OPENAI_API_KEY`, then run (c) again. Expect each turn to make
2 API calls plus 1 for task specification; `--turns 5` keeps cost small.

## 4. Errors hit and how each was fixed

| # | Error | Cause | Fix |
|---|---|---|---|
| 1 | No `python3.10` binary | Machine shipped 3.11–3.13 only | `uv python install 3.10` (downloads a managed CPython build) |
| 2 | `ValueError: Missing or empty required API keys ... OPENAI_API_KEY` | No key on the machine | Cannot be fixed without a key. Added `.env.example`, early exit with instructions in `demo/role_playing_demo.py`, and `--stub` offline mode |
| 3 | `git status` showed `camel/` as an untracked embedded repo | Nested `git clone` inside our repo | `git submodule add` on the existing clone, pinned to commit `797296a` |
| 4 | `warning: The UV_NATIVE_TLS environment variable is deprecated` | Machine-level env var for uv | Harmless; no action |

No error required changing upstream CAMEL files.

## 5. Design decisions

* **Submodule, not vendoring.** Keeps our repo small and makes "which CAMEL
  commit did we test?" unambiguous (`git -C camel rev-parse HEAD`). Team
  members clone with `--recurse-submodules` or run `setup.sh`.
* **Editable install (`-e`).** `import camel` resolves to `camel/camel/` in the
  submodule, so reading the code and running it are the same files.
* **Own demo script instead of editing the upstream example.** Upstream code
  stays pristine; our changes (dotenv, `--turns`, `--stub`, plain printing)
  are listed in the script's docstring.
* **Stub model for offline verification.** `ModelPlatformType.STUB` /
  `ModelType.STUB` are upstream's own unit-test backend
  (`camel/camel/models/stub_model.py`).

## 6. Files deliberately excluded from the clean public copy (`camel-public/`)

| Excluded | Why |
|---|---|
| `.venv/` | 200+ MB of installed packages; recreated by `setup.sh` |
| `.env` | would hold a real key (none existed on this machine, but the rule stands) |
| `camel-public/` | the copy itself, nested inside the private folder |
| `.git/` history of the private working repo | public repo starts with a clean history |
| `__pycache__/`, `*.pyc` | build caches |
| Untracked files inside `camel/` | none existed; submodule tree was clean |

Kept: `README.md`, `setup.sh`, `.env.example`, `.gitignore`, `.gitmodules`,
`camel/` (submodule reference), `demo/`, `docs/` (these notes and the captured
demo logs, which are the Phase 1 evidence; delete `docs/demo_output/` if the
team prefers a code-only repo).

## 7. Second machine: Windows 11 reproduction and the real LLM run (2026-10-24)

Fresh clone of this repo at commit `fae3b32` on Windows 11 (PowerShell 7,
uv already installed, system Python 3.11.9, no 3.10). An `OPENAI_API_KEY`
was already present as a user environment variable on this machine; no
`.env` file was created and the key was never printed or copied.

```powershell
git submodule update --init --depth 1 camel
#   -> checked out 797296a718ce856d9c9476e4bb69fedb71ab6382
uv python install 3.10
#   -> Installed Python 3.10.19 (cpython-3.10.19-windows-x86_64-none)
uv venv --python 3.10 .venv
uv pip install --python .venv\Scripts\python.exe -e .\camel
#   -> editable source install, same dependency set as on Linux

.venv\Scripts\python.exe demo\check_env.py              # -> docs/demo_output/05_*.txt
.venv\Scripts\python.exe demo\role_playing_demo.py --stub --turns 3   # -> 06_*.txt
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe demo\role_playing_demo.py --turns 5 > docs\demo_output\07_demo_real_llm_5_turns.txt 2>&1
#   -> exit 0, 5 full turns, model gpt-4.1-mini-2025-04-14
```

Result of the real run (log 07): task specifier produced a concrete spec;
AI User issued five instructions (install libraries; Twitter streaming module
with Tweepy; VADER sentiment function; integrate sentiment into the stream
callback; stop-loss risk-management module); AI Assistant answered each with
code. API usage: 1 call for task specification + 2 per turn = 11 calls.

Differences from the Linux setup and how they were handled:

| # | Issue | Fix |
|---|---|---|
| 5 | `setup.sh` hard-codes `.venv/bin/python`; Windows venvs use `.venv\Scripts\python.exe` | Added `setup.ps1` with the same four steps |
| 6 | uv's current 3.10 build is 3.10.19 on Windows vs 3.10.20 on Linux | Patch-level difference, no action |
| 7 | Redirecting Python output to a file on Windows may use cp1252 and crash on box-drawing characters in model replies | `$env:PYTHONUTF8 = '1'` before the run |
| 8 | `colorama` ANSI colour codes end up in logs when stdout is redirected on Windows (not on Linux) | Stripped from logs 06 and 07 after capture with a regex (`\x1b\[[0-9;]*[A-Za-z]`); content unchanged |
| 9 | First attempt piped the run through `Select-Object -First 40`, which closes the pipeline and killed the demo part-way through turn 1 | Re-ran with plain `>` redirection; partial log discarded |

No upstream CAMEL file was modified.

## 8. Notebook version of the demo (2026-10-24)

`demo/role_playing_demo.ipynb` mirrors `role_playing_demo.py` cell by cell
(settings, model selection, `RolePlaying` construction, system messages,
turn loop) and renders each turn as Markdown. Jupyter is not a CAMEL
dependency, so it was installed separately into the venv:

```powershell
uv pip install --python .venv\Scripts\python.exe notebook nbconvert ipykernel
# verify offline first (copy with STUB = True, TURNS = 2): 12 outputs, 0 errors
# then the real run, saved in place:
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m nbconvert --to notebook --execute --inplace demo\role_playing_demo.ipynb
#   -> 5 turns, gpt-4.1-mini-2025-04-14, 0 errors; checked that no key text is in the saved outputs
```

The notebook locates the repo root by looking for `.env.example` in the
current directory or its parent, so it works whether Jupyter is started from
the repo root or from `demo/`.

### 8.1 Google Colab fix

First attempt to run the notebook on Google Colab failed in the imports cell
with `ModuleNotFoundError: No module named 'camel'` (Colab has no access to
our `.venv`). Fix: a setup cell that installs `camel-ai==0.2.91a7` from PyPI
when `importlib.util.find_spec("camel")` finds nothing, and reads the key
from Colab's `userdata` secret or a hidden `getpass` prompt when `.env` and
the environment have none.

| # | Issue | Fix |
|---|---|---|
| 10 | Colab kernel has no CAMEL | Setup cell runs `pip install camel-ai==0.2.91a7` (exact PyPI match for submodule commit `797296a`) |
| 11 | From the repo root, a bare `find_spec("camel")` returns an empty *namespace package* for the `camel/` submodule folder even when CAMEL is not installed, so the check would wrongly skip the install | Treat `spec.origin is None` as "not installed" |
| 12 | Colab has no `.env` | Read `google.colab.userdata.get("OPENAI_API_KEY")`, else `getpass`; the key is never echoed |

Verified by simulating Colab locally: a fresh Python 3.10 venv with only
`notebook`, `nbconvert`, `ipykernel` and `pip` (uv venvs ship without pip,
which Colab has), run from a directory outside the repo, in stub mode. The
setup cell reported "CAMEL not found ... installing", installed 0.2.91a7,
and the dialogue ran 2 stub turns with 0 errors. The already-installed path
was re-verified in the project venv.

### 8.2 Making the output readable

Teammate feedback after the first Colab run: "a wall of text". Restructured
the notebook: a "what you will see" table, a guide cell before each stage,
the two system messages collapsed behind `<details>` blocks, each turn headed
by the AI User's instruction in one line, and a summary table at the end.
Re-executed with the real model (5 turns, 0 errors) and committed with
outputs.

## 9. Letting each team member pick their own model provider (2026-10-24)

Question from the team: can we use a free model instead of OpenAI? Yes.
`ModelFactory.create` in the pinned CAMEL accepts plain strings for both the
platform (`"groq"`, `"gemini"`, `"ollama"`, ... see
`camel/camel/types/enums.py`, `ModelPlatformType`) and the model name, and
each backend checks its own key variable (`GROQ_API_KEY`, `GEMINI_API_KEY`,
...). Groq and Gemini go through CAMEL's `OpenAICompatibleModel`, so no
extra packages are needed.

Change: `--platform` / `--model` flags in `demo/role_playing_demo.py`,
`PLATFORM` / `MODEL` in the notebook settings cell, and `MODEL_PLATFORM` /
`MODEL_NAME` read from `.env`, so a teammate only edits their private `.env`.
A small `PLATFORMS` table (platform -> key variable, default model) gives an
early, clear error when the key is missing; unknown platform names fall
through to CAMEL's own error. `--list-platforms` prints every name CAMEL
knows. `.env.example` lists the key variable and sign-up URL per platform.

Verified: stub run; `--platform groq` with no key exits 1 with a clear
message; `--platform nosuch` exits 1 naming the unknown platform; default
OpenAI path re-run for 1 real turn. Not verified: an actual Groq / Gemini /
Ollama dialogue, because no such key or local server was available. The
first teammate with a free key should run `--platform groq --turns 5` and
drop the log into `docs/demo_output/`.

### 9.1 Interactive prompts in the notebook

Request from the team: ask for the model and key instead of requiring the
variables to be filled in first. The notebook's imports cell now resolves
each of platform, model and key in the order: settings cell, then `.env` /
environment / Colab secret of the same name, then a prompt (`input()` menu
for the platform, `input()` for the model only when the platform has no
default, `getpass` for the key so it is never echoed or saved). Stub mode
skips all prompts, which keeps `nbconvert --execute` usable for verification.

Tested without a kernel by exec'ing the two cells with scripted answers:
"2" selects groq and asks for `GROQ_API_KEY`; a blank answer defaults to
openai; "nosuch" is rejected and "gemini" then accepted by name; "mistral"
(a CAMEL platform with no default here) asks for a model name and then fails
because CAMEL's Mistral backend imports the `mistralai` SDK, which is not a
`camel-ai` dependency. Added a clear ImportError message naming the missing
package in both the notebook and the script (issue 13).

## 10. Playground UI (2026-10-24)

Team feedback: the notebook is code heavy; we want to give any vague prompt,
watch the agents in small bursts, understand what the code they write is for,
know the goal and whether it was reached, and be able to ask follow-ups or
give feedback between bursts.

Built `demo/playground.py`, a Gradio app over the unmodified CAMEL classes:

* `Playground` class drives one `RolePlaying` session in bursts
  (`run_turns(n)`), tracks turn count, `CAMEL_TASK_DONE`, and termination.
* Narrator: a separate `ChatAgent` with a fixed plain-English report format
  (goal, what just happened, how it is going, done?, suggested nudge),
  called once per burst on the new exchanges only.
* Human guidance: appended to the pending message before `session.step`, so
  the user agent reads it in context and the assistant sees it echoed.
* Direct question: `session.assistant_agent.step(...)`; the reply becomes the
  pending message so the user agent reacts to it next.
* Role suggestion: a `ChatAgent` returning two labelled lines, parsed.
* UI: Gradio Blocks (task, roles, provider, stub/specify toggles; goal panel,
  narrator panel, chat; turns-per-burst slider; guidance and question boxes).
  All callbacks catch exceptions and show them in an error line.

Dependency: `gradio` (6.30.0 at the time), installed separately into
`.venv` and by the notebook setup cell when missing (Colab). Gradio 6 has no
`type=` argument on `Chatbot` (messages format is the only one), which the
first draft used; removed.

Colab gap found by the stub test: a notebook executed outside the repo has no
`demo/playground.py` beside it, which is exactly the Colab situation (only
the .ipynb is uploaded; the repo is private so a raw download needs auth).
Fix: the Playground cell looks for the module next to the repo, and on Colab
falls back to `google.colab.files.upload()` so the user picks
`demo/playground.py` from their disk; `playground.py` carries its own copy of
the platform table so it works when uploaded alone (issue 14).

Verified with a headless harness (`Playground` driven directly, then
`build_ui().launch()` on a port and an HTTP GET of the page): stub mode
end to end; real mode (OpenAI, 2 turns + guidance + question + role
suggestion) once. The notebook's stub execution is run from a copy placed in
`demo/` so the module lookup matches a real checkout. Not verified: clicking through the UI in a browser on
Colab; the first teammate to do so should note any layout issues here.

## 12. Playground UI simplified (2026-10-24)

Team feedback: too much on one screen, not intuitive. Redesign principles:
progressive disclosure and one primary action per screen.

* Screen 1: one task box and a Start button. Roles are chosen by a model
  when left blank (the separate "Suggest roles" button is gone). Roles,
  provider, model, offline mode and the task-specifier toggle sit in a
  collapsed *Advanced* fold.
* Screen 2 (appears after Start; screen 1 hides): a status strip (goal, who
  does what, turn, done/in progress, model), the chat, **Next turn** as the
  one primary button plus **Run 3 turns** and **New task**, and a single
  step-in box with a two-way radio (guide both agents / ask the assistant)
  and Enter-to-send.
* The Narrator posts into the chat after each burst instead of a separate
  panel, so the story reads top to bottom; the turn slider, goal panel,
  narrator panel and the two separate input boxes are removed.
* Turns stream: the run callback is a generator that yields after every turn,
  so a 3-turn burst shows each exchange as it lands.
* Errors use Gradio's toast (`gr.Error`) instead of a Markdown error line.
* Soft theme via `launch(theme=...)` (Gradio 6 takes `theme` on `launch`,
  not on `Blocks`).

Verified with the HTTP harness (3.11 venv, Render-style start, login): start
with blank roles, next turn, guidance, run 3 (two Narrator entries), ask,
reset, second visitor refused. Note for future tests: `gradio_client` omits
layout components (Groups) from returned values and returns chat message
content as a list of blocks.

## 13. Notebook aligned with the Playground (2026-10-24)

The notebook had drifted: it opened with a code-heavy walkthrough, carried
its own copy of the provider table and the interactive model/key prompts,
and the Playground sat at the bottom. Rebuilt it from a generator script:

1. settings (STUB, PLATFORM, MODEL; walkthrough task, roles, TURNS),
2. setup (installs `camel-ai` and `gradio` if missing, finds or uploads
   `demo/playground.py`, imports),
3. **Quick start**: the Playground cell,
4. **Walkthrough**: the same `Playground` class step by step, with the
   Narrator at the end, so the notebook and the app share one engine.

Shared code moved into `demo/playground.py`: `choose_model()` (precedence
settings > `.env` / Colab secret > console menu and hidden key prompt >
default) and `Playground.start(..., model=, mode=)` to accept a prebuilt
model. The notebook no longer defines the provider table or prompts itself.
Verified: stub execution from `demo/`, then a real 5-turn execution saved in
place (launch cell output cleared afterwards).

## 14. Playground feedback round two (2026-10-24)

Three items from testing on a phone against the Render deployment.

* **"Start does nothing on my phone."** Start did run, but gave no feedback:
  the conversation screen stayed hidden until the two setup calls (role
  suggestion, task specifier) finished, which on a free OpenRouter model can
  take 30-60 s. `on_start` is now a generator: it switches screens at once
  with a "Setting up..." strip and a Starting... message, then fills in the
  real state; on failure it switches back and shows the error toast. Enter in
  the task box also starts (`task.submit`).
* **"Responses are too verbose."** CAMEL's assistant template asks for
  "detailed implementations", so replies ran to 1,400-2,300 characters.
  Added `STYLE_RULES` (at most 80 words, plain language, code only on
  request, no repetition) appended to the task text *after* the task
  specifier has run, so the specifier cannot rewrite it away. To do that the
  Playground now runs `TaskSpecifyAgent` itself and builds `RolePlaying` with
  `with_task_specify=False`; `pg.specified_task` holds the clean sharpened
  task for display. A "Concise replies" box under Advanced turns it off.
  The Narrator prompt shrank to four one-sentence lines. Measured on the
  real 5-turn notebook run: assistant replies 53-73 words, user turns 28-42
  words (a first attempt with "under 120 words" still produced ~160).
* **"New task should clear the role fields."** `on_reset` now clears the
  task and both role boxes along with the session, so the model picks fresh
  roles for the next task.

Also: the model is passed per agent (`assistant_agent_kwargs` /
`user_agent_kwargs`) instead of globally, which removes CAMEL's "model
provided globally" warning, and the notebook setup cell silences library
warnings so cell outputs stay clean.

## 15. Guided next-move cards (2026-10-24)

Request: three guided "question cards" that push the human in the right
direction. On a single turn the human picks a card (or types their own); on
multi-turn runs the instruction-giving agent drives as before.

Design, all in `demo/playground.py`, upstream untouched:

* `GUIDE_SYSTEM` + `Playground.suggest_next()`: a coaching `ChatAgent` gets
  the goal, the roles and the last four transcript entries and returns three
  short next moves (<= 14 words, different directions: deepen, broaden, check,
  decide, finish). Parsed line by line; padded with generic fallbacks if the
  model returns fewer (the stub returns one line, so this path is exercised).
* `Playground.instruct(text)`: the human takes the user seat for one turn.
  The text is wrapped in CAMEL's `Instruction: ... / Input: None` format,
  recorded into the user agent's memory with `ChatAgent.record_message` so a
  later autonomous turn sees a coherent history, then sent to the assistant
  agent; its reply becomes the pending message for the next `RolePlaying.step`.
* UI: three `gr.Button` cards whose labels are updated from `pg.cards`
  (hidden while the agents work, after `done`, and on reset); one text box
  (radio: *To the assistant* = same path as a card, *Note for both agents* =
  the old guidance); *Let the AI User take one turn / run 3 turns* keep the
  pure CAMEL loop with the Narrator; fresh cards after every turn. All
  callbacks are generators so the human's line shows before the model call.
* Cost: one extra model call per card refresh.

Verified with the HTTP harness in stub mode: card 1 produces a human-led turn
(so the cards existed server-side); typed instruction; note; one and three
autonomous turns with Narrator posts; reset clears fields; second visitor
isolated. `gradio_client` omits Button components from returned values, so
the card labels themselves cannot be read through it; they are plain
`gr.update(value=..., visible=True)` calls. A real-model run (laptop task)
gave three distinct cards (list options under $1,200 / warranties / student
discounts), a 93-word answer to the picked card, three new cards, and an
autonomous AI User turn that referred back to "these laptop models", which
confirms the memory recording keeps the agents coherent.

## 16. Free-tier limits hit on OpenRouter; stale provider defaults (2026-10-24)

The hosted Playground errored after a short session. Cause: OpenRouter's
`:free` models allow 50 requests/day for accounts that have never bought $10
of credit (20 RPM; 1,000/day after a $10 purchase). The Playground makes 2-8
calls per click (see README), so one session exhausts 50.

While checking the providers' pages:

| # | Finding | Action |
|---|---|---|
| 15 | Groq deprecated `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` (shutdown 2026-08-16); replacements `openai/gpt-oss-120b` / `openai/gpt-oss-20b` | Groq default changed to `openai/gpt-oss-120b` in both PLATFORMS tables, `.env.example`, README |
| 16 | Cerebras lists only `gpt-oss-120b` and `qwen-3.8-27b`; "Is there a permanently free tier? No." ($5 trial, 30 days) | Default changed to `gpt-oss-120b`; README no longer calls it a free tier |
| 17 | Gemini pricing page marks 2.5 Flash / Flash-Lite "free of charge" but per-model RPM/RPD only appear in AI Studio after login | Kept `gemini-2.5-flash`; documented the caveat |
| 18 | Raw 429 text from the provider surfaced in the UI | `friendly()` maps 429/rate-limit, 401, 404/model-not-found and timeouts to one line of advice; used in start, turns, cards and Narrator |

Not verified: an actual Groq or Gemini run (no keys here). Groq's Free Plan
tab could not be separated from the Developer Plan table when fetched.

## 11. Hosting the Playground on Render with auto-deploy (2026-10-24)

Request: a hosted version so nobody configures anything, updated whenever
`main` changes. Added:

* `render.yaml` (Blueprint): free Python web service, `branch: main`,
  `autoDeploy: true`, build `pip install -r requirements-render.txt`, start
  `python demo/playground.py --no-browser`, health check `/`. Env vars:
  `PYTHON_VERSION=3.11.9`, `MODEL_PLATFORM=openai`, `OPENAI_API_KEY`
  (`sync: false`, entered in the dashboard), `PLAYGROUND_USER=group3`,
  `PLAYGROUND_PASSWORD` (`generateValue: true`), `GRADIO_ANALYTICS_ENABLED=False`.
* `requirements-render.txt`: `camel-ai==0.2.91a7` from PyPI (no submodule on
  Render), `gradio==6.30.0`, `python-dotenv`.
* `demo/playground.py` changes for hosting:
  - binds `0.0.0.0:$PORT` when `PORT` is set (Render sets it), else
    `127.0.0.1:7860` as before; `--host/--port` override;
  - optional login via `PLAYGROUND_USER` / `PLAYGROUND_PASSWORD` (Gradio
    `auth`), with a warning if hosted without one, because the hosted app
    spends whichever API key is configured;
  - **per-visitor sessions**: the `Playground` object moved from a shared
    module variable into `gr.State`, which Gradio deep-copies per browser
    session; every callback takes and returns it. Without this, two
    teammates on the hosted URL would share and overwrite one conversation.
  - `api_name` on each button so the app can be driven by `gradio_client`.

Default provider for the hosted app changed to OpenRouter at the team's
request (free models, no card). Queried OpenRouter's public model list on
2026-10-24: 20 of 467 models were free, and `openrouter/free` is a router that
picks among them, so it will not break when a single free model is retired.
`render.yaml` now sets `MODEL_PLATFORM=openrouter`, `MODEL_NAME=openrouter/free`
and asks for `OPENROUTER_API_KEY`; the `openrouter` default in the
`PLATFORMS` tables (script, notebook, playground) is `openrouter/free` too.
Caveat: free models are rate-limited per key and follow CAMEL's role format
less strictly than gpt-4.1-mini; not yet run end to end here (no key).

Verified locally as a Render stand-in: Python 3.11 venv built only from
`requirements-render.txt` (3.11.14; CAMEL declares `<3.15`), server started
with `PORT`, login, no API key, stub ticked; `gradio_client` as visitor 1:
start, 2 turns, guidance, 1 turn, direct question; unauthenticated client
refused; visitor 2 told "Press Start first", proving isolation. Not verified:
the actual Render build (needs the team's Render account); the first deploy
should be watched once in the Render logs.
