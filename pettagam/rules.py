"""Your own placement rules: conditions on a file, and the folder it goes to when they match.

Rules live in a JSON file, by default <library>/_meta/rules.json. The first rule that matches wins;
files that match no rule are placed by the built-in smart types in classify.py.

  {"rules": [
    {"name": "Tax papers", "if": {"ext": "pdf", "name": "*tax*"}, "then": "documents/tax/{year}"},
    {"name": "iPhone photos", "if": {"camera": "iphone"}, "then": "images/photos/iphone"},
    {"name": "Big videos", "if": {"category": "videos", "size_over": "1GB"}, "then": "videos/large"}
  ]}

Conditions: category, subtype, ext, kind, name, path (wildcards like *tax*), name_regex, camera,
size_over, size_under, older_than, newer_than, year, confidence_below, tags (set by `pettagam tag`).
Every condition in "if" must hold. Use {"any": [...]} for "or" and {"not": {...}} for "but not".
"""

import fnmatch
import json
import os
import re
import time
from dataclasses import dataclass

RULES_FILE = os.path.join("_meta", "rules.json")
BAD_FOLDER_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')  # same set as plan.BAD_CHARS, minus "/"
FIELDS = ("category", "subtype", "ext", "kind")
PLACEHOLDERS = {"category", "subtype", "ext", "year", "month", "camera"}
UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12}
AGES = {"d": 1, "w": 7, "m": 30.44, "y": 365.25}

STARTER = {
    "rules": [
        {"name": "Tax papers", "if": {"ext": "pdf", "name": ["*tax*", "*1099*", "*w-2*", "*w2*"]},
         "then": "documents/tax/{year}", "off": True},
        {"name": "iPhone photos by year", "if": {"subtype": "photos", "camera": "iphone"},
         "then": "images/photos/iphone/{year}", "off": True},
        {"name": "WhatsApp media", "if": {"any": [{"path": "*whatsapp*"}, {"name": ["IMG-*-WA*", "VID-*-WA*"]}]},
         "then": "{category}/whatsapp", "off": True},
        {"name": "Big videos", "if": {"category": "videos", "size_over": "1GB"}, "then": "videos/large", "off": True},
        {"name": "Old screenshots", "if": {"subtype": "screenshots", "older_than": "2y"},
         "then": "images/screenshots/old", "off": True},
    ]
}


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    name: str
    cond: dict
    then: str


def _size(v, where: str) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.fullmatch(r"\s*([\d.]+)\s*([kmgt]?b)?\s*", str(v).lower())
    if not m:
        raise RuleError(f"{where}: '{v}' is not a size like 500KB, 10MB or 1.5GB")
    return float(m.group(1)) * UNITS[m.group(2) or "b"]


def _age_days(v, where: str) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.fullmatch(r"\s*([\d.]+)\s*([dwmy])\s*", str(v).lower())
    if not m:
        raise RuleError(f"{where}: '{v}' is not an age like 30d, 6m or 2y")
    return float(m.group(1)) * AGES[m.group(2)]


def _years(v, where: str) -> set:
    out = set()
    for item in v if isinstance(v, list) else [v]:
        m = re.fullmatch(r"\s*(\d{4})\s*(?:-\s*(\d{4})\s*)?", str(item))
        if not m:
            raise RuleError(f"{where}: '{item}' is not a year like 2021 or a range like 2019-2021")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out |= set(range(min(a, b), max(a, b) + 1))
    return out


def _strings(v, where: str) -> list:
    items = v if isinstance(v, list) else [v]
    if not items or not all(isinstance(x, str) and x for x in items):
        raise RuleError(f"{where}: expected text or a list of text")
    return [x.lower() for x in items]


def compile_cond(c, where: str) -> dict:
    """Check a condition and turn it into a form that is quick to test."""
    if not isinstance(c, dict) or not c:
        raise RuleError(f"{where}: a condition must be a non-empty {{...}}")
    out = {}
    for key, v in c.items():
        w = f"{where} '{key}'"
        if key in FIELDS:
            out[key] = set(x.lstrip(".") if key == "ext" else x for x in _strings(v, w))
        elif key in ("name", "path"):
            out[key] = _strings(v, w)
        elif key == "tags":
            out[key] = v if isinstance(v, bool) else set(x.strip() for x in _strings(v, w))
        elif key == "name_regex":
            try:
                out[key] = re.compile(v, re.I)
            except (re.error, TypeError) as e:
                raise RuleError(f"{w}: {e}") from None
        elif key == "camera":
            out[key] = v if isinstance(v, bool) else _strings(v, w)
        elif key in ("size_over", "size_under"):
            out[key] = _size(v, w)
        elif key in ("older_than", "newer_than"):
            out[key] = _age_days(v, w)
        elif key == "year":
            out[key] = _years(v, w)
        elif key == "confidence_below":
            if not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise RuleError(f"{w}: expected a number between 0 and 1")
            out[key] = float(v)
        elif key in ("any", "all"):
            if not isinstance(v, list) or not v:
                raise RuleError(f"{w}: expected a list of conditions")
            out[key] = [compile_cond(x, f"{w} #{i + 1}") for i, x in enumerate(v)]
        elif key == "not":
            out[key] = compile_cond(v, w)
        else:
            raise RuleError(f"{where}: unknown condition '{key}'. Known: " + ", ".join(
                sorted([*FIELDS, "name", "path", "tags", "name_regex", "camera", "size_over", "size_under",
                        "older_than", "newer_than", "year", "confidence_below", "any", "all", "not"])))
    return out


def _check_template(t, where: str) -> str:
    if not isinstance(t, str) or not t.strip("/ "):
        raise RuleError(f"{where}: 'then' must be a folder such as documents/tax/{{year}}")
    for name in re.findall(r"{([^{}]*)}", t):
        if name not in PLACEHOLDERS:
            raise RuleError(f"{where}: unknown placeholder {{{name}}}. Known: "
                            + ", ".join(f"{{{p}}}" for p in sorted(PLACEHOLDERS)))
    if t.startswith("/") or ".." in t.split("/") or t.split("/")[0].lower() in ("_meta", "views", ""):
        raise RuleError(f"{where}: 'then' must be a folder inside the library, like images/family")
    return t.strip("/")


def parse_rules(data) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuleError('the file must look like {"rules": [ ... ]}')
    rules, names = [], set()
    for i, r in enumerate(data["rules"], 1):
        if not isinstance(r, dict):
            raise RuleError(f"rule #{i}: expected {{...}}")
        name = str(r.get("name") or f"rule {i}")
        where = f"rule '{name}'"
        unknown = set(r) - {"name", "if", "then", "off", "note"}
        if unknown:
            raise RuleError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}; use name, if, then, off, note")
        if name.lower() in names:
            raise RuleError(f"{where}: two rules have this name")
        names.add(name.lower())
        cond, then = compile_cond(r.get("if"), where), _check_template(r.get("then"), where)
        if not r.get("off"):
            rules.append(Rule(name, cond, then))
    return rules


def load_rules(path: str | None) -> list:
    """Rules from a JSON file; a missing file means no rules."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise RuleError(f"{path} is not valid JSON: {e}") from None
    try:
        return parse_rules(data)
    except RuleError as e:
        raise RuleError(f"{path}: {e}") from None


def default_path(library: str | None) -> str | None:
    return os.path.join(library, RULES_FILE) if library else None


def write_starter(path: str) -> bool:
    """Create a starter file with example rules switched off. Never replaces an existing file."""
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(STARTER, f, indent=2)
        f.write("\n")
    return True


def facts(row, category, subtype, confidence) -> dict:
    """What conditions can look at, from a catalog row (or anything with the same keys)."""
    return {"name": row["name"], "path": row["path"], "ext": row["ext"] or "", "kind": row["kind"] or "",
            "size": row["size"], "mtime": row["mtime"], "taken": _get(row, "taken"), "camera": row["camera"] or "",
            "category": category or "", "subtype": subtype or "", "confidence": confidence,
            "tags": set(t for t in (_get(row, "tags") or "").split(",") if t)}


def _get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def when(f) -> float:
    """The file's date: when the photo was taken if the camera recorded it, else when it was last changed."""
    return f["taken"] or f["mtime"]


def date_parts(f) -> time.struct_time:
    # EXIF times have no time zone and are stored as if UTC, so read them back the same way.
    return time.gmtime(f["taken"]) if f["taken"] else time.localtime(f["mtime"])


def matches(c: dict, f: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    for key, v in c.items():
        if key in FIELDS:
            ok = f[key].lower() in v
        elif key == "name":
            ok = any(fnmatch.fnmatchcase(f["name"].lower(), p) for p in v)
        elif key == "path":
            p = f["path"].replace(os.sep, "/").lower()
            ok = any(fnmatch.fnmatchcase(p, g) for g in v)
        elif key == "name_regex":
            ok = bool(v.search(f["name"]))
        elif key == "tags":
            ok = bool(f["tags"]) == v if isinstance(v, bool) else bool(v & f["tags"])
        elif key == "camera":
            cam = f["camera"].lower()
            ok = bool(cam) == v if isinstance(v, bool) else any(x in cam for x in v)
        elif key == "size_over":
            ok = f["size"] > v
        elif key == "size_under":
            ok = f["size"] < v
        elif key == "older_than":
            ok = now - when(f) > v * 86400
        elif key == "newer_than":
            ok = now - when(f) < v * 86400
        elif key == "year":
            ok = date_parts(f).tm_year in v
        elif key == "confidence_below":
            ok = f["confidence"] is not None and f["confidence"] < v
        elif key == "any":
            ok = any(matches(x, f, now) for x in v)
        elif key == "all":
            ok = all(matches(x, f, now) for x in v)
        else:  # not
            ok = not matches(v, f, now)
        if not ok:
            return False
    return True


def render(template: str, f: dict) -> str:
    t = date_parts(f)
    values = {"category": f["category"] or "other", "subtype": f["subtype"] or "general",
              "ext": f["ext"] or "no-extension", "year": f"{t.tm_year}", "month": f"{t.tm_mon:02d}",
              "camera": f["camera"] or "unknown-camera"}
    out = re.sub(r"{(\w+)}", lambda m: values[m.group(1)], template)
    parts = [BAD_FOLDER_CHARS.sub("_", p).strip(" .") or "_" for p in out.split("/") if p.strip()]
    return "/".join(parts)


def first_match(rules: list, f: dict, now: float | None = None):
    """(rule, folder) for the first rule that matches, else (None, None)."""
    for r in rules:
        if matches(r.cond, f, now):
            return r, render(r.then, f)
    return None, None
