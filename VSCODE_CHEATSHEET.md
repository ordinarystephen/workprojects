# VS Code Cheatsheet — Tracing Python Code in KRONOS

This cheatsheet answers the question: **"I see a name in the code — how do I find where it comes from?"**

All examples use KRONOS code you'll encounter directly.

---

## Part 1 — Reading Python Imports (the mental model)

Before using VS Code shortcuts, you need to be able to read import lines.
An import is a map from a name in the code to a file on disk.

### The pattern

```python
from pipeline.analyze import analyze
```

Read this as:

```
from  pipeline / analyze  import  analyze
      ^folder   ^filename          ^function inside that file
```

So this line says: "Go into the `pipeline/` folder, open `analyze.py`, find the `analyze` function, and bring it in."

---

### The dot notation on module calls

```python
portfolio_summary.run(file_obj)
```

Read this as:

```
portfolio_summary  .  run
^the module (file)    ^function inside that file
```

So `portfolio_summary.run` means: "the `run()` function inside `pipeline/scripts/portfolio_summary.py`".

---

### Real examples from KRONOS

| What you see in code | What file it lives in | What inside that file |
|---|---|---|
| `from pipeline.analyze import analyze` | `pipeline/analyze.py` | the `analyze()` function |
| `from pipeline.agent import ask_agent` | `pipeline/agent.py` | the `ask_agent()` function |
| `from pipeline.tracking import mlflow_run` | `pipeline/tracking.py` | the `mlflow_run()` context manager |
| `from pipeline.prompts import get_system_prompt` | `pipeline/prompts.py` | the `get_system_prompt()` function |
| `portfolio_summary.run` | `pipeline/scripts/portfolio_summary.py` | the `run()` function |
| `concentration_risk.run` | `pipeline/scripts/concentration_risk.py` | the `run()` function |

Note: `pipeline/llm.py` was removed during the bank-standard refactor — the
Azure OpenAI client is now built inside `pipeline/agent.py` (in `create_llm()`).

**The rule:** Replace `.` with `/` and add `.py` at the end to get the file path.
`pipeline.scripts.portfolio_summary` → `pipeline/scripts/portfolio_summary.py`

---

## Part 2 — VS Code Navigation Shortcuts

### The most important one: Go to Definition

**Mac:** `Cmd + Click` on any function/variable name
**Windows:** `Ctrl + Click`

This is the single most useful shortcut. Click on a name, it jumps to where it's defined.

Examples:
- `Cmd+Click` on `analyze` in `server.py` → jumps to the `analyze()` function in `pipeline/analyze.py`
- `Cmd+Click` on `ask_agent` → jumps to `pipeline/agent.py`
- `Cmd+Click` on `build_llm` inside `agent.py` → jumps to `pipeline/llm.py`

---

### Quick Open (find any file fast)

**Mac:** `Cmd + P`
**Windows:** `Ctrl + P`

Type part of a filename and VS Code shows matches. Hit Enter to open.

Examples:
- `Cmd+P` → type `analyze` → opens `pipeline/analyze.py`
- `Cmd+P` → type `prompts` → opens `pipeline/prompts.py`
- `Cmd+P` → type `portfolio` → opens `portfolio_summary.py` (once you create it)

---

### Search Across All Files

**Mac:** `Cmd + Shift + F`
**Windows:** `Ctrl + Shift + F`

Type any text — function names, variable names, comments — and VS Code shows
every file and line where it appears.

Examples:
- Search `SCRIPT_MAP` → see every place the map is referenced
- Search `portfolio-summary` → find everywhere this mode slug appears
  (prompts.json, analyze.py, prompts.py — confirms they're all in sync)
- Search `def run` → find every processor `run()` function in the project

---

### Peek Definition (see a function without leaving your file)

**Mac:** `Option + F12`
**Windows:** `Alt + F12`

Hover over a function name and use this shortcut to see the function's code
in a floating panel inside the current file. Useful when you want to
check a function quickly without losing your place.

---

### Find All References (where is this thing used?)

**Mac:** `Shift + F12`
**Windows:** `Shift + F12`

Right-click on any name → "Find All References" — shows every place in the
project that uses this name. Useful for answering "if I change this, what else breaks?"

Example: right-click on `analyze` → see that `server.py` calls it and
`analyze.py` defines it.

---

### Go Back / Go Forward

**Mac:** `Ctrl + -` (go back) / `Ctrl + Shift + -` (go forward)
**Windows:** `Alt + ←` / `Alt + →`

After jumping to a definition, these bring you back to where you were.
Works like a browser's back button for code navigation.

---

### Rename Symbol (rename a function everywhere at once)

**Mac/Windows:** `F2` while cursor is on a name

Renames the function/variable in every file it's used. Safer than find-and-replace.

---

### Open Terminal

**Mac:** `` Ctrl + ` ``
**Windows:** `` Ctrl + ` ``

Opens the integrated terminal at the bottom. This is where you run `python server.py`.

---

### Outline View (see all functions in the current file)

**Mac/Windows:** In the left sidebar, click the document icon → "Outline" panel at the bottom

Shows every class and function in the current file as a collapsible tree.
Click a function name to jump to it instantly.

---

## Part 3 — Tracing `portfolio_summary.run` Step by Step

Here is the exact workflow you'd use to wire a new processor.

### Scenario: you want to add the Portfolio Summary processor

**Step 1 — Open the dispatcher file**

`Cmd+P` → type `analyze` → open `pipeline/analyze.py`

**Step 2 — Find the SCRIPT_MAP**

`Cmd+F` (find in file) → type `SCRIPT_MAP`

You'll see:
```python
SCRIPT_MAP = {
    # "portfolio-summary":  portfolio_summary.run,   # TODO
}
```

**Step 3 — Understand what `.run` means**

The commented line tells you:
- mode slug: `"portfolio-summary"` (must match `prompts.json`)
- module: `portfolio_summary` (a file you'll create)
- function: `.run` (a function inside that file)

**Step 4 — Create the processor file**

You need to create: `pipeline/scripts/portfolio_summary.py`

With a function named `run` that accepts `file_obj` and returns `{ context, metrics }`.

**Step 5 — Add the import and register it**

At the top of `analyze.py`:
```python
from pipeline.scripts import portfolio_summary
```

In `SCRIPT_MAP`:
```python
SCRIPT_MAP = {
    "portfolio-summary": portfolio_summary.run,
}
```

**Step 6 — Verify the mode slug matches prompts.json**

`Cmd+Shift+F` → search `portfolio-summary`

You should see it in three places:
1. `static/prompts.json` — the button definition
2. `pipeline/analyze.py` — the SCRIPT_MAP key
3. `pipeline/prompts.py` — the MODE_SYSTEM_PROMPTS key (optional but recommended)

If any of the three are missing or spelled differently, something won't work.

---

## Part 4 — The KRONOS Wiring Checklist

When adding any new mode, verify all three match exactly:

```
static/prompts.json          →  "mode": "my-new-mode"
pipeline/analyze.py          →  SCRIPT_MAP key: "my-new-mode"
pipeline/prompts.py          →  MODE_SYSTEM_PROMPTS key: "my-new-mode"
```

Use `Cmd+Shift+F` to search for your mode slug and confirm it appears in all three.

---

## Part 5 — Reading Error Messages

When something goes wrong, Flask prints an error in the terminal.
Here's how to read them:

```
Traceback (most recent call last):
  File "server.py", line 90, in upload        ← the call in server.py
  File "pipeline/analyze.py", line 45, in analyze    ← which function
  File "pipeline/analyze.py", line 112, in placeholder_processor ← where exactly
ImportError: No module named 'pipeline.scripts'    ← the actual problem
```

Read from the **bottom up** — the last line is the error, the lines above
show you the path through the code that led to it.

**Common errors and fixes:**

| Error | Meaning | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'pipeline.scripts'` | You haven't created the scripts/ folder yet | Create `pipeline/scripts/__init__.py` |
| `ImportError: cannot import name 'run'` | The file exists but has no `run()` function | Add `def run(file_obj):` to your script |
| `KeyError: 'context'` | Your processor returned a dict without a `context` key | Make sure your `run()` returns `{ "context": ..., "metrics": ... }` |
| `ClientAuthenticationError` | Not logged into Azure | Run `az login` in the terminal |
| `ValueError: Unknown mode` | Mode slug in prompts.json doesn't exist in SCRIPT_MAP | Add it to SCRIPT_MAP or fix the spelling |

---

## Quick Reference Card

| Task | Shortcut |
|---|---|
| Jump to where a function is defined | `Cmd+Click` on the name |
| Open any file by name | `Cmd+P` → type filename |
| Search all files for any text | `Cmd+Shift+F` |
| See definition without leaving | `Option+F12` |
| See all usages of a name | `Shift+F12` |
| Go back after jumping | `Ctrl + -` |
| Find text in current file | `Cmd+F` |
| Open terminal | `Ctrl + `` ` |
| See all functions in current file | Outline panel (left sidebar) |
| Rename something everywhere | `F2` on the name |
