"""Synthetic tests for Windows pairing gates and the production adapter wiring."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hantek_dso5k import _pair_windows as windows
from hantek_dso5k._host import invocation_reason
from hantek_dso5k._pair_session import PROFILES, PairSpec
from hantek_dso5k._retrieve import CONFIRMATIONS
from hantek_dso5k.cli import main

from test_pair_session import FakeLink, frames


class PairWindowsTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.marker = root / "marker"
        self.evidence = root / "evidence"
        self.pin = "a" * 40
        self.data = [bytes([i]) * size for i, (_, _, size) in enumerate(PROFILES)]
        self.specs = tuple(PairSpec(root / f"reference{i}.csv", sha256(data).hexdigest(),
                                    root / f"output{i}.csv") for i, data in enumerate(self.data))
        for spec, data in zip(self.specs, self.data):
            spec.reference.write_bytes(data)
        for name, value in {
            "_source_commit": Mock(return_value=self.pin),
            "state_path": Mock(return_value=self.marker),
            "host_snapshot": Mock(return_value={}),
            "assess_host": Mock(return_value={"blocked": None, "available_instances": 1,
                                             "driver": "WinUSB", "assignment": "no_usbip_service",
                                             "unavailable_command_lines": 0}),
            "open_link": Mock(side_effect=AssertionError("USB is forbidden in tests")),
            "lease": Mock(side_effect=AssertionError("Unexpected lease")),
        }.items():
            patcher = patch.object(windows, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(windows, "sys", SimpleNamespace(
            platform="win32", executable=sys.executable, _base_executable=sys._base_executable,
            stdin=Mock(), stderr=StringIO()))
        patcher.start()
        self.addCleanup(patcher.stop)
        windows.sys.stdin.isatty.return_value = True
        windows.sys.stderr.isatty = lambda: True

    def command_args(self, command):
        a, b = self.specs
        return ["files", command, "--ch1-reference", str(a.reference), "--ch2-reference", str(b.reference),
                "--ch1-output", str(a.output), "--ch2-output", str(b.output), "--ch1-sha256",
                a.expected_sha256, "--ch2-sha256", b.expected_sha256, "--evidence-dir",
                str(self.evidence), "--source-commit", self.pin]

    def test_help_and_preflight_never_claim_or_take_lease(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as help_exit:
                main(["files", "retrieve-pair", "--help"])
            self.assertEqual(help_exit.exception.code, 0)
            windows.host_snapshot.assert_not_called()
            self.assertEqual(main(self.command_args("pair-preflight")), 0)
        windows.host_snapshot.assert_called_once()
        windows.lease.assert_not_called()
        windows.open_link.assert_not_called()
        self.assertFalse(self.evidence.exists())

    def test_clean_import_and_help_without_usb_modules(self):
        probe = (
            "import sys; "
            "sys.modules['usb'] = None; sys.modules['libusb_package'] = None; "
            "from hantek_dso5k.cli import main; main(['files', 'retrieve-pair', '--help'])"
        )
        result = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reference_missing_malformed_and_changed(self):
        a, b = self.specs
        malformed = a._replace(expected_sha256="g" * 64)
        self.assertEqual(windows.passive_preflight((malformed, b), self.evidence, self.pin)["exit_code"], 3)
        a.reference.unlink()
        self.assertEqual(windows.passive_preflight(self.specs, self.evidence, self.pin)["exit_code"], 3)
        a.reference.write_bytes(b"X" * len(self.data[0]))
        self.assertEqual(windows.passive_preflight(self.specs, self.evidence, self.pin)["exit_code"], 3)
        windows.host_snapshot.assert_not_called()
        windows.open_link.assert_not_called()

    def test_active_admission_without_tty_confirmation_or_verified_pin(self):
        with patch("builtins.input", side_effect=AssertionError("Do not prompt")):
            self.assertEqual(windows.retrieve_pair(self.specs, self.evidence, self.pin, ())["exit_code"], 3)
            windows.sys.stdin.isatty.return_value = False
            self.assertEqual(windows.retrieve_pair(self.specs, self.evidence, self.pin,
                                                    CONFIRMATIONS)["exit_code"], 3)
            windows.sys.stdin.isatty.return_value = True
            windows._source_commit.side_effect = windows.PairBlocked("Wrong pin")
            self.assertEqual(windows.retrieve_pair(self.specs, self.evidence, self.pin,
                                                    CONFIRMATIONS)["exit_code"], 3)
        windows.open_link.assert_not_called()

    def test_interactive_decline_and_cancel(self):
        for reply in ("NO", KeyboardInterrupt()):
            with self.subTest(reply=reply):
                with patch("builtins.input", side_effect=[reply]):
                    result = windows.retrieve_pair(self.specs, self.evidence, self.pin, CONFIRMATIONS)
                self.assertEqual(result["request_attempts"], 0)
        windows.open_link.assert_not_called()
        windows.lease.assert_not_called()

    def test_existing_marker_and_collision_block_passive(self):
        self.marker.write_text("existing")
        first = windows.passive_preflight(self.specs, self.evidence, self.pin)
        self.assertEqual(first["exit_code"], 3)
        self.assertEqual(self.marker.read_text(), "existing")
        self.marker.unlink()
        self.specs[1].output.write_bytes(b"keep")
        second = windows.passive_preflight(self.specs, self.evidence, self.pin)
        self.assertEqual(second["exit_code"], 3)
        self.assertEqual(self.specs[1].output.read_bytes(), b"keep")
        windows.open_link.assert_not_called()

    def test_live_ownership_conflict_releases_lease_without_claim(self):
        events = []

        @contextmanager
        def hold():
            events.append("lease")
            yield
            events.append("released")

        windows.lease.side_effect = hold
        windows.assess_host.return_value = {"blocked": "Competing client"}
        with patch("builtins.input", return_value="YES"):
            result = windows.retrieve_pair(self.specs, self.evidence, self.pin, CONFIRMATIONS)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(events, ["lease", "released"])
        self.assertEqual(result["marker"], "ABSENT")
        windows.open_link.assert_not_called()

    def test_real_bulk_adapter_wiring_with_fake_pyusb_device(self):
        fake = FakeLink(*(frames(data) for data in self.data))
        events = []

        class DeviceTimeout(Exception):
            pass

        class Device:
            def read(self, endpoint, size, *, timeout):
                self_test.assertEqual(endpoint, 0x81)
                self_test.assertGreaterEqual(timeout, 1)
                self_test.assertLessEqual(timeout, 250)
                try:
                    return fake.read(size, timeout_ms=timeout)
                except TimeoutError as exc:
                    raise DeviceTimeout() from exc

            def write(self, endpoint, data, *, timeout):
                self_test.assertEqual(endpoint, 0x02)
                self_test.assertEqual(timeout, 2000)
                return fake.write(data)

        self_test = self

        @contextmanager
        def hold():
            events.append("lease")
            yield
            events.append("release_lease")

        @contextmanager
        def open_link():
            self.assertTrue(self.marker.is_file())
            events.append("claim")
            yield SimpleNamespace(device=Device(), timeout_error=DeviceTimeout)
            events.append("release_interface")

        windows.lease.side_effect = hold
        windows.open_link.side_effect = open_link
        with patch.object(windows, "perf_counter", side_effect=lambda: fake.clock):
            with patch("builtins.input", return_value="YES"):
                result = windows.retrieve_pair(self.specs, self.evidence, self.pin, CONFIRMATIONS)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["source_commit"], self.pin)
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(result["retries"], 0)
        self.assertEqual(len(fake.writes), 2)
        self.assertEqual(events, ["lease", "claim", "release_interface", "release_lease"])
        windows.host_snapshot.assert_called_once()

    def test_experimental_guard_is_explicit_and_active_only(self):
        args = self.command_args("retrieve-pair")
        with patch.object(windows, "retrieve_pair", return_value={"exit_code": 0}) as retrieve:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(args), 0)
                self.assertFalse(retrieve.call_args.kwargs["experimental_s92_guard"])
                self.assertEqual(main([*args, "--experimental-s92-guard"]), 0)
                self.assertTrue(retrieve.call_args.kwargs["experimental_s92_guard"])
                with self.assertRaises(SystemExit) as error:
                    main([*self.command_args("pair-preflight"), "--experimental-s92-guard"])
                self.assertEqual(error.exception.code, 2)
        windows.open_link.assert_not_called()

    def test_ownership_classifier_distinguishes_active_and_passive_module_invocations(self):
        def classify(action):
            return invocation_reason({"exe": "python.exe", "argv": [
                "python.exe", "-B", "-m", "hantek_dso5k", "files", action]})

        self.assertIsNotNone(classify("retrieve-pair"))
        self.assertIsNotNone(classify("retrieve"))
        self.assertIsNone(classify("pair-preflight"))
