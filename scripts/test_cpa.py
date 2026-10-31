"""Run: python scripts/test_cpa.py"""

import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cpa  # noqa: E402

EXAMPLE = '''server:
  host: ""
  port: 8317
access:
  api-keys:
    - "your-api-key-1"
    - "your-api-key-2"
# old spelling:
#   api-keys: ["your-api-key-1"]
'''


class MakeConfig(unittest.TestCase):
    def test_binds_loopback_and_replaces_keys(self):
        text = cpa.make_config(EXAMPLE)
        self.assertIn('  host: "127.0.0.1"\n', text)
        self.assertNotIn('- "your-api-key', text)
        self.assertEqual(text.count("    - "), 1)

    def test_keys_are_random(self):
        self.assertNotEqual(cpa.make_config(EXAMPLE), cpa.make_config(EXAMPLE))

    def test_refuses_unknown_layout(self):
        with self.assertRaises(RuntimeError):
            cpa.make_config(EXAMPLE.replace('host: ""', 'host: "0.0.0.0"'))


class AssetSuffix(unittest.TestCase):
    def check(self, plat, machine, windows, want):
        with mock.patch.object(cpa.sys, "platform", plat), \
             mock.patch.object(cpa.platform, "machine", return_value=machine), \
             mock.patch.object(cpa, "WINDOWS", windows):
            self.assertEqual(cpa.asset_suffix(), want)

    def test_platforms(self):
        self.check("win32", "AMD64", True, "_windows_amd64.zip")
        self.check("linux", "x86_64", False, "_linux_amd64.tar.gz")
        self.check("linux", "aarch64", False, "_linux_aarch64.tar.gz")
        self.check("darwin", "arm64", False, "_darwin_aarch64.tar.gz")

    def test_unknown_cpu(self):
        with mock.patch.object(cpa.platform, "machine", return_value="riscv64"):
            with self.assertRaises(RuntimeError):
                cpa.asset_suffix()


class Extract(unittest.TestCase):
    def test_hostile_path_lands_in_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src, dest = tmp / "f", tmp / "out"
            src.write_text("x")
            dest.mkdir()
            with tarfile.open(tmp / "a.tar.gz", "w:gz") as t:
                t.add(src, arcname="../../proxy")
            with zipfile.ZipFile(tmp / "a.zip", "w") as z:
                z.write(src, "./proxy2")
            cpa._extract(tmp / "a.tar.gz", {"proxy"}, dest)
            cpa._extract(tmp / "a.zip", {"proxy2"}, dest)
            self.assertEqual(sorted(p.name for p in dest.iterdir()), ["proxy", "proxy2"])


if __name__ == "__main__":
    unittest.main()
