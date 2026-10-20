import os
import struct

import pytest

from pettagam import classify as cl
from pettagam.apply import apply
from pettagam.db import connect
from pettagam.hashing import find_duplicates
from pettagam.plan import _split, plan, safe_name
from pettagam.report import report
from pettagam.scan import scan
from pettagam.sniff import sniff_bytes
from pettagam.verify import verify

quiet = lambda *a, **k: None


def jpeg(make=None, model=None, w=4032, h=3024, pad=600_000):
    """Minimal JPEG: optional EXIF IFD0 with Make/Model, then a SOF0 with the size."""
    out = b"\xff\xd8"
    if make:
        entries, data = [], b""
        base_data = 8 + 2 + 12 * 2 + 4
        for tag, val in ((0x010F, make), (0x0110, model)):
            v = val.encode() + b"\x00"
            entries.append(struct.pack("<HHII", tag, 2, len(v), base_data + len(data)))
            data += v
        tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 2) + b"".join(entries) + b"\x00" * 4 + data
        app1 = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    sof = struct.pack(">BHHB", 8, h, w, 3) + b"\x00" * 9
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xda" + b"\x00" * pad
    return out


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" * 40


def write(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "old"
    s = str(src)
    write(s, "2019/Trip/IMG_0001.JPG", jpeg("Apple", "iPhone 12"))
    write(s, "backup/copy of trip/IMG_0001.JPG", jpeg("Apple", "iPhone 12"))  # duplicate
    write(s, "desktop/Screenshot 2024-05-01 at 10.00.png", png(2880, 1800))
    write(s, "misc/unnamed.png", png(1920, 1080))
    write(s, "misc/logo.png", png(256, 256))
    write(s, "docs/Invoice_2024_03.PDF", b"%PDF-1.7\n" + b"x" * 500)
    write(s, "docs/notes.txt", b"hello")
    write(s, "docs/budget.xlsx", b"PK\x03\x04" + b"xl/workbook.xml" + b"\x00" * 100)
    write(s, "docs/mystery", b"%PDF-1.4\n" + b"y" * 300)  # no extension
    write(s, "docs/bad:name?.pdf", b"%PDF-1.4\n" + b"z" * 300)
    write(s, "music/song.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x10TIT2\x00\x00\x00\x05\x00\x00\x00Song" + b"\x00" * 200)
    write(s, "video/VID_20240101.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300)
    write(s, "video/Screen Recording 1.mov", b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 300)
    write(s, "a/.DS_Store", b"junk")
    write(s, "a/._IMG_0001.JPG", b"junk")
    write(s, "a/empty.txt", b"")
    write(s, "code/app.py", b"print(1)")
    write(s, "weird/file.xyz", b"???")
    write(s, ".Spotlight-V100/store.db", b"index")
    return src, tmp_path / "library", tmp_path / "pettagam.db"


def run_all(src, lib, dbp, tmp_path):
    con = connect(str(dbp))
    scan(con, str(src), workers=4, progress=quiet)
    find_duplicates(con, progress=quiet)
    plan(con, str(lib), progress=quiet)
    report(con, str(tmp_path / "report"), progress=quiet)
    return con


def dest_of(con, rel):
    return con.execute("SELECT dest, action FROM files WHERE path=?", (rel,)).fetchone()


def test_dry_run_changes_nothing(tree, tmp_path):
    src, lib, dbp = tree
    before = sorted((p, os.stat(os.path.join(d, p)).st_mtime) for d, _, fs in os.walk(src) for p in fs)
    run_all(src, lib, dbp, tmp_path)
    after = sorted((p, os.stat(os.path.join(d, p)).st_mtime) for d, _, fs in os.walk(src) for p in fs)
    assert before == after
    assert not lib.exists()
    assert (tmp_path / "report" / "report.md").exists()
    assert (tmp_path / "report" / "plan.csv").exists()


def test_classification_and_naming(tree, tmp_path):
    src, lib, dbp = tree
    con = run_all(src, lib, dbp, tmp_path)
    j = os.path.join
    assert dest_of(con, j("2019", "Trip", "IMG_0001.JPG"))["dest"] == "images/photos/IMG_0001.jpg"
    assert dest_of(con, j("backup", "copy of trip", "IMG_0001.JPG"))["action"] == "duplicate"
    assert dest_of(con, j("desktop", "Screenshot 2024-05-01 at 10.00.png"))["dest"].startswith("images/screenshots/")
    assert dest_of(con, j("misc", "unnamed.png"))["dest"] == "images/screenshots/unnamed.png"
    assert dest_of(con, j("misc", "logo.png"))["dest"] == "images/graphics/logo.png"
    assert dest_of(con, j("docs", "Invoice_2024_03.PDF"))["dest"] == "documents/invoices/Invoice_2024_03.pdf"
    assert dest_of(con, j("docs", "budget.xlsx"))["dest"] == "documents/spreadsheets/budget.xlsx"
    assert dest_of(con, j("docs", "mystery"))["dest"] == "documents/general/mystery.pdf"
    assert dest_of(con, j("docs", "bad:name?.pdf"))["dest"] == "documents/general/bad_name_.pdf"
    assert dest_of(con, j("music", "song.mp3"))["dest"] == "audio/music/song.mp3"
    assert dest_of(con, j("video", "VID_20240101.mp4"))["dest"] == "videos/recordings/VID_20240101.mp4"
    assert dest_of(con, j("video", "Screen Recording 1.mov"))["dest"].startswith("videos/screen-recordings/")
    assert dest_of(con, j("code", "app.py"))["dest"] == "code/app.py"
    assert dest_of(con, j("weird", "file.xyz"))["dest"] == "other/xyz/file.xyz"
    for junk in (j("a", ".DS_Store"), j("a", "._IMG_0001.JPG"), j("a", "empty.txt")):
        assert dest_of(con, junk)["action"] == "skip"
    assert dest_of(con, j(".Spotlight-V100", "store.db")) is None


def test_apply_link_and_verify(tree, tmp_path):
    src, lib, dbp = tree
    con = run_all(src, lib, dbp, tmp_path)
    res = apply(con, workers=2, progress=quiet)
    assert res["method"] == "link" and res["errors"] == 0
    photo = lib / "images" / "photos" / "IMG_0001.jpg"
    assert os.stat(photo).st_ino == os.stat(src / "2019" / "Trip" / "IMG_0001.JPG").st_ino
    assert (lib / "_meta" / "manifest.csv").exists()
    v = verify(con, rescan=True, progress=quiet)
    assert v["ok"], v["problems"]
    # Re-running is safe and does nothing new.
    assert apply(con, workers=2, progress=quiet)["errors"] == 0


def test_apply_copy_and_detect_drift(tree, tmp_path):
    src, lib, dbp = tree
    con = run_all(src, lib, dbp, tmp_path)
    assert apply(con, method="copy", progress=quiet)["errors"] == 0
    assert os.stat(lib / "code" / "app.py").st_ino != os.stat(src / "code" / "app.py").st_ino
    assert verify(con, sample=100, rescan=True, progress=quiet)["ok"]
    write(str(src), "code/new.py", b"x = 2")
    with open(lib / "code" / "app.py", "wb") as f:
        f.write(b"tampered!")
    v = verify(con, sample=100, rescan=True, progress=quiet)
    assert not v["ok"]
    assert "code/app.py" in v["problems"]["mismatch"]
    assert os.path.join("code", "new.py") in v["problems"]["new"]


def test_existing_different_file_is_never_overwritten(tree, tmp_path):
    src, lib, dbp = tree
    con = run_all(src, lib, dbp, tmp_path)
    write(str(lib), "code/app.py", b"someone else's file")
    res = apply(con, progress=quiet)
    assert res["errors"] == 1
    assert open(lib / "code" / "app.py", "rb").read() == b"someone else's file"


def test_library_inside_source_is_refused(tree, tmp_path):
    src, _, dbp = tree
    con = connect(str(dbp))
    scan(con, str(src), progress=quiet)
    with pytest.raises(SystemExit):
        plan(con, str(src / "library"), progress=quiet)


def test_name_clashes_get_suffix(tmp_path):
    src = tmp_path / "old"
    write(str(src), "a/report.pdf", b"%PDF-1 a")
    write(str(src), "b/Report.pdf", b"%PDF-1 bb")
    write(str(src), "c/report.PDF", b"%PDF-1 ccc")
    con = run_all(src, tmp_path / "lib", tmp_path / "p.db", tmp_path)
    dests = sorted(r[0] for r in con.execute("SELECT dest FROM files"))
    assert dests == ["documents/reports/Report__2.pdf", "documents/reports/report.pdf",
                     "documents/reports/report__3.pdf"]


def test_large_folders_split_by_letter_then_range():
    names = [f"{c}{i}.jpg" for c in "abc" for i in range(40)]
    m = _split(names, 50)
    assert m["a0.jpg"] == "a/" and m["c39.jpg"] == "c/"
    camera = [f"IMG_{i:04d}.jpg" for i in range(120)]
    m = _split(camera, 50)
    folders = set(m.values())
    assert len(folders) == 3 and all(f.startswith("i/") for f in folders)
    assert len(_split(names, 1000)) == len(names) and set(_split(names, 1000).values()) == {""}


def test_sniffing():
    s = sniff_bytes(jpeg("Canon", "EOS R6", w=6000, h=4000, pad=10))
    assert (s.kind, s.width, s.height, s.camera) == ("jpg", 6000, 4000, "Canon EOS R6")
    assert sniff_bytes(png(1170, 2532)).width == 1170
    assert sniff_bytes(b"\x00\x00\x00\x18ftypheic").kind == "heic"
    assert sniff_bytes(b"<svg xmlns='x'></svg>").kind == "svg"


def test_safe_name_and_ext():
    assert safe_name("Photo.JPG", "jpg") == "Photo.jpg"
    assert safe_name("archive.tar.gz", "tar") == "archive.tar"
    assert safe_name("trailing. ", "") == "trailing"
    assert cl.normalize_ext("x.JPEG") == "jpg"
    assert cl.normalize_ext(".bashrc") == ""
