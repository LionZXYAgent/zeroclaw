#!/usr/bin/env python3
"""Test the installer with fake downloads, binaries, and systemd commands."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

INSTALLER = Path(os.environ.get(
    "INSTALLER_UNDER_TEST",
    str(Path(__file__).resolve().parents[1] / "scripts/install-telegram-rich.sh"),
))
SOURCE = "24946811cc47ad925be4e9563c1a14d107217b0c"
TAG = "v0.8.6-telegram-rich-pr11862-24946811c"
ASSET = "zeroclaw-x86_64-unknown-linux-gnu.tar.gz"

NEW_BINARY = r'''#!/usr/bin/env python3
# NEW_RELEASE_MARKER
import json, os, pathlib, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("zeroclaw 0.8.6 (telegram-rich-pr11862-24946811c-test)")
elif args == ["config", "schema"]:
    print(json.dumps({"$defs": {
        "TelegramConfig": {"properties": {"rich_messages": {"type": "boolean", "default": False}}},
        "McpServerConfig": {"properties": {"tls_ca_cert_path": {"type": "string"}}}
    }}))
elif "--config-dir" in args and "set" in args:
    directory = pathlib.Path(args[args.index("--config-dir") + 1])
    with (directory / "config.toml").open("a") as stream:
        stream.write("rich_messages = true\n")
    (directory / "new-version-state").write_text("new state")
    if os.environ.get("MOCK_FAIL") == "config":
        sys.exit(42)
else:
    sys.exit("Unexpected fake binary arguments: " + repr(args))
'''

SYSTEMCTL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["MOCK_ROOT"])
state_file = root / "service.json"
state = json.loads(state_file.read_text())
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
command = args[0]
if command == "is-active":
    sys.exit(0 if state["active"] else 3)
if command == "show":
    if "--property=ExecStart" in args:
        binary = os.environ["ZEROCLAW_BIN"]
        if os.environ.get("MOCK_FAIL") == "wrong-path":
            binary = "/not/the/expected/binary"
        print("{ path=" + binary + " ; argv[]=zeroclaw daemon ; }")
    elif "--property=MainPID" in args:
        print(state["pid"] if state["active"] else 0)
    else:
        sys.exit(2)
elif command == "stop":
    state["active"] = False
    state_file.write_text(json.dumps(state))
elif command == "start":
    is_new = "NEW_RELEASE_MARKER" in pathlib.Path(os.environ["ZEROCLAW_BIN"]).read_text()
    if os.environ.get("MOCK_FAIL") == "start" and is_new:
        state["active"] = False
        state_file.write_text(json.dumps(state))
        sys.exit(1)
    state["active"] = True
    state["pid"] += 1
    state_file.write_text(json.dumps(state))
else:
    sys.exit(2)
'''

CURL = r'''#!/usr/bin/env python3
import os, pathlib, shutil, sys
if os.environ.get("MOCK_FAIL") == "download":
    sys.exit(22)
args = sys.argv[1:]
output = pathlib.Path(args[args.index("--output") + 1])
url = next(arg for arg in args if arg.startswith("https://"))
source = pathlib.Path(os.environ["MOCK_ROOT"]) / "assets" / url.rsplit("/", 1)[-1]
shutil.copyfile(source, output)
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zeroclaw-installer-test-")
        self.root = Path(self.temporary.name)
        self.mockbin = self.root / "mockbin"
        self.mockbin.mkdir()
        self.binary = self.root / "bin" / "zeroclaw"
        self.binary.parent.mkdir()
        self.old_binary = b"#!/bin/sh\n# OLD_RELEASE_MARKER\nexit 0\n"
        self.binary.write_bytes(self.old_binary)
        self.binary.chmod(0o755)
        self.data = self.root / "state"
        self.data.mkdir()
        self.old_config = b"schema_version = 3\n[channels.telegram.telegram]\nenabled = true\n"
        (self.data / "config.toml").write_bytes(self.old_config)
        (self.data / "memory.db").write_bytes(b"existing memory")
        (self.data / "encryption-key-test-fixture").write_bytes(b"test fixture only")
        (self.root / "assets").mkdir()
        (self.root / "service.json").write_text(json.dumps({"active": True, "pid": 100}))
        self.write_command("id", "#!/bin/sh\nprintf '0\\n'\n")
        self.write_command("uname", "#!/bin/sh\ncase \"$1\" in -s) echo Linux;; -m) echo x86_64;; esac\n")
        self.write_command("systemctl", SYSTEMCTL)
        self.write_command("curl", CURL)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.mockbin) + os.pathsep + self.env["PATH"],
            "MOCK_ROOT": str(self.root),
            "ZEROCLAW_BIN": str(self.binary),
            "ZEROCLAW_DATA_DIR": str(self.data),
            "ZEROCLAW_BACKUP_ROOT": str(self.root / "backups"),
            "ZEROCLAW_UPDATE_LOCK": str(self.root / "update.lock"),
            "ZEROCLAW_UPDATE_HEALTH_DELAY": "0",
        })
        self.package()

    def tearDown(self):
        self.temporary.cleanup()

    def write_command(self, name, text):
        path = self.mockbin / name
        path.write_text(text)
        path.chmod(0o755)

    def package(self, source=SOURCE, extra_file=False):
        archive = self.root / "assets" / ASSET
        metadata = {
            "repository": "LionZXYAgent/zeroclaw", "release_tag": TAG,
            "source_commit": source, "target": "x86_64-unknown-linux-gnu",
        }
        files = {"zeroclaw": NEW_BINARY.encode(), "BUILDINFO.json": json.dumps(metadata).encode()}
        if extra_file:
            files["../unexpected-file"] = b"must not be extracted"
        with tarfile.open(archive, "w:gz") as output:
            for name, content in files.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                output.addfile(item, io.BytesIO(content))
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.root / "assets" / "SHA256SUMS").write_text(checksum + "  " + ASSET + "\n")

    def run_installer(self, mode=None):
        if mode:
            self.env["MOCK_FAIL"] = mode
        return subprocess.run(["sh", str(INSTALLER)], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_old_state(self):
        self.assertEqual(self.binary.read_bytes(), self.old_binary)
        self.assertEqual((self.data / "config.toml").read_bytes(), self.old_config)
        self.assertEqual((self.data / "memory.db").read_bytes(), b"existing memory")
        self.assertFalse((self.data / "new-version-state").exists())
        self.assertTrue(json.loads((self.root / "service.json").read_text())["active"])

    def assert_not_stopped(self):
        self.assertFalse(any(call[0] == "stop" for call in self.calls()))
        self.assert_old_state()

    def test_success_preserves_state_and_saves_backup(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEW_RELEASE_MARKER", self.binary.read_text())
        self.assertIn("rich_messages = true", (self.data / "config.toml").read_text())
        self.assertEqual((self.data / "memory.db").read_bytes(), b"existing memory")
        backups = list((self.root / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "zeroclaw").read_bytes(), self.old_binary)
        self.assertEqual((backups[0] / "state" / "config.toml").read_bytes(), self.old_config)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        self.assertTrue(json.loads((self.root / "service.json").read_text())["active"])

    def test_checksum_failure_does_not_stop_service(self):
        (self.root / "assets" / "SHA256SUMS").write_text("0" * 64 + "  " + ASSET + "\n")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_stopped()

    def test_download_failure_does_not_stop_service(self):
        result = self.run_installer("download")
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_stopped()

    def test_wrong_source_does_not_stop_service(self):
        self.package(source="0" * 40)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_stopped()

    def test_unexpected_archive_member_does_not_stop_service(self):
        self.package(extra_file=True)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_stopped()
        self.assertFalse((self.root / "unexpected-file").exists())

    def test_wrong_service_binary_does_not_stop_service(self):
        result = self.run_installer("wrong-path")
        self.assertNotEqual(result.returncode, 0)
        self.assert_not_stopped()

    def test_configuration_failure_restores_binary_and_full_state(self):
        result = self.run_installer("config")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_state()
        failed = list((self.root / "backups").glob("*/state-after-failed-update"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "new-version-state").exists())

    def test_start_failure_restores_binary_and_full_state(self):
        result = self.run_installer("start")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_state()


if __name__ == "__main__":
    unittest.main()
