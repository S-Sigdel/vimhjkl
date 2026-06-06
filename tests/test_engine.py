"""Unit checks for the engine selector + Leitner progress (no vim needed)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vimhjkl.challenge import Skill, Challenge
from vimhjkl.engine import select_due_skills, is_due, outcome_passed, _due_interval
from vimhjkl.grader import GradeResult
from vimhjkl import store

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        FAIL += 1
        print(f"  \033[31m✗ {name}\033[0m  {detail}")


def mk(id, diff):
    return Skill(id=id, title=id, category="operator", teach="t",
                 difficulty=diff, challenges=[Challenge(start=["x"], goal=["y"], par_keys=1)])


def main():
    now = time.time()
    skills = [mk("a", 1), mk("b", 3), mk("c", 5), mk("d", 2)]

    # Empty progress -> all "new", gated by difficulty, easiest first.
    prog = {"skills": {}}
    sel = select_due_skills(skills, prog, count=2, new_gate=2, now=now)
    check("gate excludes hard new skills",
          all(s.difficulty <= 2 for s in sel), [s.id for s in sel])
    check("new skills easiest-first", [s.id for s in sel] == ["a", "d"],
          [s.id for s in sel])

    # A seen, failed, box-1 skill should outrank new skills.
    prog = {"skills": {
        "b": {"box": 1, "seen": 3, "passes": 0, "fails": 3,
              "last_seen": now - 1000, "last_result": "fail", "best_efficiency": 0.0},
        "c": {"box": 4, "seen": 5, "passes": 5, "fails": 0,
              "last_seen": now - 5, "last_result": "pass", "best_efficiency": 1.0},
    }}
    sel = select_due_skills(skills, prog, count=4, new_gate=5, now=now)
    check("most-failing due skill comes first", sel[0].id == "b", [s.id for s in sel])
    check("box-4 not-yet-due skill is deprioritised",
          sel[-1].id == "c", [s.id for s in sel])

    # is_due: box 4 rests 1h (3600s).
    check("box4 not due after 5s",
          not is_due({"box": 4, "last_seen": now - 5}, now))
    check("box4 due after 2h",
          is_due({"box": 4, "last_seen": now - 7200}, now))
    check("never-seen always due", is_due({"box": 1, "last_seen": 0.0}, now))

    # --- Two-axis (rep grooming) regression guards -------------------------
    # New skills must still surface even when many seen skills are due — the
    # box-based tier split (not a reps-based one) is what guarantees this.
    prog_busy = {"skills": {
        s.id: {"box": 5, "seen": 6, "passes": 6, "fails": 0,
               "last_seen": now - 99999, "last_result": "pass",
               "best_efficiency": 1.0}
        for s in [mk("s%d" % i, 2) for i in range(8)]
    }}
    busy_skills = [mk("s%d" % i, 2) for i in range(8)] + [mk("fresh", 2)]
    sel = select_due_skills(busy_skills, prog_busy, count=10, new_gate=5, now=now)
    check("new skill still appears past a pile of due reviews",
          "fresh" in [s.id for s in sel], [s.id for s in sel])

    # A session must BLEND new with old review, not exhaust one side: with many
    # new skills available, a capped session should still include old practice.
    many_new = [mk("n%d" % i, 2) for i in range(20)]
    prog_old = {"skills": {
        "s%d" % i: {"box": 5, "seen": 6, "passes": 6, "fails": 0,
                    "last_seen": now - 99999, "last_result": "pass",
                    "best_efficiency": 1.0}
        for i in range(8)}}
    blend = select_due_skills([mk("s%d" % i, 2) for i in range(8)] + many_new,
                              prog_old, count=10, new_gate=5, now=now)
    ids = [s.id for s in blend]
    check("session blends new + old practice (not all new)",
          any(i.startswith("n") for i in ids) and any(i.startswith("s") for i in ids),
          ids)

    # A grooved skill (box-maxed AND >=25 reps) rests on the long maintenance
    # schedule, so it is NOT due right after being seen...
    grooved = {"box": 5, "passes": 30, "last_seen": now - 10}
    check("grooved skill not due right after seen", not is_due(grooved, now))
    # ...and once it drops out of maintenance (box below max), it returns to the
    # frequent box schedule (box 3 here rests 10 min).
    dropped = {"box": 3, "passes": 30, "last_seen": now - 1200}
    check("a skill below max box is back on frequent review",
          is_due(dropped, now))

    # --- graded outcomes: efficiency drives the box, not just pass/fail ----
    pg = {"skills": {}}
    store.record_result(pg, "great", correct=True, efficiency=1.0)
    check("great solve promotes and raises ease",
          pg["skills"]["great"]["box"] == 2 and pg["skills"]["great"]["ease"] > 1.0,
          pg["skills"]["great"])
    store.record_result(pg, "good", correct=True, efficiency=0.6)
    check("good solve promotes, ease neutral",
          pg["skills"]["good"]["box"] == 2 and pg["skills"]["good"]["ease"] == 1.0)
    store.record_result(pg, "slow", correct=True, efficiency=0.3)
    check("slow-but-correct holds the box and shortens ease",
          pg["skills"]["slow"]["box"] == 1 and pg["skills"]["slow"]["ease"] < 1.0)
    # a miss demotes a mid box; a fast solve's ease lengthens the next interval.
    for _ in range(3):
        store.record_result(pg, "mid", correct=True, efficiency=1.0)   # -> box 4
    box_before = pg["skills"]["mid"]["box"]
    store.record_result(pg, "mid", correct=False, efficiency=0.0)
    check("miss demotes a mid box and resets ease",
          pg["skills"]["mid"]["box"] == box_before - 1
          and pg["skills"]["mid"]["ease"] == 1.0)
    check("higher ease lengthens the interval",
          _due_interval({"box": 3, "ease": 2.0}) > _due_interval({"box": 3}))
    check("maintenance respects the 30-day cap even with max ease",
          _due_interval({"box": 5, "passes": 200, "ease": 2.5}) <= 2592000)

    # --- softened demotion: a grooved skill survives ONE slip (issue #3) ---
    pgr = {"skills": {}}
    e = store.get_entry(pgr, "g")
    e.update({"box": store.MAX_BOX, "passes": 30, "last_result": "pass"})
    store.record_result(pgr, "g", correct=False, efficiency=0.0)
    check("one miss at top box is forgiven (grace)",
          pgr["skills"]["g"]["box"] == store.MAX_BOX
          and pgr["skills"]["g"]["last_result"] == "fail")
    store.record_result(pgr, "g", correct=False, efficiency=0.0)
    check("a second consecutive miss finally demotes",
          pgr["skills"]["g"]["box"] == store.MAX_BOX - 1)

    # Leitner record_result promotes/demotes within [1,5].
    p = {"skills": {}}
    for _ in range(7):
        store.record_result(p, "z", True, 1.0)
    check("box caps at 5", p["skills"]["z"]["box"] == 5, p["skills"]["z"]["box"])
    for _ in range(7):
        store.record_result(p, "z", False, 0.0)
    check("box floors at 1", p["skills"]["z"]["box"] == 1, p["skills"]["z"]["box"])

    # outcome_passed needs correct AND efficient.
    r_ok = GradeResult(True, 2, 2, 1.0, [], None, None, True, False)
    r_sloppy = GradeResult(True, 20, 2, 0.1, [], None, None, True, False)
    r_wrong = GradeResult(False, 2, 2, 1.0, [], None, None, True, False)
    check("efficient correct passes", outcome_passed(r_ok))
    check("sloppy correct does not promote", not outcome_passed(r_sloppy))
    check("wrong never passes", not outcome_passed(r_wrong))

    # --- config: lesson toggle filter + escape-alias validation ------------
    from vimhjkl import config
    cfg = config._default()
    cfg_skills = [mk("a", 1), mk("b", 2), mk("c", 3)]
    check("all enabled by default",
          len(config.enabled_skills(cfg_skills, cfg)) == 3)
    config.set_disabled(cfg, "b", True)
    en = config.enabled_skills(cfg_skills, cfg)
    check("disabled skill filtered out",
          [s.id for s in en] == ["a", "c"], [s.id for s in en])
    config.set_disabled(cfg, "b", False)
    check("re-enable restores skill",
          len(config.enabled_skills(cfg_skills, cfg)) == 3)
    config.set_many_disabled(cfg, ["a", "c"], True)
    check("category-style bulk disable",
          [s.id for s in config.enabled_skills(cfg_skills, cfg)] == ["b"])
    # legacy escape_aliases in an old config file migrate into remaps on load.
    import json, tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "config.json"
    tmp.write_text(json.dumps({"escape_aliases": ["jk", "jj"], "remaps": []}))
    migrated = config.load(tmp)
    check("escape aliases migrate to <Esc> remaps on load",
          config.remaps(migrated) == [{"from": "jk", "to": "<Esc>", "mode": "i"},
                                       {"from": "jj", "to": "<Esc>", "mode": "i"}],
          config.remaps(migrated))

    # general key remaps: round-trip valid ones, drop the malformed.
    config.set_remaps(cfg, [
        {"from": "<C-p>", "to": "<Esc>", "mode": "i"},
        {"from": ";", "to": ":", "mode": "n"},
        {"from": "z", "mode": "i"},                 # no `to` -> dropped
        {"from": "a", "to": "b", "mode": "x"},       # bad mode -> dropped
        {"from": "<C-p>", "to": "<Esc>", "mode": "i"},  # dup -> deduped
    ])
    rms = config.remaps(cfg)
    check("remaps keep valid, drop malformed + dups",
          rms == [{"from": "<C-p>", "to": "<Esc>", "mode": "i"},
                  {"from": ";", "to": ":", "mode": "n"}], rms)

    # a remap that shadows a taught key hides that lesson; a comfort remap (and a
    # key the skill only lists as a sibling) hides nothing.
    from vimhjkl.engine import remap_blocks_skill, usable_skills
    sub = Skill(id="sub", title="sub", category="operator", teach="t",
                key_commands=["s", ";", "."],
                challenges=[Challenge(start=["x"], goal=["y"], par_keys=1,
                                      solution="f^sX<Esc>;.")])
    auto = Skill(id="auto", title="auto", category="text_object", teach="t",
                 key_commands=["<C-n>", "<C-p>"],   # lists <C-p>, but uses <C-n>
                 challenges=[Challenge(start=["x"], goal=["y"], par_keys=1,
                                       solution="A<C-n><Esc>")])
    check("remap of taught key blocks the lesson",
          remap_blocks_skill({"from": "s", "to": "x", "mode": "n"}, sub))
    check("escape remap blocks nothing",
          not remap_blocks_skill({"from": "jk", "to": "<Esc>", "mode": "i"}, sub))
    check("remap of an unused sibling key blocks nothing",
          not remap_blocks_skill({"from": "<C-p>", "to": "<Esc>", "mode": "i"}, auto))
    check("usable_skills drops blocked, keeps the rest",
          [s.id for s in usable_skills([sub, auto], [{"from": "s", "to": "x", "mode": "n"}])]
          == ["auto"])

    # --- blind-all sweep: endless + no-repeat (issue #4) -------------------
    from vimhjkl.cli import _blind_all_sweep, _record_blind_all_cleared
    from vimhjkl.engine import SessionSummary, AttemptRecord
    sweep_skills = [mk("a", 1), mk("b", 2), mk("c", 3), mk("d", 4)]
    p = {"skills": {}, "blind_all_cleared": ["a"]}
    pool, reset = _blind_all_sweep(sweep_skills, p)
    check("blind-all excludes already-cleared skill",
          {s.id for s in pool} == {"b", "c", "d"} and not reset,
          [s.id for s in pool])
    # a sweep that passes b and fails c records only b as cleared
    res_pass = GradeResult(True, 1, 1, 1.0, ["y"], None, None, True, False)
    res_fail = GradeResult(False, 5, 1, 0.2, ["x"], None, None, False, False)
    summ = SessionSummary()
    summ.attempts = [AttemptRecord(sweep_skills[1], sweep_skills[1].challenges[0], res_pass, True),
                     AttemptRecord(sweep_skills[2], sweep_skills[2].challenges[0], res_fail, False)]
    _record_blind_all_cleared(p, summ)
    check("passed skill recorded cleared, failed not",
          "b" in p["blind_all_cleared"] and "c" not in p["blind_all_cleared"],
          p["blind_all_cleared"])
    # clearing every skill resets the sweep to the full list
    p2 = {"skills": {}, "blind_all_cleared": ["a", "b", "c", "d"]}
    pool2, reset2 = _blind_all_sweep(sweep_skills, p2)
    check("full clear resets to a fresh sweep",
          len(pool2) == 4 and reset2 and p2["blind_all_cleared"] == [],
          (len(pool2), reset2))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
