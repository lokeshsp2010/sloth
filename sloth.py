#!/usr/bin/env python3
"""
sloth.py - describe a chore in English. Local open models do the thinking. You press y once.

Zero new downloads. Uses what is already on this PC:
  llama3.1:8b  -> writes the script        hermes3:3b        -> fast per-file judgement
  nomic-embed  -> semantic folder matching  RestrictedPython  -> script jail
  pypdf / docx2txt / rapidocr -> reads PDFs, Word files, screenshots (all offline)
  watchdog -> true "walk away" mode         rich -> pretty preview

Use:
  python sloth.py "move PDFs older than 7 days from Downloads into Archive by month"
  python sloth.py "sort Documents into Resumes, Invoices, Study notes by content" --save docs
  python sloth.py --habit docs --yes                  # rerun, no model, no prompt
  python sloth.py --watch Downloads --habit docs      # sloth sleeps, folder tidies itself
  python sloth.py --bench "sort Downloads by file type"   # compare local models (markdown table)
  python sloth.py --undo                              # reverse last run (stackable)
  python sloth.py --list
"""
import argparse, builtins, datetime, json, operator, os, pathlib, re, shutil, sys, time, types

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence, guarded_unpack_sequence, safer_getattr
from RestrictedPython.PrintCollector import PrintCollector

MODEL = os.environ.get("SLOTH_MODEL", "llama3.1:8b-instruct-q4_K_M")  # writes the script
ASK_MODEL = os.environ.get("SLOTH_ASK", "hermes3:3b")                 # per-file judgement
EMBED = os.environ.get("SLOTH_EMBED", "nomic-embed-text")             # semantic matching
BENCH = os.environ.get(
    "SLOTH_BENCH", "llama3.1:8b-instruct-q4_K_M,mistral:latest,hermes3:3b,llama3.2:1b").split(",")

MARGIN = float(os.environ.get("SLOTH_MARGIN", "0.03"))  # raise it = more files go to Review

HOME = pathlib.Path.home().resolve()
STATE = HOME / ".sloth"
HABITS, TRASH, UNDO = STATE / "habits", STATE / "trash", STATE / "undo.json"
for _d in (HABITS, TRASH):
    _d.mkdir(parents=True, exist_ok=True)

FENCE = "`" * 3
JUNK = {"desktop.ini", "thumbs.db", ".ds_store"}
SELF = pathlib.Path(__file__).resolve()
PARTIAL = {".crdownload", ".part", ".tmp", ".download", ".opdownload"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".html", ".log", ".rtf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
KINDS = {
    "Images": ".jpg .jpeg .png .gif .webp .bmp .svg .heic",
    "Videos": ".mp4 .mkv .mov .avi .webm",
    "Audio": ".mp3 .wav .m4a .flac .ogg",
    "Documents": ".pdf .doc .docx .txt .md .rtf .odt .ppt .pptx .xls .xlsx .csv",
    "Archives": ".zip .rar .7z .tar .gz .xz .bz2",
    "Code": ".py .js .jsx .ts .tsx .html .htm .css .json .ipynb .java .c .cpp .ps1 .bat",
    "Installers": ".exe .msi .apk .iso .dmg",
}

SYSTEM = """You write a short Python script for a file chore. Reply with ONLY one @@@python block.
No imports. No print. Use f-strings (never .format). Available names:
  sloth.ls(folder, pattern="*", recursive=False) -> list of file Paths
  sloth.age_days(f)  sloth.size_mb(f)  sloth.month(f) -> "2026-10"  sloth.kind(f) -> "Images"/"Documents"/"Videos"/"Audio"/"Archives"/"Code"/"Installers"/"Other"
  sloth.read(f, chars=1500) -> text inside PDF/Word/text/image(OCR), "" if none
  sloth.match(f, {"Label A": "keywords", "Label B": "keywords"}, other="Review") -> best label by MEANING of the file
  sloth.ask(question) -> short answer from a small local AI (use for titles and yes/no)
  sloth.move(f, folder)   sloth.rename(f, new_name)   sloth.trash(f)
  Path, re, datetime
Handle EVERY file in the folder, not just some. Paths look like Path.home() / "Downloads". Never delete: use sloth.trash. Max 20 lines.
Folders are created automatically: never mkdir. Put new folders INSIDE the folder being sorted
(d / "Images") unless the chore names another place. For sloth.match, give each label a short
description in a dict, and pass other="Review" so unsure files are not forced into a wrong folder.

Chore: move PDFs older than 7 days from Downloads into Archive by month
@@@python
d = Path.home() / "Downloads"
for f in sloth.ls(d, "*.pdf"):
    if sloth.age_days(f) > 7:
        sloth.move(f, d / "Archive" / sloth.month(f))
@@@

Chore: sort loose files in Documents into Resumes, Invoices and Study notes by what they say
@@@python
d = Path.home() / "Documents"
topics = {"Resumes": "CV, work experience, skills, education", "Invoices": "invoice, bill, receipt, amount due, payment", "Study notes": "lecture notes, chapter, homework, exam"}
for f in sloth.ls(d):
    sloth.move(f, d / sloth.match(f, topics, other="Review"))
@@@

Chore: sort everything in Downloads by file type
@@@python
d = Path.home() / "Downloads"
for f in sloth.ls(d):
    sloth.move(f, d / sloth.kind(f))
@@@

Chore: give every screenshot in Pictures a short title based on its text
@@@python
for f in sloth.ls(Path.home() / "Pictures", "Screenshot*"):
    t = sloth.read(f)
    if t:
        sloth.rename(f, sloth.ask(f"Write a 4 word file title for this text: {t}"))
@@@""".replace("@@@", FENCE)

# ---------- the jail: model's script can only *plan*, never touch disk ----------

DENY = {"write_text", "write_bytes", "unlink", "rmdir", "rename", "replace", "open", "touch",
        "mkdir", "chmod", "lchmod", "symlink_to", "hardlink_to", "link_to", "read_text", "read_bytes"}
_INPLACE = {"+=": operator.iadd, "-=": operator.isub, "*=": operator.imul, "/=": operator.itruediv,
            "//=": operator.ifloordiv, "%=": operator.imod, "|=": operator.ior, "&=": operator.iand}
BUILTINS = dict(safe_builtins)
BUILTINS.update({k: getattr(builtins, k) for k in
                 "min max sum enumerate any all reversed list dict set map filter".split()})
_re = types.SimpleNamespace(sub=re.sub, search=re.search, match=re.match, findall=re.findall,
                            split=re.split, I=re.I)
_dt = types.SimpleNamespace(datetime=datetime.datetime, timedelta=datetime.timedelta, date=datetime.date)


def _getattr(obj, name, *default):
    if name in DENY and isinstance(obj, pathlib.PurePath):
        if name == "mkdir":  # folders are created automatically on apply
            return lambda *a, **k: None
        raise AttributeError(f"Path.{name} is not allowed. Use sloth.* helpers")
    return safer_getattr(obj, name, *default)


def inside(p):
    p = pathlib.Path(p).resolve()
    return p == HOME or HOME in p.parents


_OCR = None


def _ocr(p):
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    res, _ = _OCR(str(p))
    return " ".join(r[1] for r in res) if res else ""


def llm(messages, model=MODEL, temp=0.1):
    import ollama
    return ollama.chat(model=model, messages=messages, options={"temperature": temp})["message"]["content"]


class Sloth:
    """The ONLY hands the model's script gets. Underscore names are invisible to it."""

    def __init__(self):
        self._ops, self._asks, self._labels = [], {}, {}

    def _ok(self, p):
        p = pathlib.Path(p).expanduser().resolve()
        if not inside(p):
            raise PermissionError(f"outside home folder: {p}")
        return p

    def ls(self, folder, pattern="*", recursive=False):
        f = self._ok(folder)
        it = f.rglob(pattern) if recursive else f.glob(pattern)
        return sorted(p for p in it if p.is_file() and not p.name.startswith(".")
                      and p.suffix.lower() not in PARTIAL and p.name.lower() not in JUNK
                      and p.resolve() != SELF and STATE not in p.parents)[:500]

    def age_days(self, p):
        return (time.time() - self._ok(p).stat().st_mtime) / 86400

    def size_mb(self, p):
        return self._ok(p).stat().st_size / 1_048_576

    def month(self, p):
        return datetime.datetime.fromtimestamp(self._ok(p).stat().st_mtime).strftime("%Y-%m")

    def kind(self, p):
        ext = self._ok(p).suffix.lower()
        return next((k for k, v in KINDS.items() if ext in v.split()), "Other")

    def read(self, p, chars=1500):
        p = self._ok(p)
        ext = p.suffix.lower()
        try:
            if ext == ".pdf":
                from pypdf import PdfReader
                t = " ".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:2])
            elif ext == ".docx":
                import docx2txt
                t = docx2txt.process(str(p))
            elif ext in IMG_EXT:
                t = _ocr(p)
            elif ext in TEXT_EXT:
                t = p.read_text(errors="ignore")
            else:
                return ""
        except Exception:
            return ""
        return re.sub(r"\s+", " ", t)[:chars]

    def ask(self, q):
        if q not in self._asks:
            self._asks[q] = llm([{"role": "user", "content": q + "\nAnswer in 5 words max, no quotes."}],
                                ASK_MODEL, 0).strip()
        return self._asks[q]

    def _embed(self, texts):
        import numpy as np, ollama
        e = np.array(ollama.embed(model=EMBED, input=texts)["embeddings"], dtype=float)
        return e / np.linalg.norm(e, axis=1, keepdims=True)

    def match(self, p, labels, other=None):
        import numpy as np
        names = [str(k) for k in labels]
        docs = [f"{k}: {v}" for k, v in labels.items()] if isinstance(labels, dict) else names
        key = tuple(docs)
        if key not in self._labels:
            self._labels[key] = self._embed(["classification: " + x for x in docs])
        p = self._ok(p)
        text = self.read(p, 1200) or re.sub(r"[_\-.]+", " ", p.stem)
        v = self._embed([f"classification: {p.name}. {text}"])[0]
        sims = self._labels[key] @ v
        order = np.argsort(sims)[::-1]
        if other and len(order) > 1 and sims[order[0]] - sims[order[1]] < MARGIN:
            return str(other)  # too close to call: do not guess
        return names[int(order[0])]

    def move(self, p, folder):
        s = self._ok(p)
        self._ops.append(("move", s, self._ok(folder) / s.name))

    def rename(self, p, new_name):
        s = self._ok(p)
        n = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\r\n]', " ", str(new_name))).strip(" .")[:100]
        if n and not pathlib.Path(n).suffix:
            n += s.suffix
        if n:
            self._ops.append(("rename", s, s.with_name(n)))

    def trash(self, p):
        s = self._ok(p)
        self._ops.append(("trash", s, TRASH / f"{int(time.time())}_{s.name}"))


def plan(code):
    hands = Sloth()
    g = {"__builtins__": BUILTINS, "_getattr_": _getattr, "_getiter_": default_guarded_getiter,
         "_getitem_": default_guarded_getitem, "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
         "_unpack_sequence_": guarded_unpack_sequence, "_write_": lambda x: x,
         "_inplacevar_": lambda op, x, y: _INPLACE[op](x, y), "_print_": PrintCollector,
         "sloth": hands, "Path": pathlib.Path, "re": _re, "datetime": _dt}
    exec(compile_restricted(code, "<chore>", "exec"), g)
    return hands._ops


def clean(raw):
    m = re.search(FENCE + r"(?:python)?\n(.*?)" + FENCE, raw, re.S)
    code = m.group(1) if m else raw
    code = re.sub(r"^\s*(import|from)\s+.*$", "", code, flags=re.M)  # names are pre-provided
    return re.sub(r"^\s*Script:\s*", "", code).strip("\n")


def think(chore, model=MODEL, tries=3):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Chore: " + chore}]
    err, last = None, None
    for i in range(tries):
        code = clean(llm(msgs, model))
        try:
            ops = plan(code)
            if not ops and i == 0 and tries > 1:  # empty plan is usually a wrong path or pattern
                print("  plan was empty. asking model to look again...", flush=True)
                last = (code, ops)
                msgs += [{"role": "assistant", "content": f"{FENCE}python\n{code}\n{FENCE}"},
                         {"role": "user", "content": "That script produced zero actions. Check the folder path "
                          "and patterns, and handle EVERY file in the folder. One python block only."}]
                continue
            return code, ops
        except Exception as e:
            err = e
            print(f"  model slipped: {str(e)[:90]}. retry...", flush=True)
            msgs += [{"role": "assistant", "content": f"{FENCE}python\n{code}\n{FENCE}"},
                     {"role": "user", "content": f"Error: {e}. Fix it. One python block only."}]
    if last:
        return last
    raise RuntimeError(f"gave up: {err}")


# ---------- apply / undo ----------

def unique(d):
    n, cand = 1, d
    while cand.exists():
        cand = d.with_name(f"{d.stem}_{n}{d.suffix}")
        n += 1
    return cand


def apply(ops):
    done = []
    for _, s, d in ops[:2000]:
        try:
            if not (inside(s) and inside(d)) or not s.exists():
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            d = unique(d)
            shutil.move(str(s), str(d))
            done.append([str(s), str(d)])
        except Exception as e:
            print(f"  skipped {s.name}: {e}")
    if done:
        runs = json.loads(UNDO.read_text()) if UNDO.exists() else []
        runs.append(done)
        UNDO.write_text(json.dumps(runs[-20:]))
    print(f"done. {len(done)} moved. undo: python sloth.py --undo", flush=True)


def undo():
    runs = json.loads(UNDO.read_text()) if UNDO.exists() else []
    if not runs:
        return print("nothing to undo.")
    n = 0
    for s, d in reversed(runs.pop()):
        s, d = pathlib.Path(s), pathlib.Path(d)
        if d.exists() and not s.exists():
            s.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(s))
            n += 1
    UNDO.write_text(json.dumps(runs))
    print(f"undone. {n} restored. {len(runs)} earlier run(s) still undoable.")


def coverage(ops):
    srcs = {s for _, s, _ in ops}
    for parent in {s.parent for s in srcs}:
        left = [q for q in parent.iterdir() if q.is_file() and q not in srcs and q.resolve() != SELF
                and not q.name.startswith(".") and q.name.lower() not in JUNK]
        if left:
            print(f"HEADS UP: {len(left)} other file(s) in '{parent.name}' are NOT touched by this plan.")


def preview(ops):
    from rich import box
    from rich.console import Console
    from rich.table import Table
    t = Table(title=f"plan: {len(ops)} action(s)", box=box.ASCII)
    for c in ("action", "file", "goes to"):
        t.add_column(c)
    for op, s, d in ops[:30]:
        t.add_row(op, s.name, str(d.relative_to(HOME)))
    Console().print(t)
    if len(ops) > 30:
        print(f"...and {len(ops) - 30} more")


# ---------- bench + watch ----------

def bench(chore):
    print(f"Chore: `{chore}`\n\n| model | time | result |\n|---|---|---|", flush=True)
    for m in BENCH:
        t = time.time()
        try:
            _, ops = think(chore, m.strip(), tries=1)
            res = f"OK, {len(ops)} action(s)" if ops else "ran, 0 actions"
        except Exception as e:
            res = "FAIL: " + str(e)[:70].replace("|", "/").replace("\n", " ")
        print(f"| {m.strip()} | {time.time() - t:.0f}s | {res} |", flush=True)


def resolve_folder(arg):
    for c in (pathlib.Path(arg).expanduser(), HOME / arg):
        if c.is_dir() and inside(c):
            return c.resolve()
    sys.exit(f"folder not found: {arg}. Try a full path like {HOME / 'Downloads'}")


def watch(folder, habit):
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    code = (HABITS / f"{habit}.py").read_text()
    last = [0.0]

    class H(FileSystemEventHandler):
        def on_created(self, e):
            last[0] = time.time()
        on_moved = on_created

    obs = Observer()
    obs.schedule(H(), str(folder), recursive=False)
    obs.start()
    print(f"sloth is napping on {folder}. habit '{habit}'. Ctrl+C to wake.", flush=True)
    try:
        while True:
            time.sleep(2)
            if last[0] and time.time() - last[0] > 10:  # wait for downloads to settle
                last[0] = 0
                ops = plan(code)
                if ops:
                    apply(ops)
    except KeyboardInterrupt:
        obs.stop()


def main():
    ap = argparse.ArgumentParser(description="Lazy file chores via local AI")
    ap.add_argument("chore", nargs="*")
    ap.add_argument("--habit", help="run a saved habit (no model call)")
    ap.add_argument("--save", help="save this script as a habit")
    ap.add_argument("--yes", action="store_true", help="skip the y/N prompt")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--watch", help="folder to watch (needs --habit)")
    ap.add_argument("--bench", metavar="CHORE", help="compare local models on one chore")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--show", action="store_true", help="print the script the model wrote")
    a = ap.parse_args()

    if a.undo:
        return undo()
    if a.list:
        return print("\n".join(p.stem for p in HABITS.glob("*.py")) or "no habits yet.")
    if a.bench:
        return bench(a.bench)
    if a.watch:
        if not a.habit:
            ap.error("--watch needs --habit NAME")
        return watch(resolve_folder(a.watch), a.habit)
    try:
        if a.habit:
            code = (HABITS / f"{a.habit}.py").read_text()
            ops = plan(code)
        elif a.chore:
            code, ops = think(" ".join(a.chore), a.model)
        else:
            ap.error("give a chore, or --habit NAME")
    except RuntimeError as e:
        sys.exit(f"{e}\nTry another model: python sloth.py \"...\" --model mistral:latest")

    if a.show or not ops:
        print(f"\n--- script ---\n{code}\n--------------")
    if not ops:
        return print("nothing to do. sloth approves.")
    preview(ops)
    coverage(ops)
    if a.yes or input("\ngo? [y/N] ").strip().lower() == "y":
        apply(ops)
        if a.save:
            (HABITS / f"{a.save}.py").write_text(code)
            print(f"saved habit '{a.save}'.")
    else:
        print("cancelled. nothing touched.")


if __name__ == "__main__":
    main()