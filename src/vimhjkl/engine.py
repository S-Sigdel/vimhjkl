"""Generic session engine: scheduling + scoring.

This module knows NOTHING about specific vim tricks.  It selects which skills
are due (shared by the live drill and the --review flashcards), picks a random
challenge instance per attempt, hands grading to grader.py, and updates the
Leitner boxes in progress.json.
"""

from __future__ import annotations

import random
import textwrap
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import store
from .challenge import Skill, Challenge, is_cursor_category
from .grader import GradeResult, run_attempt, display_solution
from .keys import translate


# ---------------------------------------------------------------------------
# Shared selector — "pick due skills"
# ---------------------------------------------------------------------------
# Weighted toward weakness, used by BOTH the drill session and review mode:
#   1. due review items first  (lowest Leitner box, then most-recently-failed),
#   2. then never-seen / new skills, gated by difficulty,
#   3. then least-recently-seen as filler.
#
# "Due" uses a simple per-box interval: higher box == longer rest.

# Minimum seconds before a skill in a given box is due again.  Box 1 is always
# due; higher boxes rest longer (spaced repetition).  Boxes 3–5 stretch toward
# hours so a "known but not yet grooved" skill gets genuine spacing — but note
# `select_due_skills` backfills a session from not-yet-due skills, so this mostly
# changes PRIORITY, not whether a skill appears (true day-scale spacing is the
# grooved/maintenance tier below).  Scaled per-skill by `ease` (fluency).
_BOX_INTERVAL = {1: 0, 2: 60, 3: 600, 4: 3600, 5: 28800}

# Second axis: how many *successful* reps grooves a technique into muscle memory.
# The Leitner box (above) measures "do you know the move" and fills in ~3-4 good
# reps; this measures "have you drilled it enough to keep it" and is the bar for
# the "grooved" badge.  Until you reach it, a box-maxed skill keeps coming back
# on the normal (frequent) box schedule so you accumulate reps toward the target.
MASTERY_REPS = 25

# Once a skill is BOTH box-maxed AND grooved (>= MASTERY_REPS), it graduates to a
# long maintenance schedule: still resurfaces forever, just rarely, lengthening
# the longer you keep nailing it (1 day, then 2, 4, ... capped at ~30 days).  A
# later fail drops the box, which drops it out of maintenance automatically.
_MAINT_BASE = 86400          # 1 day
_MAINT_CAP = 2592000         # 30 days


def _entry_for(progress: dict, skill_id: str) -> dict:
    return progress.get("skills", {}).get(skill_id, store.default_progress_entry())


def is_grooved(entry: dict) -> bool:
    """True once a box-maxed skill has been drilled to the rep target.  Keyed on
    the box too, so one fail (which lowers the box) pulls it back to active
    review even if its lifetime pass count stays above the target."""
    return (entry.get("box", 1) >= store.MAX_BOX
            and entry.get("passes", 0) >= MASTERY_REPS)


def _due_interval(entry: dict) -> float:
    # `ease` (a fluency multiplier, 0.5–2.5) stretches/shrinks the rest: fast solves
    # earn longer rests, slow ones shorter.  Applied BEFORE the maintenance cap so a
    # high ease can never push a grooved skill past the 30-day ceiling.
    ease = entry.get("ease", 1.0)
    if is_grooved(entry):
        extra = entry.get("passes", 0) - MASTERY_REPS
        return min(_MAINT_CAP, _MAINT_BASE * (2 ** (extra // 5)) * ease)
    return _BOX_INTERVAL.get(entry.get("box", 1), 0) * ease


def is_due(entry: dict, now: float) -> bool:
    last = entry.get("last_seen", 0.0) or 0.0
    if last == 0.0:
        return True  # never seen -> due (treated as "new" below)
    return (now - last) >= _due_interval(entry)


def _weighted_order(items: list, weight_fn, rng: random.Random) -> list:
    """Random order where higher-weight items tend toward the front
    (Efraimidis–Spirakis weighted sampling without replacement)."""
    keyed = []
    for it in items:
        w = max(weight_fn(it), 1e-9)
        keyed.append((rng.random() ** (1.0 / w), it))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [it for _, it in keyed]


def _fail_rank(e: dict) -> int:
    return 0 if e.get("last_result") == "fail" else 1


def _interleave(a: list, b: list) -> list:
    """Round-robin merge of two ordered lists (a first when tied), so a capped
    session draws from both instead of exhausting one before touching the other."""
    out = []
    for x, y in zip(a, b):
        out.append(x)
        out.append(y)
    out.extend(a[len(b):] if len(a) > len(b) else b[len(a):])
    return out


def select_due_skills(skills: list[Skill], progress: dict, count: int,
                      *, new_gate: Optional[int] = None,
                      include_new: bool = True,
                      now: Optional[float] = None,
                      rng: Optional[random.Random] = None) -> list[Skill]:
    """Return up to ``count`` skills, weakest/most-due first.

    ``new_gate``: only introduce brand-new skills whose ``difficulty`` <=
    new_gate.  ``None`` means no gate (all new skills eligible).

    ``include_new``: when False, brand-new (never-seen) skills are excluded
    entirely — the session only draws from skills you've already encountered.
    Blind mode uses this so it recalls what you know rather than quizzing
    material you've never been taught; the eligible set grows on its own as
    other modes record a ``last_seen`` for each skill.

    ``rng``: when given, the order within each priority tier is a *weighted
    shuffle* — still skewed toward weak/easy skills, but a different mix every
    session so you aren't served the same list each time.  When ``None`` the
    order is fully deterministic (used by the tests).
    """
    now = now if now is not None else time.time()

    seen, new = [], []
    for s in skills:
        entry = _entry_for(progress, s.id)
        if entry.get("last_seen", 0.0):
            seen.append((s, entry))
        else:
            new.append((s, entry))

    # Due skills split into two tiers: skills still being *learned* (box < max)
    # come first, brand-new skills next, and fully-mastered skills (box 5) that
    # have merely passed their rest interval go AFTER new skills.  Otherwise a
    # big pile of mastered "due" reviews fills every session slot and a player
    # who has aced the easy tier never actually sees the skills just unlocked.
    due_learn = [(s, e) for (s, e) in seen
                 if is_due(e, now) and e.get("box", 1) < store.MAX_BOX]
    due_review = [(s, e) for (s, e) in seen
                  if is_due(e, now) and e.get("box", 1) >= store.MAX_BOX]
    eligible_new = [] if not include_new else \
        [(s, e) for (s, e) in new
         if new_gate is None or s.difficulty <= new_gate]
    not_due = [(s, e) for (s, e) in seen if not is_due(e, now)]

    # Within the box-maxed review tier, the skills still grooming (passes < the
    # rep target) get priority by fewest reps, so every technique climbs toward
    # 25 rather than the same few being over-served.
    reps_left = lambda se: max(0, MASTERY_REPS - se[1].get("passes", 0))

    if rng is None:
        # Deterministic: lowest box first / most-recently-failed; new easiest-first.
        box_key = lambda se: (se[1].get("box", 1), _fail_rank(se[1]),
                              se[1].get("last_seen", 0.0))
        due_learn.sort(key=box_key)
        due_review.sort(key=lambda se: (-reps_left(se), se[1].get("last_seen", 0.0)))
        eligible_new.sort(key=lambda se: (se[0].difficulty, se[0].id))
        not_due.sort(key=lambda se: se[1].get("last_seen", 0.0))
    else:
        # Weighted-random: weaker box / a recent fail / easier difficulty all
        # raise the odds of appearing, without fixing the order.
        box_weight = lambda se: ((6 - se[1].get("box", 1))
                                 * (2 if _fail_rank(se[1]) == 0 else 1))
        due_learn = _weighted_order(due_learn, box_weight, rng)
        due_review = _weighted_order(due_review,
                                     lambda se: 1 + reps_left(se), rng)
        eligible_new = _weighted_order(eligible_new,
                                       lambda se: 6 - se[0].difficulty, rng)
        rng.shuffle(not_due)

    # Weak/failing skills first (must-fix), then a BLEND of brand-new skills and
    # old box-maxed skills due for review — interleaved so a session always mixes
    # learning something new with practising things you already know, and neither
    # side starves the other when one list is long.
    blended = _interleave([s for (s, _) in eligible_new],
                          [s for (s, _) in due_review])
    ordered = [s for (s, _) in due_learn] \
        + blended \
        + [s for (s, _) in not_due]

    # De-dup while preserving order, then truncate.
    out: list[Skill] = []
    used: set[str] = set()
    for s in ordered:
        if s.id not in used:
            out.append(s)
            used.add(s.id)
        if len(out) >= count:
            break
    return out


# Per-skill "shuffle bag": deal every instance once in a random order before any
# repeats, so you cycle through ALL of a skill's variety (not the same one twice
# in a row by chance).  State lives for the process — a fresh shuffle each run.
_bags: dict[str, list[int]] = {}
_last_dealt: dict[str, int] = {}


def pick_challenge(skill: Skill, rng: Optional[random.Random] = None) -> Challenge:
    rng = rng or random
    n = len(skill.challenges)
    if n <= 1:
        return skill.challenges[0]
    bag = _bags.get(skill.id)
    if not bag:
        bag = list(range(n))
        rng.shuffle(bag)
        # don't let a refill immediately repeat the instance we just showed
        if bag[0] == _last_dealt.get(skill.id) and n > 1:
            bag[0], bag[1] = bag[1], bag[0]
    idx = bag.pop(0)
    _bags[skill.id] = bag
    _last_dealt[skill.id] = idx
    return skill.challenges[idx]


# ---------------------------------------------------------------------------
# Weak-skill selector — drives Practice mode ("hammer what you keep missing")
# ---------------------------------------------------------------------------

def select_weak_skills(skills: list[Skill], progress: dict, count: int,
                       *, rng: Optional[random.Random] = None) -> list[Skill]:
    """Return up to ``count`` *previously-attempted* skills you're weakest at.

    Weak == low Leitner box (<=2) or the last attempt failed.  ``rng`` makes the
    pick a weighted shuffle (weaker/failed skills favoured) so Practice varies;
    ``None`` keeps the deterministic weakest-first order.  Returns ``[]`` if
    nothing has been attempted yet — Practice is remedial, so the caller decides
    what to do when there is nothing to remediate.
    """
    weak: list[tuple[Skill, dict]] = []
    for s in skills:
        e = progress.get("skills", {}).get(s.id)
        if not e or not e.get("last_seen", 0.0):
            continue  # never attempted -> not "missed", nothing to practice
        if e.get("box", 1) <= 2 or e.get("last_result") == "fail":
            weak.append((s, e))
    if rng is None:
        weak.sort(key=lambda se: (se[1].get("box", 1), _fail_rank(se[1]),
                                  se[1].get("last_seen", 0.0)))
    else:
        weak = _weighted_order(
            weak,
            lambda se: (6 - se[1].get("box", 1)) * (2 if _fail_rank(se[1]) == 0 else 1),
            rng)
    return [s for (s, _) in weak[:count]]


# ---------------------------------------------------------------------------
# Mastery / belt progression — keybr-style "watch the number climb"
# ---------------------------------------------------------------------------

# A dojo ranking: your overall mastery (mean Leitner box across the whole
# curriculum) maps to a belt and unlocks harder new skills as you rank up.
BELTS = ["white", "yellow", "orange", "green", "blue", "purple", "brown", "black"]


def mastery_summary(skills: list[Skill], progress: dict) -> dict:
    """Overall progression: belt, percent, and the difficulty gate for new skills.

    ``fraction`` is mean ``(box-1)/4`` over every skill (unseen counts as box 1),
    so a fresh player starts white-belt and climbs toward black as boxes fill.
    ``new_gate`` is the max difficulty of brand-new skills to introduce — it rises
    with rank so the curriculum unlocks gradually (keybr-style).
    """
    n = len(skills) or 1
    pdata = progress.get("skills", {})
    entries = [(pdata.get(s.id) or {}) for s in skills]
    boxes = [e.get("box", 1) for e in entries]
    mastered = sum(1 for b in boxes if b >= 4)
    grooved = sum(1 for e in entries if is_grooved(e))
    fraction = max(0.0, min(1.0, (sum(boxes) / n - 1) / 4))
    belt_idx = round(fraction * (len(BELTS) - 1))
    return {
        "fraction": fraction,
        "percent": int(round(fraction * 100)),
        "belt": BELTS[belt_idx],
        "belt_index": belt_idx,
        "level": belt_idx + 1,
        "max_level": len(BELTS),
        "new_gate": _new_gate(skills, progress),
        "mastered": mastered,
        "grooved": grooved,
        "total": len(skills),
    }


# Box at/above which a skill counts as "mastered" for unlocking the next tier.
GATE_MASTER_BOX = 4


def _new_gate(skills: list[Skill], progress: dict) -> int:
    """Difficulty ceiling for introducing brand-new skills, 2..5.

    Tier-based progression instead of a global mean: you start with the
    post-vimtutor tier (difficulty <=2) open, and the gate rises to the next
    difficulty once you have mastered the tier at or below it.  This is what
    lets the curriculum keep unlocking — a mean over *all* skills can't, because
    the large pool of still-locked skills sits at box 1 and permanently drags
    the average below the next threshold (the gate would deadlock at 2).

    A tier counts as mastered with an all-but-one tolerance (>=80%, and never
    more than one straggler), so a single demotion among many mastered skills
    doesn't bounce the gate back down and re-hide the next tier.
    """
    pdata = progress.get("skills", {})

    def tier_mastered(max_diff: int) -> bool:
        tier = [s for s in skills if s.difficulty <= max_diff]
        if not tier:
            return False
        done = sum(1 for s in tier
                   if (pdata.get(s.id) or {}).get("box", 1) >= GATE_MASTER_BOX)
        missing = len(tier) - done
        return missing <= max(1, int(round(len(tier) * 0.2)))

    gate = 2
    while gate < 5 and tier_mastered(gate):
        gate += 1
    return gate


# ---------------------------------------------------------------------------
# Promotion policy
# ---------------------------------------------------------------------------
# A "pass" for spaced-rep purposes = correct AND reasonably efficient.  Correct
# but wasteful still counts as correct (the buffer is right) but we require a
# modest efficiency floor to PROMOTE — sloppy solves don't advance the box.  Same
# threshold store.record_result uses to grade a "good" (promoting) solve, so the
# session summary's "promoted" count and the actual box move never disagree.

EFFICIENCY_FLOOR = store.PROMOTE_EFF


def outcome_passed(result: GradeResult) -> bool:
    return result.correct and result.efficiency >= EFFICIENCY_FLOOR


# ---------------------------------------------------------------------------
# remap collisions — a remap can shadow a key a lesson needs, making it
# impossible.  Detect those skills so they can be excluded from scheduling.
# ---------------------------------------------------------------------------

def _first_key(kc: str) -> str:
    """The first keystroke of a key_command string: a ``<…>`` token whole, else the
    first character (``dd``→``d``, ``ciw``→``c``, ``<C-a>``→``<C-a>``)."""
    if kc.startswith("<") and ">" in kc:
        return kc[: kc.index(">") + 1]
    return kc[:1]


_CMDLINE_CATEGORIES = {"ex_command", "search_replace"}


def remap_blocks_skill(remap: dict, skill: Skill) -> bool:
    """True if ``remap`` shadows a key the skill TEACHES, so the lesson can't be done
    with the mapping in place.  Three conditions, so it neither over- nor
    under-blocks:

    1. The skill isn't cmdline-driven (``ex_command``/``search_replace`` are typed
       after ``:`` — a normal/insert key remap doesn't shadow them).
    2. ``from`` matches the skill's author-declared ``key_commands`` — a single-key
       command it lists (``s``, ``;``, ``/``) or the leading key of one (``d`` in
       ``dd``).  This confirms the key is part of the taught technique.
    3. ``from`` actually appears in at least one optimal solution — so a key the
       skill merely lists as a sibling (``<C-p>`` next to the ``<C-n>`` it really
       uses) doesn't block it, and neither does a comfort remap like ``jk``/``<C-p>``
       → ``<Esc>`` (which matches no command and no solution)."""
    if skill.category in _CMDLINE_CATEGORIES:
        return False
    frm = remap.get("from", "")
    if not frm:
        return False
    if not any(kc == frm or _first_key(kc) == frm for kc in skill.key_commands):
        return False
    fb = translate(frm)
    return bool(fb) and any(fb in translate(c.solution)
                            for c in skill.challenges if c.solution)


def usable_skills(skills: list[Skill], remaps: Optional[list[dict]]) -> list[Skill]:
    """Skills schedulable given the player's remaps — those NOT blocked by any
    remap (you can't drill a technique whose key you've mapped away)."""
    rs = remaps or []
    return [s for s in skills
            if not any(remap_blocks_skill(r, s) for r in rs)]


def skills_blocked_by_remaps(skills: list[Skill],
                             remaps: Optional[list[dict]]) -> list[Skill]:
    rs = remaps or []
    return [s for s in skills if any(remap_blocks_skill(r, s) for r in rs)]


def attempt_abstained(result: GradeResult, category: str) -> bool:
    """True if the player bailed rather than genuinely attempting — vim failed to
    run, or a save-based challenge was quit without writing the buffer.

    Abstentions leave mastery untouched (no promote, no demote): quitting without
    saving means "I'm stopping for now, I'll restart later", not "I failed".  We
    only apply the no-save rule to buffer challenges; motions never save, so they
    are graded on where the cursor landed as usual.
    """
    if result.aborted:
        return True
    if not is_cursor_category(category) and not result.saved and not result.correct:
        return True
    return False


# ---------------------------------------------------------------------------
# Drill session
# ---------------------------------------------------------------------------

def reveal_pane_lines(skill: Skill, challenge: Challenge,
                      width: int = 26, remaps: Optional[list[dict]] = None) -> list[str]:
    """Plain-text cheat-sheet lines for the in-vim GOAL pane (Learn/Practice/Grind,
    never Blind): the idiomatic move, why it's idiomatic, the hint, and the key
    family.  Each line is ``2 + 11 (label) + width`` ≈ 39 cols, so it fits the
    side pane (capped at half the terminal) without the pane truncating it.  When
    the player has remaps, the move is shown with THEIR keys."""
    out: list[str] = []

    def field(label: str, text: str) -> None:
        if not text:
            return
        wrapped = textwrap.wrap(text, width) or [""]
        out.append(f"  {label}{wrapped[0]}")
        pad = " " * (2 + len(label))
        out.extend(pad + w for w in wrapped[1:])

    field("the move:  ", display_solution(challenge.solution, remaps))
    field("why:       ", challenge.why)
    field("hint:      ", challenge.hint)
    field("keys:      ", "  ".join(skill.key_commands))
    out.append(f"  par:       {challenge.par_keys} keystrokes")
    return out


@dataclass
class AttemptRecord:
    skill: Skill
    challenge: Challenge
    result: GradeResult
    passed: bool
    abstained: bool = False        # quit without saving -> mastery left untouched


@dataclass
class SessionSummary:
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.attempts)

    @property
    def correct(self) -> int:
        return sum(1 for a in self.attempts if a.result.correct)

    @property
    def promoted(self) -> int:
        return sum(1 for a in self.attempts if a.passed)

    @property
    def skipped(self) -> int:
        return sum(1 for a in self.attempts if a.abstained)


class DrillSession:
    """Runs a sequence of real-vim challenges and updates progress.

    UI is injected via callbacks so the engine stays headless/testable:
      * ``present(skill, challenge)`` — show the task, return when ready to launch.
      * ``review(record)``           — show the graded outcome + optimal solution.
      * ``run`` returns a SessionSummary.
    """

    def __init__(self, skills: list[Skill], progress: dict,
                 present: Callable[[Skill, Challenge], None],
                 review: Callable[[AttemptRecord], str],
                 rng: Optional[random.Random] = None,
                 editor: Optional[str] = None,
                 on_record: Optional[Callable[[], None]] = None,
                 highlight_target: bool = False,
                 reveal_detail: bool = False,
                 free_form: bool = False,
                 remaps: Optional[list[dict]] = None):
        self.skills = skills
        self.progress = progress
        self.present = present
        self.review = review
        self.rng = rng or random
        self.editor = editor
        # Called after each graded attempt is recorded — used to flush progress
        # to disk so a Ctrl-C / crash mid-session never loses finished work.
        self.on_record = on_record
        # Show the motion target in-buffer (learn/practice) vs hide it (blind).
        self.highlight_target = highlight_target
        # Show the move/why/hint cheat-sheet in the GOAL pane (learn) vs hide it.
        self.reveal_detail = reveal_detail
        # Free-form (no-handcuffs blind): accept ANY path that reaches the goal —
        # drop the ex-command technique enforcement.  The buffer/register goal and
        # efficiency are still graded.
        self.free_form = free_form
        # General key remaps to inject into the live drill (and to rewrite the
        # suggested move in the goal pane so it shows the player's own keys).
        self.remaps = remaps

    def run(self, selected: list[Skill]) -> SessionSummary:
        """Drill each selected skill.  ``review`` returns an action string:
        ``"retry"`` re-attempts the skill and the discarded attempt does NOT
        touch mastery; ``"next"`` commits the attempt and advances; ``"quit"``
        commits and ends the session early.  An abstain (quit without saving)
        re-shows the full task itself, so on its retry we relaunch the SAME
        challenge directly rather than presenting the task a second time."""
        summary = SessionSummary()
        for skill in selected:
            action = "next"
            challenge = pick_challenge(skill, self.rng)
            present_next = True
            while True:
                if present_next:
                    self.present(skill, challenge)
                present_next = True
                goal_extra = (reveal_pane_lines(skill, challenge, remaps=self.remaps)
                              if self.reveal_detail else None)
                result = run_attempt(challenge, skill.category, editor=self.editor,
                                     highlight_target=self.highlight_target,
                                     goal_extra=goal_extra,
                                     enforce_command=not self.free_form,
                                     remaps=self.remaps)
                abstained = attempt_abstained(result, skill.category)
                passed = (not abstained) and outcome_passed(result)
                record = AttemptRecord(skill, challenge, result, passed,
                                       abstained=abstained)
                action = self.review(record)
                if action == "retry":
                    if abstained:
                        present_next = False   # abstain screen already showed it
                    else:
                        challenge = pick_challenge(skill, self.rng)
                    continue            # toss this attempt; mastery untouched
                # Accepted: commit exactly this attempt's outcome (graded by
                # correctness + efficiency, so a fast solve advances and a slow one
                # holds — see store.record_result).
                if not abstained:
                    store.record_result(self.progress, skill.id, result.correct,
                                        result.efficiency)
                    if self.on_record:
                        self.on_record()  # flush so Ctrl-C never loses it
                summary.attempts.append(record)
                break
            if action == "quit":
                break
        return summary
