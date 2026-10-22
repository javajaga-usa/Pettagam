import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pettagam import panel

from test_pipeline import jpeg, write


@pytest.fixture
def server(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(panel, "HOME", str(home))
    monkeypatch.setattr(panel, "SETTINGS", str(home / "panel.json"))
    monkeypatch.setattr(panel, "DEFAULTS", dict(panel.DEFAULTS, db=str(home / "p.db"), report=str(home / "report")))
    monkeypatch.setattr(panel, "JOB", panel.Job())
    monkeypatch.setattr(panel.Handler, "token", None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


def call(base, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req) as r:
        data = r.read().decode()
        return json.loads(data) if "json" in r.headers.get("Content-Type", "") else data


def wait_job(base):
    for _ in range(200):
        st = call(base, "/api/state")
        if not st["job"]["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_full_flow_through_panel(server):
    base, tmp = server
    src = tmp / "old"
    write(str(src), "Trip/IMG_0001.JPG", jpeg("Apple", "iPhone 12"))
    write(str(src), "docs/Invoice_1.pdf", b"%PDF-1.7 hello")
    assert "<title>Pettagam</title>" in call(base, "/")
    call(base, "/api/settings", {"source": str(src), "library": str(tmp / "lib")})

    call(base, "/api/run", {"action": "dryrun"})
    st = wait_job(base)
    assert st["summary"]["planned"], st["job"]
    assert st["summary"]["actions"]["place"]["files"] == 2
    assert not (tmp / "lib").exists()
    assert "Pettagam dry-run report" in call(base, "/api/report")
    rows = call(base, "/api/plan?q=invoice")
    assert rows[0]["dest"] == "documents/invoices/Invoice_1.pdf"

    call(base, "/api/run", {"action": "apply"})
    st = wait_job(base)
    assert st["summary"]["placed"] == 2 and st["job"]["result"]["errors"] == 0
    call(base, "/api/run", {"action": "verify"})
    st = wait_job(base)
    assert st["job"]["result"]["ok"]


def test_browse_lists_folders(server):
    base, tmp = server
    (tmp / "a" / "b").mkdir(parents=True)
    d = call(base, f"/api/browse?path={tmp / 'a'}")
    assert [x["name"] for x in d["dirs"]] == ["b"]


def test_apply_before_dryrun_is_refused(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/run", {"action": "apply"})
    assert e.value.code == 400


def test_other_websites_cannot_trigger_steps(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/run", {"action": "dryrun"}, headers={"Origin": "https://evil.example"})
    assert e.value.code == 403


def test_network_mode_needs_token(server, monkeypatch):
    base, _ = server
    monkeypatch.setattr(panel.Handler, "token", "secret123")
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/state")
    assert e.value.code == 403
    assert "settings" in call(base, "/api/state?token=secret123")
    assert "settings" in call(base, "/api/state", headers={"Cookie": "pettagam=secret123"})


def test_explore_inbox_and_files(server):
    base, tmp = server
    src, inbox = tmp / "old", tmp / "inbox"
    write(str(src), "Trip/IMG_0001.JPG", jpeg("Apple", "iPhone 12"))
    write(str(src), "docs/Invoice_1.pdf", b"%PDF-1.7 hello")
    call(base, "/api/settings", {"source": str(src), "library": str(tmp / "lib"), "inbox": str(inbox)})
    call(base, "/api/run", {"action": "dryrun"})
    wait_job(base)
    call(base, "/api/run", {"action": "apply"})
    wait_job(base)

    d = call(base, "/api/explore?q=invoice")
    assert d["total"] == 1 and d["items"][0]["dest"] == "documents/invoices/Invoice_1.pdf"
    assert d["items"][0]["original"] == os.path.join("docs", "Invoice_1.pdf")
    assert call(base, "/api/explore?category=images")["total"] == 1
    inv = d["items"][0]
    on_day = call(base, "/api/explore?day=" + time.strftime("%m-%d", time.gmtime(inv["taken"] or inv["mtime"])))
    assert inv["dest"] in [it["dest"] for it in on_day["items"]]  # FR-FIND-5
    assert call(base, f"/api/copies?src=files&id={inv['id']}") == {"copies": [], "total": 0}  # FR-FIND-6
    assert call(base, "/api/copies?src=files&id=x")["total"] == 0
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/explore?day=99-99")
    assert e.value.code == 400
    f = call(base, "/api/facets")
    assert ["documents", 1] in f["categories"] and f["years"]
    assert call(base, "/api/file?path=documents/invoices/Invoice_1.pdf") == "%PDF-1.7 hello"
    for bad in ("../old/docs/Invoice_1.pdf", "/etc/passwd", "documents"):
        with pytest.raises(urllib.error.HTTPError) as e:
            call(base, "/api/file?path=" + bad)
        assert e.value.code == 404
    req = urllib.request.Request(base + "/api/file?path=documents/invoices/Invoice_1.pdf", headers={"Range": "bytes=2-4"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206 and r.read() == b"DF-"

    # Inbox needs a real folder; then new files are filed and show up in Explore.
    with pytest.raises(urllib.error.HTTPError):
        call(base, "/api/run", {"action": "inbox"})
    p = write(str(inbox), "Receipt_42.pdf", b"%PDF-1.7 receipt")
    os.utime(p, (time.time() - 60, time.time() - 60))
    call(base, "/api/run", {"action": "inbox"})
    st = wait_job(base)
    assert st["job"]["result"]["placed"] == 1, st["job"]
    assert call(base, "/api/explore?q=receipt")["items"][0]["dest"] == "documents/receipts/Receipt_42.pdf"
    call(base, "/api/run", {"action": "views"})
    assert wait_job(base)["job"]["result"]["Recent"] == 3
    assert call(base, "/api/estimate")["files"] == 3

    # Tags typed in the file details are saved and searchable at once (FR-TAG-6).
    it = call(base, "/api/explore?q=receipt")["items"][0]
    d = call(base, "/api/tags", {"src": it["src"], "id": it["id"], "tags": ["Tax 2026", "shop"],
                                 "description": "Hardware store"})
    assert d == {"tags": "tax 2026,shop", "description": "Hardware store"}
    assert call(base, "/api/explore?tag=tax 2026".replace(" ", "%20"))["total"] == 1
    assert call(base, "/api/explore?q=hardware")["total"] == 1
    for bad in ({"src": "sqlite_master", "id": 1, "tags": []}, {"src": "inbox", "id": "1", "tags": []},
                {"src": "inbox", "id": it["id"], "tags": "a,b"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            call(base, "/api/tags", bad)
        assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/tags", {"src": "inbox", "id": 999, "tags": ["x"]})
    assert e.value.code == 404


def test_doctor_service_and_undo_from_panel(server, monkeypatch):
    from pettagam import service
    base, tmp = server
    monkeypatch.setenv("HOME", str(tmp / "home"))
    calls = []
    monkeypatch.setattr(service, "RUNNER", lambda args, **kw: calls.append(args) or type("R", (), {"returncode": 0})())
    src, inbox = tmp / "old", tmp / "inbox"
    inbox.mkdir()
    write(str(src), "docs/Invoice_1.pdf", b"%PDF-1.7 hello")
    call(base, "/api/settings", {"source": str(src), "library": str(tmp / "lib"), "inbox": str(inbox)})
    names = {c["name"]: c["status"] for c in call(base, "/api/doctor")}
    assert names["Old files"] == "ok" and "Placement" in names

    st = call(base, "/api/service", {"on": True, "every": 600})
    assert st["installed"] and st["every"] == 600 and calls
    assert call(base, "/api/service")["installed"]
    assert not call(base, "/api/service", {"on": False})["installed"]

    call(base, "/api/run", {"action": "dryrun"})
    wait_job(base)
    call(base, "/api/run", {"action": "apply"})
    wait_job(base)
    assert (tmp / "lib" / "_meta" / "catalog.db").exists()
    st = call(base, "/api/stats")
    assert st["files"] == 1 and st["by_type"][0]["category"] == "documents"
    call(base, "/api/run", {"action": "fixity"})
    st = wait_job(base)
    assert st["job"]["result"]["recorded"] == 1 and st["job"]["result"]["damaged"] == []
    call(base, "/api/run", {"action": "undo"})
    st = wait_job(base)
    assert st["job"]["result"]["removed"] == 1 and st["summary"]["placed"] == 0
    assert (src / "docs" / "Invoice_1.pdf").exists()


def test_rules_editor(server):
    base, tmp = server
    lib = tmp / "lib"
    assert call(base, "/api/rules")["path"] is None
    call(base, "/api/settings", {"library": str(lib)})
    r = call(base, "/api/rules")
    assert not r["exists"] and '"Tax papers"' in r["text"]  # the starter examples, not saved yet
    bad = call(base, "/api/rules", {"text": '{"rules": [{"name": "x", "if": {"colour": "red"}, "then": "a"}]}'})
    assert not bad["ok"] and "colour" in bad["error"]
    assert not call(base, "/api/rules", {"text": "{nope"})["ok"]
    text = '{"rules": [{"name": "Tax", "if": {"ext": "pdf", "name": "*tax*"}, "then": "documents/tax"}]}'
    res = call(base, "/api/rules", {"text": text, "save": True})
    assert res["ok"] and res["rules"][0]["name"] == "Tax" and res["saved"].endswith("rules.json")
    assert call(base, "/api/rules")["exists"]
    assert not call(base, "/api/rules", {"text": '{"rules": [{"then": "../x"}]}', "save": True})["ok"]
    assert "Tax" in (lib / "_meta" / "rules.json").read_text()  # a bad save leaves the good file alone


# --- accessibility (USE-6) ----------------------------------------------------------

def _page():
    from importlib import resources
    return resources.files("pettagam").joinpath("panel.html").read_text(encoding="utf-8")


def test_panel_controls_have_names_and_work_from_the_keyboard():  # USE-6
    import re
    from html.parser import HTMLParser
    page = _page()

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth, self.unnamed = 0, []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "label":
                self.depth += 1
            if tag in ("input", "select", "textarea") and not self.depth and "aria-label" not in a:
                self.unnamed.append(a.get("id"))

        def handle_endtag(self, tag):
            if tag == "label":
                self.depth -= 1
    f = Fields()
    f.feed(page)
    assert not f.unnamed, f"form fields without a name for screen readers: {f.unnamed}"
    # Everything clickable is a real button (or link), so Tab, Enter and Space reach it.
    assert not re.search(r"<(div|span)[^>]*data-(path|item|tag)=", page)
    assert not re.search(r"<a [^>]*>\s*<button", page)  # no button inside a link
    assert ":focus-visible" in page and 'class="skip"' in page
    assert page.count('role="tabpanel"') == 2 and "ArrowRight" in page
    assert 'aria-live="polite"' in page and 'role="status"' in page


def test_panel_text_colours_are_readable():  # USE-6: WCAG 2.2 AA, 4.5:1 for normal text
    import re
    page = _page()

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)
    blocks = re.findall(r"(?:^:root|:root:not\(\[data-theme=\"light\"\]\)) \{([^}]*)\}", page, re.M)
    assert len(blocks) == 2  # light and dark
    for block in blocks:
        v = dict(re.findall(r"--([\w-]+): (#[0-9a-fA-F]{6})", block))
        for fg in ("ink", "muted", "accent", "ok", "warn", "bad"):
            for bg in ("bg", "card", "chip", "code", "bar"):
                assert ratio(v[fg], v[bg]) >= 4.5, (fg, bg, round(ratio(v[fg], v[bg]), 2))
        assert ratio(v["accent-ink"], v["accent"]) >= 4.5


def test_speed_check_from_panel(server, monkeypatch):  # FR-BENCH-2
    base, tmp = server
    monkeypatch.setattr(panel, "BENCH_SEARCH_ROWS", 2000)
    src, lib = tmp / "old", tmp / "lib"
    write(str(src), "a/IMG_1.JPG", jpeg("Apple", "iPhone 12"))
    lib.mkdir()
    call(base, "/api/settings", {"source": str(src), "library": str(lib)})
    call(base, "/api/run", {"action": "bench"})
    st = wait_job(base)
    lines = {line["name"]: line for line in st["job"]["result"]["lines"]}
    assert set(lines) == {"Scan", "Read", "Place", "Search"} and lines["Place"]["ok"] is True
    assert os.listdir(lib / "_meta") == [] and (src / "a" / "IMG_1.JPG").exists()
    assert 'id="btn-bench"' in call(base, "/")
