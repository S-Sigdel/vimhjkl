<div align="center">

# ⚔ vimhjkl

**Drills the Vim techniques `vimtutor` skips, in real vim/nvim, graded on your keystrokes.**

[![AUR version](https://img.shields.io/aur/version/vimhjkl?label=AUR)](https://aur.archlinux.org/packages/vimhjkl)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Lessons](https://img.shields.io/badge/lessons-66-success)
![Pure stdlib](https://img.shields.io/badge/deps-stdlib%20only-informational)

</div>

66 skills, 230 challenges: dot command, operator+motion grammar, text objects,
registers, marks, macros, `:g` / `:normal` / ranges, regex and substitution,
indentation, joins, paragraph motions. Every challenge is machine-verified
against real vim. The goal is shown in a split next to the buffer while you
edit.

![the menu](img/homepage.png)

## Install

### macOS / Linux (Homebrew)

```sh
brew install S-Sigdel/tap/vimhjkl
```

### Arch Linux (AUR)

```sh
yay -S vimhjkl
```

### From source

Needs [uv](https://docs.astral.sh/uv/) and `vim` or `nvim`:

```sh
git clone https://github.com/S-Sigdel/vimhjkl && cd vimhjkl
uv sync && uv run vimhjkl
```

## Usage

```sh
vimhjkl                                     # interactive menu
vimhjkl --drill                             # Learn mode
vimhjkl --drill --mode blind                # Blind mode
vimhjkl --drill --mode blind --blind-all    # blind sweep of every skill
vimhjkl --practice                          # weakest skills, retry until pass
vimhjkl --reps 6 [--skill ID]               # Grind one skill N times
vimhjkl --review                            # flashcards, no editor
vimhjkl --list                              # curriculum + mastery + skill IDs
vimhjkl --lang zh-CN                        # teaching text in Chinese
```

Other flags: `-n/--count N`, `--gate D` (cap new-skill difficulty), `--hide-moves`.

| Mode     | What it does                                              |
|----------|-----------------------------------------------------------|
| Learn    | Shows the technique and the idiomatic move, then you edit |
| Blind    | Before/after only; you recall the move                    |
| Practice | Weakest skills, retry until pass                          |
| Grind    | One skill, N reps back-to-back                            |
| Review   | Flashcards, self-rated                                    |

Settings (in the menu): toggle lessons, choose language, remap any key in any
mode (`jk` → `<Esc>`, `;` → `:`) — remapped keys are graded as the original —
and add "Vim extras": your own display commands (`set norelativenumber`,
`colorscheme habamax`) run at drill startup. Drills stay on a clean
`vim -u NONE` so plugins and autocmds can't skew grading.

## How it works

You edit in real vim, not an emulator. The goal sits in a read-only split:

![the goal sits beside the buffer](img/actualedit.png)

- Keystrokes are captured with `vim -W` and scored on correctness and
  efficiency against a verified par.
- Command drills (`:s`, `:g`, `:normal`) require an actual ex command;
  hand-editing to the goal is rejected.
- Mastery is per-skill: a Leitner box (1–5) for scheduling and unlocks, plus a
  rep count toward 25, after which the skill moves to a maintenance schedule.
- A passing attempt is correct and ≤ 2× par. Quitting without saving is an
  abstain and does not count against you.
- Harder skills unlock as the tier below is mastered.

All modes write to the same mastery model; the mode only changes how much help
you see before editing. Practice records one outcome per skill (best retry);
Grind records every rep.

<details>
<summary>More screenshots</summary>

![Learn mode](img/learnmode.png)

![Curriculum](img/curriculum.png)

</details>

## Rebuilding the curriculum

`src/vimhjkl/data/skills.json` is generated, not hand-edited. Lessons live in
`build/passes/*.py`; `content/llm_pool.json` holds extra verified instances.

```sh
uv run python -m build.generate          # verify every challenge in real vim, write skills.json
uv run python -m tests.test_grader       # grading tests (replays keys through vim)
uv run python -m tests.test_engine       # scheduling/scoring tests
uv run python -m tests.test_i18n         # locale overlay tests
```

`build.generate` refuses to write if any challenge fails verification.

## Contributing

Adding a technique is a data change, not an engine change. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)

## Star History

<a href="https://star-history.dera.page/#S-Sigdel/vimhjkl&type=date&legend=top-left">
 <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=S-Sigdel/vimhjkl&type=date&theme=dark&legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=S-Sigdel/vimhjkl&type=date&legend=top-left" />
    <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=S-Sigdel/vimhjkl&type=date&legend=top-left" />
 </picture>
</a>
