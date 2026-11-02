"""Free disk space when a run finishes: its scratch folder, Python environments, and large files.

Published packages (folders with manifest.json.sig) and everything under 20 MB in the
working directory are kept, so code and results stay inspectable. Large data files are
public downloads or derived caches; shared copies live in the data store.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config

LARGE = 20 << 20


def _published(path: Path, work: Path) -> bool:
    return any((d / "manifest.json.sig").exists() for d in path.parents if work in d.parents)


def prune_run(run_id: str) -> int:
    """Delete a finished run's bulky files. Returns bytes freed."""
    run = config.RUNS / run_id
    freed = 0

    def drop(path: Path):
        nonlocal freed
        try:
            if path.is_dir() and not path.is_symlink():
                freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
                shutil.rmtree(path, ignore_errors=True)
            else:
                freed += path.lstat().st_size
                path.unlink()
        except OSError:
            pass

    tmp = run / "tmp"
    if tmp.is_dir():
        drop(tmp)
        tmp.mkdir()
    work = run / "work"
    for dirpath, dirnames, names in os.walk(work):
        d = Path(dirpath)
        for name in [n for n in dirnames if n in (".venv", "venv", "__pycache__")]:
            drop(d / name)
            dirnames.remove(name)
        for name in names:
            p = d / name
            try:
                if not p.is_symlink() and p.stat().st_size >= LARGE and not _published(p, work):
                    drop(p)
            except OSError:
                pass
    return freed
