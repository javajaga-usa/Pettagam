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
