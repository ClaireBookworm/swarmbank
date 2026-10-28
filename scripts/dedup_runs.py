"""One-time cleanup: move one copy of each large data file into the shared store, free the rest.

usage: dedup.py [--apply]   (dry run without --apply)
"""
import collections, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

APPLY = "--apply" in sys.argv
ROOT = Path.home() / ".local/share/quo-swarm"
RUNS, OBJ, INDEX = ROOT / "runs", ROOT / "data/objects", ROOT / "data/index.json"
MIN = 20 << 20
states = dict(sqlite3.connect(ROOT / "state/swarm.sqlite").execute("select id, state from runs"))
index = json.loads(INDEX.read_text()) if INDEX.is_file() else {}
URL = re.compile(r"https?://[^\s'\"<>()\\]+")


def published(p: Path, work: Path) -> bool:
    return any((d / "manifest.json.sig").exists() for d in p.parents if d != work and work in d.parents)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while b := f.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def urls_for(name: str, work: Path) -> set:
    found = set()
    for t in work.rglob("*"):
        if t.suffix in (".py", ".md", ".sh", ".txt", ".json", ".ipynb", ".yaml", ".toml") and t.is_file() \
                and t.stat().st_size < 2 << 20 and ".venv" not in t.parts:
            try:
                for u in URL.findall(t.read_text(errors="ignore")):
                    u = u.rstrip(".,;:`")
                    if name in u and "{" not in u:
                        found.add(u)
            except OSError:
                pass
    return found


files = []  # (path, run_state, work)
venvs = []
now = time.time()
for run in RUNS.iterdir():
    work = run / "work"
    st = states.get(run.name, "?")
    if not work.is_dir():
        continue
    for dirpath, dirnames, names in os.walk(work):
        d = Path(dirpath)
        if d.name == ".venv":
            if st in ("completed", "failed"):
                venvs.append(d)
            dirnames[:] = []
            continue
        for n in names:
            p = d / n
            try:
                s = p.stat()
            except OSError:
                continue
            if p.is_symlink() or s.st_size < MIN or p.suffix in (".so", ".dylib") or published(p, work):
                continue
            if st == "running" and now - s.st_mtime < 900:
                continue  # may still be written
            files.append((p, st, work, s.st_size))

groups = collections.defaultdict(list)
for i, (p, st, work, size) in enumerate(files):
    groups[sha(p)].append((p, st, work, size))
    print(f"\rhashed {i + 1}/{len(files)}", end="", file=sys.stderr)
print(file=sys.stderr)

freed = stored = cloned = 0
log = []
for digest, copies in groups.items():
    finished = [c for c in copies if c[1] in ("completed", "failed")]
    urls = set()
    for p, st, work, size in copies:
        urls |= urls_for(p.name, work)
    keep = len(copies) > 1 or urls  # duplicated, or a known download
    store = Path(index["sha256:" + digest]) if ("sha256:" + digest) in index else None
    if keep and store is None:
        src = (finished or copies)[0]
        store = OBJ / digest[:32] / src[0].name
        log.append(f"store {src[0].stat().st_size >> 20} MB {store.name} urls={sorted(urls)[:2]}")
        if APPLY:
            store.parent.mkdir(parents=True, exist_ok=True)
            if src in finished:
                shutil.move(src[0], store)
            else:
                subprocess.run(["cp", "-c", src[0], store], check=True)
            store.chmod(0o444)
        stored += 1
    if keep and APPLY:
        index["sha256:" + digest] = str(store)
        for u in urls:
            index.setdefault("url:" + u, str(store))
    for p, st, work, size in copies:
        if not p.exists() and APPLY:
            continue
        if st in ("completed", "failed"):
            log.append(f"delete {size >> 20} MB {p.relative_to(RUNS)}")
            freed += size
            if APPLY:
                p.unlink()
        elif keep and st == "running" and store and (not APPLY or store.exists()):
            log.append(f"clone  {size >> 20} MB {p.relative_to(RUNS)}")
            freed += size
            cloned += 1
            if APPLY:
                mode = p.stat().st_mode & 0o777
                tmp = p.with_name(p.name + ".dedup-tmp")
                subprocess.run(["cp", "-c", store, tmp], check=True)
                tmp.chmod(mode)
                tmp.replace(p)

venv_bytes = 0
for v in venvs:
    venv_bytes += int(subprocess.run(["du", "-sk", v], capture_output=True, text=True).stdout.split()[0]) << 10
    log.append(f"delete venv {v.relative_to(RUNS)}")
    if APPLY:
        shutil.rmtree(v)

if APPLY:
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=0))
    tmp.replace(INDEX)
    (ROOT / "state/dedup.log").write_text("\n".join(log) + "\n")
print("\n".join(l for l in log if l.startswith("store")))
print(f"files: {len(files)}  groups: {len(groups)}  stored: {stored}  clones: {cloned}")
print(f"freed: {freed / 2**30:.1f} GiB files + {venv_bytes / 2**30:.1f} GiB venvs ({len(venvs)})  apply={APPLY}")
