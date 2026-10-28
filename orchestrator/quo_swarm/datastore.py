"""Shared download service: one read-only copy of each public file for all agents.

An agent runs `fetch URL`. The service downloads the URL once into
DATA/objects/<hash of URL>/<file name> and returns that path; later requests
for the same URL get the same path. The answer has the same form whether the
file was already there or not, and agents cannot list the cache, so what
other agents downloaded does not steer what an agent studies.

The service runs in the orchestrator, outside the sandbox. It fetches only
public http(s) addresses (no loopback or private networks, also after
redirects) and stops when free disk space falls below the pool reserve.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

OBJECTS = config.DATA / "objects"
PARTIAL = config.DATA / "partial"
INDEX = config.DATA / "index.json"  # {"url:<url>": path, "sha256:<hex>": path}; never shown to agents
LOG = config.STATE / "data.log"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class FetchError(RuntimeError):
    pass


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http and https URLs are supported")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {parts.hostname}: {exc}") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise FetchError(f"{parts.hostname} is not a public address")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirects)


def _name(url: str) -> str:
    base = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    base = "".join(c if c.isalnum() or c in "._-" else "_" for c in base).lstrip(".")
    return base[:120] or "download"


def object_path(url: str) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    return OBJECTS / key / _name(url)


def _free_gib() -> float:
    return shutil.disk_usage(config.ROOT).free / 2**30


def _index() -> dict:
    try:
        return json.loads(INDEX.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _remember(**entries) -> None:
    index = _index()
    index.update(entries)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=0), "utf-8")
    tmp.replace(INDEX)


def _log(entry: dict) -> None:
    with LOG.open("a") as f:
        f.write(json.dumps({"t": round(time.time()), **entry}) + "\n")


def fetch(url: str, run_id: str = "") -> Path:
    url = url.strip()
    target = object_path(url)
    with _locks_guard:
        lock = _locks.setdefault(url, threading.Lock())
    with lock:  # one download per URL; other requests for it wait and share the result
        with _locks_guard:
            known = _index().get("url:" + url)
        if known and Path(known).is_file():
            _log({"run": run_id, "url": url, "hit": True})
            return Path(known)
        _check_url(url)
        cfg = config.load()
        reserve = float(cfg["disk_reserve_gib"])
        limit = float(cfg["data_service"]["max_file_gib"]) * 2**30
        if _free_gib() < reserve:
            raise FetchError("the shared disk is full; downloads are paused")
        PARTIAL.mkdir(parents=True, exist_ok=True)
        tmp = PARTIAL / f"{target.parent.name}.{threading.get_ident()}"
        size, digest = 0, hashlib.sha256()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sci-swarm-fetch/1"})
            with _opener.open(req, timeout=120) as resp, tmp.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise FetchError("file is larger than the shared download limit")
                    if size % (1 << 30) < (1 << 20) and _free_gib() < reserve:
                        raise FetchError("the shared disk is full; downloads are paused")
                    digest.update(chunk)
                    out.write(chunk)
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"HTTP {exc.code} from server") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            tmp.unlink(missing_ok=True)
            raise FetchError(f"download failed: {exc}") from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        sha = digest.hexdigest()
        with _locks_guard:  # same bytes from another URL: keep one copy
            same = _index().get("sha256:" + sha)
            if same and Path(same).is_file():
                tmp.unlink()
                target = Path(same)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp.chmod(0o444)
                tmp.rename(target)
            _remember(**{"url:" + url: str(target), "sha256:" + sha: str(target)})
        _log({"run": run_id, "url": url, "hit": False, "bytes": size, "sha256": sha})
        return target


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        url = (query.get("url") or [""])[0]
        try:
            if not url:
                raise FetchError("usage: fetch URL")
            path = fetch(url, self.headers.get("X-Run-Id", ""))
            body, code = {"path": str(path), "bytes": path.stat().st_size}, 200
        except FetchError as exc:
            body, code = {"error": str(exc)}, 400
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve() -> ThreadingHTTPServer:
    cfg = config.load()["data_service"]
    OBJECTS.mkdir(parents=True, exist_ok=True)
    for stale in PARTIAL.glob("*") if PARTIAL.is_dir() else ():
        stale.unlink(missing_ok=True)
    server = ThreadingHTTPServer((cfg["host"], int(cfg["port"])), Handler)
    server.daemon_threads = True
    return server
