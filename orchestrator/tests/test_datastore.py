"""Shared-store behavior, including Linux's unlistable object directories."""
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quo_swarm import config, datastore


class DataStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in {"OBJECTS": root / "objects", "PARTIAL": root / "partial",
                            "INDEX": root / "index.json", "LOG": root / "data.log",
                            "_locks": {}, "_last_fetch": {}}.items():
            p = patch.object(datastore, name, value)
            p.start()
            self.addCleanup(p.stop)
        cfg = config.load()
        cfg["disk_reserve_gib"] = 0
        cfg["data_service"]["max_file_gib"] = 1
        p = patch.object(config, "load", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(datastore, "_free_gib", return_value=100)
        p.start()
        self.addCleanup(p.stop)
        # Directory modes must be restored for TemporaryDirectory cleanup as non-root.
        self.addCleanup(self.restore_modes)

    def restore_modes(self):
        if datastore.OBJECTS.exists():
            datastore.OBJECTS.chmod(0o700)
        for path in set(datastore._index().values()):
            if Path(path).parent.exists():
                Path(path).parent.chmod(0o700)

    def download(self, url="https://example.org/one.dat", payload=b"same bytes"):
        with patch.object(datastore, "_check_url"), \
             patch.object(datastore._opener, "open", return_value=io.BytesIO(payload)):
            return datastore.fetch(url)

    def test_content_dedup_and_hits_return_one_immutable_object(self):
        first = self.download()
        second = self.download("https://example.org/two.dat")
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), b"same bytes")
        self.assertEqual(first.stat().st_mode & 0o777, 0o444)
        with patch.object(datastore._opener, "open", side_effect=AssertionError("redownload")):
            self.assertEqual(datastore.fetch("https://example.org/one.dat"), first)
        self.assertEqual(len(set(datastore._index().values())), 1)

    def test_concurrent_requests_download_once(self):
        paths = []
        with patch.object(datastore, "_check_url"), \
             patch.object(datastore._opener, "open", return_value=io.BytesIO(b"data")) as remote:
            workers = [threading.Thread(target=lambda: paths.append(datastore.fetch("https://example.org/data")))
                       for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        self.assertEqual(len(paths), 4)
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(remote.call_count, 1)

    def test_eviction_uses_private_index_and_keeps_recent_fetches(self):
        old = self.download()
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        datastore.LOG.write_text("")
        datastore._last_fetch.clear()
        with patch.object(datastore, "_free_gib", return_value=0):
            self.assertEqual(datastore.evict(1), len(b"same bytes"))
        self.assertFalse(old.exists())
        self.assertEqual(datastore._index(), {})
        fresh = self.download()
        with patch.object(datastore, "_free_gib", return_value=0):
            self.assertEqual(datastore.evict(1), 0)
        self.assertTrue(fresh.exists())

    def test_loopback_private_and_redirect_targets_are_rejected(self):
        for host in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"):
            with patch.object(datastore.socket, "getaddrinfo", return_value=[(0, 0, 0, "", (host, 80))]):
                with self.assertRaises(datastore.FetchError):
                    datastore._check_url("http://example.org/data")
                with self.assertRaises(datastore.FetchError):
                    datastore._Redirects().redirect_request(None, None, 302, "", {}, "http://example.org/data")

    def test_failed_download_leaves_no_partial_or_index_entry(self):
        with patch.object(datastore, "_check_url"), \
             patch.object(datastore._opener, "open", side_effect=TimeoutError("test")):
            with self.assertRaises(datastore.FetchError):
                datastore.fetch("https://example.org/data")
        self.assertEqual(list(datastore.PARTIAL.iterdir()), [])
        self.assertFalse(datastore.INDEX.exists())


if __name__ == "__main__":
    unittest.main()
