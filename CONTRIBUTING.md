# Contributing to vimhjkl

Thanks for helping out! Bug fixes, new lessons, and better example buffers are all
welcome.

## Setup

You need [uv](https://docs.astral.sh/uv/) and `vim` or `nvim` on `PATH`. There are
no other dependencies — the package is pure standard library.

```sh
git clone https://github.com/S-Sigdel/vimhjkl && cd vimhjkl
uv sync
uv run vimhjkl            # run it
```

Run the test suites before opening a PR (the grader suite plays keystrokes back
through real vim):

```sh
uv run python -m tests.test_grader
uv run python -m tests.test_engine
```

## Workflow

Work on a branch, never directly on `master` — a named branch keeps your pull
request reviewable and lets you pull upstream changes without conflicts.

1. **Fork** the repo and clone your fork.

2. **Branch, named for the change.** Use a short prefix so the intent is obvious:
   `fix/` for a bug, `lesson/` for a new or reworked lesson, `feature/` for
   anything else.

   ```sh
   git switch -c fix/ctrl-j-quit-counted
   ```

3. **Make one focused change.** One bug fix or one lesson per branch — small PRs
   get reviewed and merged faster. Match the surrounding style (the package is
   stdlib-only; no new dependencies without discussion).

4. **Run both test suites** (commands above) and confirm they pass. If you touched
   grading behaviour, add a check to `tests/test_grader.py`; if you touched
   selection/scoring, add one to `tests/test_engine.py`.

5. **Commit in the repo's style** — a short, lowercase, imperative summary with no
   `type:` prefix, e.g.

   ```
   count the save keystrokes correctly when Enter sends <NL>
   ```

   Keep each commit a single logical change; squash noise before pushing.

6. **Push and open a pull request** against `master`. In the description, say what
   changed and why. For a lesson, paste the **start buffer**, the **goal**, and the
   **keystrokes** so a reviewer can verify it in one paste.

## Layout

```
src/vimhjkl/
  cli.py            # menu, modes, session orchestration
  engine.py         # scheduling and scoring — knows nothing about specific tricks
  challenge.py      # Challenge/Skill model + the category registry
  grader.py         # launches real vim, captures result + keystrokes, scores
  store.py          # JSON persistence
  tui.py            # ANSI rendering and input
  data/skills.json  # the curriculum (data, not code)
tests/              # headless grader and engine checks
```

The engine is generic: a technique is a **data entry**, never an engine edit. A new
trick should be a lesson in `skills.json`, not a special case in `grader.py` or
`engine.py` — if you find yourself editing those to teach one specific move, stop
and reconsider.

## Adding or fixing a lesson

A lesson lives in `src/vimhjkl/data/skills.json`. Each skill has a `category` (one
of the keys in `CATEGORIES` in `challenge.py`) that decides how it's graded, and a
list of `challenges`:

```json
{
  "id": "marks-as-ex-range",
  "title": "Use marks as an Ex range ('a,'b)",
  "category": "ex_command",
  "teach": "Two or three sentences explaining the move.",
  "key_commands": [":'a,'b", "ma", "mb"],
  "difficulty": 4,
  "challenges": [
    {
      "start": ["lines the user starts with"],
      "goal":  ["lines the buffer must equal when done"],
      "solution": "the literal keystrokes, <Esc>/<CR> spelled out",
      "par_keys": 25,
      "hint": "a short nudge",
      "why": "one line on why this is the idiomatic path"
    }
  ]
}
```

`motion` challenges use `start_cursor` + `target` (a 1-based `[line, col]`) instead
of `goal`. See `CATEGORIES` in `challenge.py` for every category.

What makes a **good** challenge:

- **The example must need the technique.** Size the buffer so the lesson's move is
  the shortest correct path. A `:g`/macro/sort/range drill on a 2-line buffer is
  pointless — a simpler move would beat it. For range/marks drills, put a match
  *outside* the range so a whole-file `:%…` would change the wrong lines.
- **Original, concrete text.** No `foo`/`bar`, no `one/two/three`. Vivid, specific
  example text; never copy real book/song/source text.
- **`solution` is the optimal path** (`par_keys` is the optimal keystroke count,
  excluding the final save/quit) and must reproduce `goal` exactly when replayed in
  real vim. Run your keystrokes in a clean editor (`vim -u NONE`) to confirm.

## Reporting issues

Open an [issue](https://github.com/S-Sigdel/vimhjkl/issues) for bugs or lesson
ideas. For a new lesson, the most useful report is the **start buffer**, the
**goal buffer**, and the **keystrokes** — that's everything needed to verify it.

By contributing you agree your work is licensed under the [MIT License](LICENSE).
