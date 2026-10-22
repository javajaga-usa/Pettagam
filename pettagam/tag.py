"""AI tagging: Claude looks at each photo or document in the library and gives it a few descriptive tags
(beach, birthday, receipt, passport...) and a one-line description, so you can search by what is in a
file, not just its name. Tags can also drive rules and views ({"tags": ["beach"]}, by-tag/{tag}).

Optional and paid: needs `python3 -m pip install anthropic` and an API key (ANTHROPIC_API_KEY or
`ant auth login`). Only files without tags are sent, so it can be stopped and resumed. Files are read
from the library, never from the old folder.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

MODEL = "claude-opus-5-5"
MAX_IMAGE = 5 * 1024 * 1024    # the API's per-image limit
MAX_PDF = 20 * 1024 * 1024
TEXT_BYTES = 8000
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
CONVERTIBLE = {"heic", "avif", "tiff", "bmp", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "raf"}
TEXT_TYPES = {"txt", "md", "csv", "tsv", "json", "yaml", "html", "rtf"}
# Rough cost per file in US dollars on the default model, for the estimate shown before tagging.
EST_COST = {"image": 0.008, "pdf": 0.02, "text": 0.004}

SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "3 to 8 short lowercase tags, most specific first"},
        "description": {"type": "string", "description": "one plain sentence, at most 20 words"},
    },
    "required": ["tags", "description"],
    "additionalProperties": False,
}

PROMPT = (
    "You are helping someone organise their personal files. Give this {what} 3 to 8 short, lowercase "
    "tags a person might search for later: what or who is in it, the place or event, the kind of document "
    "(receipt, passport, tax form, school report...), and any brand, shop or organisation named. Use plain "
    "single words or two-word phrases. Then write one plain sentence describing it. Its file name is "
    "\"{name}\"; use it as a hint only."
)


def kind_of(ext: str):
    if ext in IMAGE_TYPES or ext in CONVERTIBLE:
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext in TEXT_TYPES:
        return "text"
    return None


def _thumbnail(path: str):
    """A JPEG preview of a photo the API can't take as is (HEIC, RAW, too big), using macOS `sips`."""
    sips = shutil.which("sips")
    if not sips:
        return None
    fd, out = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run([sips, "-s", "format", "jpeg", "-Z", "1568", path, "--out", out],
                       check=True, capture_output=True, timeout=60)
        with open(out, "rb") as f:
            return f.read()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def content_for(path: str, ext: str):
    """Message content blocks for one file, or None when it can't be sent."""
    kind = kind_of(ext)
    size = os.path.getsize(path)
    if kind == "image":
        if ext in IMAGE_TYPES and size <= MAX_IMAGE:
            with open(path, "rb") as f:
                data, media = f.read(), IMAGE_TYPES[ext]
        else:
            data, media = _thumbnail(path), "image/jpeg"
            if not data:
                return None
        return [{"type": "image", "source": {"type": "base64", "media_type": media,
                                             "data": base64.standard_b64encode(data).decode()}}], "photo or image"
    if kind == "pdf":
        if size > MAX_PDF:
            return None
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": data}}], "document"
    if kind == "text":
        with open(path, "rb") as f:
            text = f.read(TEXT_BYTES).decode("utf-8", "replace")
        return [{"type": "text", "text": f"<file>\n{text}\n</file>"}], "text file"
    return None


def clean_tags(tags) -> list:
    out = []
    for t in tags if isinstance(tags, list) else []:
        t = " ".join(str(t).lower().replace(",", " ").split())[:40]
        if t and t not in out:
            out.append(t)
    return out[:8]


class Tagger:
    """Calls Claude for one file at a time. Swappable in tests."""

    def __init__(self, model: str = MODEL):
        try:
            import anthropic
        except ImportError:
            raise SystemExit("AI tagging needs the Anthropic library: python3 -m pip install anthropic") from None
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def __call__(self, blocks: list, what: str, name: str) -> dict:
        kwargs = dict(
            model=self.model,
            max_tokens=1024,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [*blocks, {"type": "text",
                                                              "text": PROMPT.format(what=what, name=name)}]}],
        )
        if not self.model.startswith("claude-haiku"):
            # If a safety check declines a file, let the API retry it on a suitable model.
            resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks="default", **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            raise ValueError("declined by the model")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return {"tags": data.get("tags"), "description": data.get("description", "")}


def _todo(con, retag: bool, only: str = None):
    where = "" if retag else " AND (tags IS NULL OR tags='')"
    rows = [("files", r["id"], r["dest"], r["ext"]) for r in con.execute(
        f"SELECT id, dest, ext FROM files WHERE action='place' AND status='done'{where} ORDER BY dest")]
    rows += [("inbox", r["id"], r["dest"], os.path.splitext(r["dest"])[1][1:].lower()) for r in con.execute(
        f"SELECT id, dest FROM inbox WHERE action='placed'{where} ORDER BY dest")]
    rows = [r for r in rows if kind_of(r[3])]
    if only:
        rows = [r for r in rows if r[2].lower().startswith(only.lower().strip("/") + "/")]
    return rows


def estimate(con, retag: bool = False, only: str = None) -> dict:
    rows = _todo(con, retag, only)
    by = {}
    for r in rows:
        k = kind_of(r[3])
        by[k] = by.get(k, 0) + 1
    return {"files": len(rows), "by_kind": by, "usd": round(sum(EST_COST[k] * n for k, n in by.items()), 2)}


def tag_library(con, library: str, tagger=None, workers: int = 4, limit: int = 0, retag: bool = False,
                only: str = None, model: str = MODEL, progress=print) -> dict:
    rows = _todo(con, retag, only)
    if limit:
        rows = rows[:limit]
    if not rows:
        progress("Nothing to tag: every photo and document already has tags.")
        return {"tagged": 0, "skipped": 0, "errors": 0}
    tagger = tagger or Tagger(model)
    progress(f"Tagging {len(rows):,} files with {getattr(tagger, 'model', 'AI')}")

    def one(r):
        table, fid, dest, ext = r
        try:
            got = content_for(os.path.join(library, dest), ext)
            if not got:
                return r, None, "can't be sent (too big, or a format this computer can't preview)"
            blocks, what = got
            return r, tagger(blocks, what, os.path.basename(dest)), None
        except Exception as e:  # one bad file must not stop the rest
            return r, None, str(e)[:200]

    t0, tagged, skipped, errors = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (r, res, err) in enumerate(pool.map(one, rows), 1):
            table, fid, dest, _ = r
            tags = clean_tags(res.get("tags")) if res else []
            if tags:
                con.execute(f"UPDATE {table} SET tags=?, description=? WHERE id=?",
                            (",".join(tags), str(res.get("description") or "")[:300], fid))
                tagged += 1
            elif err and err.startswith("can't be sent"):
                skipped += 1
            else:
                errors += 1
                progress(f"  {dest}: {err or 'no tags returned'}")
            if i % 50 == 0:
                con.commit()
                progress(f"  {i:,}/{len(rows):,} done ({i / (time.time() - t0):.1f}/s)")
    con.commit()
    progress(f"Tagged {tagged:,} files, skipped {skipped:,}, {errors:,} errors")
    return {"tagged": tagged, "skipped": skipped, "errors": errors}
