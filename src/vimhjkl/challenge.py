"""Challenge / Skill data model and the challenge-type registry.

The engine is *generic*: it knows nothing about specific vim tricks.  Everything
trick-specific lives in data (``content/skills.json``) and is interpreted here
through ``CATEGORIES``.  Adding a new technique = adding a data entry, never
editing engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Challenge-type registry
# ---------------------------------------------------------------------------
# Each category drives how grader.py scores an attempt.  The registry is the
# single source of truth for which categories exist and how they are graded.
#
#   grade_kind:
#     "buffer" -> compare the resulting buffer lines against `goal`.
#     "cursor" -> compare the final cursor (line,col) against `target`.
#
#   wants_command: the typed ex command line is meaningful and is surfaced /
#                  graded (a single command should accomplish the transform).

@dataclass(frozen=True)
class CategorySpec:
    name: str
    grade_kind: str          # "buffer" | "cursor"
    wants_command: bool
    blurb: str               # one-line description of what this drills


CATEGORIES: dict[str, CategorySpec] = {
    "motion": CategorySpec(
        "motion", "cursor", False,
        "navigate to a target — graded on where the cursor lands + key count",
    ),
    "operator": CategorySpec(
        "operator", "buffer", False,
        "operator+motion transforms; rewards the dot-command formula",
    ),
    "text_object": CategorySpec(
        "text_object", "buffer", False,
        "transforms where a text object (iw, i\", i(, it...) is the short path",
    ),
    "register": CategorySpec(
        "register", "buffer", False,
        "rearrange text with yank/delete/put registers",
    ),
    "macro": CategorySpec(
        "macro", "buffer", False,
        "record once, replay across many lines; graded on the final buffer",
    ),
    "ex_command": CategorySpec(
        "ex_command", "buffer", True,
        "achieve a transform with ONE ex command (:g, :s, :normal, ranges)",
    ),
    "search_replace": CategorySpec(
        "search_replace", "buffer", True,
        "substitution challenges; graded on the resulting buffer",
    ),
}


def is_cursor_category(category: str) -> bool:
    spec = CATEGORIES.get(category)
    return spec is not None and spec.grade_kind == "cursor"


def wants_command(category: str) -> bool:
    spec = CATEGORIES.get(category)
    return spec is not None and spec.wants_command


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Challenge:
    """A single concrete practice instance for a skill.

    ``start``/``goal`` are buffer contents (list of lines, no trailing newline).
    For ``motion`` challenges the buffer is not changed; ``start_cursor`` and
    ``target`` describe a 1-based ``[line, col]`` to navigate from/to.
    """

    start: list[str]
    goal: list[str] = field(default_factory=list)
    par_keys: int = 1
    hint: str = ""
    # motion-only:
    start_cursor: Optional[list[int]] = None     # [line, col], 1-based
    target: Optional[list[int]] = None           # [line, col], 1-based
    # yank challenges: the buffer is unchanged (goal == start), so success is the
    # unnamed register holding `yank` (the exact bytes a linewise yank produces,
    # trailing newline and all).  Auto-captured by the build from the solution
    # replay; None for ordinary buffer/motion challenges.
    yank: Optional[str] = None
    # shown only AFTER the attempt (and on review-card BACK):
    solution: str = ""                           # optimal keystrokes, e.g. 'ciwfoo<Esc>'
    why: str = ""                                # one-line "why this is idiomatic"

    @classmethod
    def from_dict(cls, d: dict) -> "Challenge":
        return cls(
            start=list(d.get("start", [])),
            goal=list(d.get("goal", [])),
            par_keys=int(d.get("par_keys", 1)),
            hint=d.get("hint", ""),
            start_cursor=d.get("start_cursor"),
            target=d.get("target"),
            yank=d.get("yank"),
            solution=d.get("solution", ""),
            why=d.get("why", ""),
        )

    def to_dict(self) -> dict:
        d: dict = {"start": self.start, "par_keys": self.par_keys, "hint": self.hint}
        if self.goal:
            d["goal"] = self.goal
        if self.start_cursor is not None:
            d["start_cursor"] = self.start_cursor
        if self.target is not None:
            d["target"] = self.target
        if self.yank is not None:
            d["yank"] = self.yank
        if self.solution:
            d["solution"] = self.solution
        if self.why:
            d["why"] = self.why
        return d


@dataclass
class Skill:
    """A technique with many repeatable challenge instances."""

    id: str
    title: str
    category: str
    teach: str
    key_commands: list[str] = field(default_factory=list)
    difficulty: int = 1
    challenges: list[Challenge] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(
            id=d["id"],
            title=d["title"],
            category=d["category"],
            teach=d.get("teach", ""),
            key_commands=list(d.get("key_commands", [])),
            difficulty=int(d.get("difficulty", 1)),
            challenges=[Challenge.from_dict(c) for c in d.get("challenges", [])],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "teach": self.teach,
            "key_commands": self.key_commands,
            "difficulty": self.difficulty,
            "challenges": [c.to_dict() for c in self.challenges],
        }

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty == valid)."""
        problems: list[str] = []
        if self.category not in CATEGORIES:
            problems.append(f"{self.id}: unknown category {self.category!r}")
        if not self.challenges:
            problems.append(f"{self.id}: no challenge instances")
        for i, c in enumerate(self.challenges):
            tag = f"{self.id}[{i}]"
            if is_cursor_category(self.category):
                if not c.target:
                    problems.append(f"{tag}: motion challenge missing 'target'")
            else:
                if not c.goal:
                    problems.append(f"{tag}: missing 'goal' buffer")
            if c.par_keys < 1:
                problems.append(f"{tag}: par_keys must be >= 1")
        return problems
