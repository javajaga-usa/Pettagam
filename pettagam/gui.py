"""Pettagam panel: a small window with one button per step.

  python3 -m pettagam.gui     (or double-click Pettagam.command on the Mac)

Each button runs the normal command line in the background and shows its
output, so the panel does exactly what the README steps do.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SETTINGS = Path.home() / ".pettagam-gui.json"
DEFAULTS = {
    "source": "",
    "library": "",
    "db": str(Path.home() / "pettagam.db"),
    "out": str(Path.home() / "pettagam-report"),
    "workers": "2",
}


def load_settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def open_path(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


class Panel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        root.title("Pettagam")
        root.minsize(720, 480)

        s = load_settings()
        self.vars = {k: tk.StringVar(value=v) for k, v in s.items() if k in DEFAULTS}

        form = ttk.Frame(root, padding=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        rows = [
            ("source", "Old files (read only)", "dir"),
            ("library", "New library", "dir"),
            ("db", "Catalog file", "file"),
            ("out", "Report folder", "dir"),
        ]
        for i, (key, label, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.vars[key]).grid(row=i, column=1, sticky="ew", padx=6)
            ttk.Button(form, text="Choose…", command=lambda k=key, t=kind: self.choose(k, t)).grid(row=i, column=2)
        ttk.Label(form, text="Parallel workers").grid(row=len(rows), column=0, sticky="w", pady=3)
        ttk.Spinbox(form, from_=1, to=16, width=5, textvariable=self.vars["workers"]).grid(
            row=len(rows), column=1, sticky="w", padx=6)

        bar = ttk.Frame(root, padding=(12, 0))
        bar.pack(fill="x")
        self.buttons = [
            ttk.Button(bar, text="1. Dry run", command=self.dryrun),
            ttk.Button(bar, text="2. Open report", command=self.open_report),
            ttk.Button(bar, text="3. Place files", command=self.apply),
            ttk.Button(bar, text="4. Verify", command=self.verify),
        ]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right")

        self.status = tk.StringVar(value="Ready. The old files are never changed.")
        ttk.Label(root, textvariable=self.status, padding=(12, 6)).pack(fill="x")

        logf = ttk.Frame(root, padding=(12, 0, 12, 12))
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=16, wrap="word", state="disabled")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.pump)

    # --- inputs ---------------------------------------------------------
    def choose(self, key: str, kind: str) -> None:
        cur = self.vars[key].get()
        if kind == "dir":
            path = filedialog.askdirectory(initialdir=cur or "/Volumes" if sys.platform == "darwin" else cur)
        else:
            path = filedialog.asksaveasfilename(initialfile=os.path.basename(cur) or "pettagam.db",
                                                initialdir=os.path.dirname(cur) or str(Path.home()),
                                                confirmoverwrite=False)
        if path:
            self.vars[key].set(path)

    def get(self, key: str) -> str:
        return os.path.expanduser(self.vars[key].get().strip())

    def save(self) -> None:
        try:
            SETTINGS.write_text(json.dumps({k: v.get() for k, v in self.vars.items()}, indent=2), encoding="utf-8")
        except OSError:
            pass

    def need(self, *keys: str) -> bool:
        names = {"source": "Old files", "library": "New library", "db": "Catalog file", "out": "Report folder"}
        missing = [names[k] for k in keys if not self.get(k)]
        if missing:
            messagebox.showwarning("Pettagam", "Please fill in: " + ", ".join(missing))
            return False
        return True

    # --- steps ----------------------------------------------------------
    def dryrun(self) -> None:
        if not self.need("source", "library", "db", "out"):
            return
        if not os.path.isdir(self.get("source")):
            messagebox.showwarning("Pettagam", "The old files folder doesn't exist. Is the drive plugged in?")
            return
        self.run("Dry run", ["dryrun", self.get("source"), "--library", self.get("library"),
                             "--workers", self.vars["workers"].get(), "--out", self.get("out")],
                 then=self.open_report)

    def open_report(self) -> None:
        rep = os.path.join(self.get("out"), "report.md")
        if os.path.exists(rep):
            open_path(self.get("out"))
            open_path(rep)
        else:
            messagebox.showinfo("Pettagam", "No report yet. Run the dry run first.")

    def apply(self) -> None:
        if not self.need("db"):
            return
        if not os.path.exists(self.get("db")):
            messagebox.showinfo("Pettagam", "No catalog yet. Run the dry run first.")
            return
        if not messagebox.askyesno("Place files",
                                   "Place the planned files into the library?\n\n"
                                   "The old files are not changed. You can stop and resume at any time."):
            return
        self.run("Placing files", ["apply", "--yes", "--workers", self.vars["workers"].get()])

    def verify(self) -> None:
        if not self.need("db"):
            return
        if not os.path.exists(self.get("db")):
            messagebox.showinfo("Pettagam", "No catalog yet. Run the dry run first.")
            return
        self.run("Verify", ["verify", "--rescan"])

    # --- process handling ----------------------------------------------
    def run(self, title: str, args: list[str], then=None) -> None:
        if self.proc:
            return
        self.save()
        cmd = [sys.executable, "-m", "pettagam", "--db", self.get("db"), *args]
        self.write(f"\n$ pettagam --db {self.get('db')} {' '.join(args)}\n")
        self.status.set(f"{title}…")
        self.set_busy(True)
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1, env=env)

        def reader(proc=self.proc):
            for line in proc.stdout:
                self.lines.put(line)
            self.lines.put((title, proc.wait(), then))

        threading.Thread(target=reader, daemon=True).start()

    def pump(self) -> None:
        try:
            while True:
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    title, code, then = item
                    self.proc = None
                    self.set_busy(False)
                    ok = code == 0
                    self.status.set(f"{title}: {'done' if ok else f'finished with problems (code {code})'}")
                    self.write(f"[{title} {'finished' if ok else f'exit code {code}'}]\n")
                    if ok and then:
                        then()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def write(self, text: str) -> None:
        # Progress lines often use \r; show each update on its own line.
        self.log.configure(state="normal")
        self.log.insert("end", text.replace("\r", "\n"))
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.status.set("Stopping… finished files are kept; run the step again to continue.")

    def close(self) -> None:
        if self.proc and not messagebox.askyesno("Pettagam", "A step is still running. Stop it and quit?"):
            return
        self.stop()
        self.save()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    Panel(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
