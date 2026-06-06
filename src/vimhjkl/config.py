"""User PREFERENCES (separate from progress.json player state and skills.json
curriculum).

Three stores, deliberately separate:
  * skills.json    — the curriculum (data, shipped in the package).
  * progress.json  — player MASTERY state (Leitner boxes, history).
  * config.json    — player PREFERENCES (which lessons are off, key mappings).

Preferences are local and never committed (see .gitignore).  Kept apart from
progress so wiping your stats never loses your setup, and vice versa.  Lives next
to progress.json: repo root in a source checkout, the XDG user data dir when
installed; override with ``$VIMHJKL_CONFIG``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import store


def _config_path() -> Path:
    env = os.environ.get("VIMHJKL_CONFIG")
    if env:
        return Path(env)
    # Sit beside progress.json wherever that resolves (checkout root or XDG).
    return store._progress_path().with_name("config.json")


CONFIG_PATH = _config_path()


def _default() -> dict:
    return {
        # Skill ids the player has switched OFF.  Excluded from every scheduling
        # decision (and from the belt/mastery maths) but still listed, greyed, in
        # the curriculum so they can be switched back on.
        "disabled_skills": [],
        # Key remaps, e.g. {"from": "<C-p>", "to": "<Esc>", "mode": "i"}.  Each
        # becomes a `{mode}noremap {from} {to}` injected into interactive drills, the
        # suggested solution is shown with YOUR key, and the typed key is folded back
        # to canonical so it costs the same as the original.  An escape remap is just
        # `{from} → <Esc>` in insert mode.  See cli.run_remaps.
        "remaps": [],
    }


def load(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    if not path.exists():
        return _default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default()
    # Merge over defaults so a config written by an older version still has every
    # key the current code expects.
    cfg = _default()
    if isinstance(data, dict):
        cfg.update({k: data[k] for k in cfg if k in data})
        # Migration: escape aliases used to be their own list; they're just remaps
        # to <Esc> in insert mode, so fold any old ones into `remaps`.
        for alias in data.get("escape_aliases", []):
            if isinstance(alias, str) and alias:
                cfg.setdefault("remaps", []).append(
                    {"from": alias, "to": "<Esc>", "mode": "i"})
    return cfg


def save(cfg: dict, path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    store._atomic_write(path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# disabled lessons
# ---------------------------------------------------------------------------

def disabled_set(cfg: dict) -> set[str]:
    return set(cfg.get("disabled_skills", []))


def is_disabled(cfg: dict, skill_id: str) -> bool:
    return skill_id in disabled_set(cfg)


def set_disabled(cfg: dict, skill_id: str, disabled: bool) -> None:
    """Switch one skill on/off (mutates ``cfg``; caller persists)."""
    cur = disabled_set(cfg)
    if disabled:
        cur.add(skill_id)
    else:
        cur.discard(skill_id)
    cfg["disabled_skills"] = sorted(cur)


def set_many_disabled(cfg: dict, skill_ids, disabled: bool) -> None:
    """Switch a whole group (e.g. a category) on/off at once."""
    cur = disabled_set(cfg)
    if disabled:
        cur |= set(skill_ids)
    else:
        cur -= set(skill_ids)
    cfg["disabled_skills"] = sorted(cur)


def enabled_skills(skills: list, cfg: dict) -> list:
    """The skills a session should actually schedule: everything not switched off.

    This is the SINGLE filter point — apply it before scheduling AND before the
    belt/mastery maths, so a disabled skill never drags the average down or stalls
    the unlock gate.  The full (unfiltered) list still feeds the curriculum view so
    disabled skills stay visible and re-enableable.
    """
    off = disabled_set(cfg)
    return [s for s in skills if s.id not in off]


# ---------------------------------------------------------------------------
# key remaps (any key, any mode — escape remaps are just `… → <Esc>` in insert)
# ---------------------------------------------------------------------------

_MODES = {"i", "n"}        # insert / normal — the two modes the drills exercise


def _valid_remap(r: dict) -> bool:
    return (isinstance(r, dict)
            and bool(r.get("from")) and bool(r.get("to"))
            and r.get("mode") in _MODES
            and "|" not in (r["from"] + r["to"]))   # | would break the map command


def remaps(cfg: dict) -> list[dict]:
    """The configured, validated key remaps (de-duplicated by from+mode).

    Each is ``{"from": <your key>, "to": <canonical key>, "mode": "i"|"n"}`` in vim
    notation (``<C-p>``, ``<Esc>``, ``;`` …)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in cfg.get("remaps", []):
        if not _valid_remap(r):
            continue
        key = (r["from"], r["mode"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"from": r["from"], "to": r["to"], "mode": r["mode"]})
    return out


def set_remaps(cfg: dict, rms: list[dict]) -> None:
    cfg["remaps"] = [{"from": r["from"], "to": r["to"], "mode": r["mode"]}
                     for r in rms if _valid_remap(r)]
