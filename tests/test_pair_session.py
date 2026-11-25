"""ACQ-06 simulated sequential S10 controller; no live device access."""
from collections import deque
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k._file_replay import CHUNK_BYTES, csv_request
from hantek_dso5k._pair_session import PairBlocked, PairS10IO, PairSpec, PROFILES, simulate_pair
from hantek_dso5k._receive import ReceiveError
from hantek_dso5k._retrieve import CONFIRMATIONS
from hantek_dso5k._s92_guard import observe_qualification_guard, qualification_guard_result


class Timeout(Exception):
    pass


def frames(data):
    return b"".join([frame(0x90, b"\x01" + data[i:i + CHUNK_BYTES])
                    for i in range(0, len(data), CHUNK_BYTES)] +
                    [frame(0x90, bytes([2, sum(data) & 255]))])


class FakeLink:
    """Select scripted receive windows by count of attempted S10 writes."""
    def __init__(self, first=b"", second=b"", *, preflight=b"", guard=b"",
                 fail_write=None, short_write=None, fault_on_second_read=False):
        self.clock = 0.0
        self.writes = []
        self.preflight = deque([preflight] if preflight else [])
        self.guard = deque([guard] if guard else [])
        self.first = deque([first] if first else [])
        self.second = deque([second] if second else [])
        self.fail_write = fail_write
        self.short_write = short_write
        self.fault_on_second_read = fault_on_second_read
        self.guarding = False

    def read(self, size, *, timeout_ms):
        if len(self.writes) == 0:
            queue = self.preflight
        elif len(self.writes) == 1 and self.guarding:
            queue = self.guard
        elif len(self.writes) == 1:
            queue = self.first
        else:
            if self.fault_on_second_read:
                raise OSError("second response disconnected")
            queue = self.second
        if queue:
            payload = queue.popleft()
            self.clock += 0.002
            if isinstance(payload, Exception):
                raise payload
            if len(payload) > size:
                queue.appendleft(payload[size:])
            return payload[:size]
        self.clock += timeout_ms / 1000
        raise TimeoutError()

    def write(self, request):
        self.writes.append(request)
        if self.fail_write == len(self.writes):
            raise OSError("ambiguous USB write")
        if self.short_write == len(self.writes):
            return len(request) - 1
        return len(request)


class PairSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.marker = self.root / "marker.json"
        self.evidence = self.root / "evidence"
        self.data = {}
        self.specs = []
        for key, filename, count in PROFILES:
            data = (key.encode("ascii") + b",1,2\r\n") * (count // 9 + 2)
            data = data[:count]
            self.data[key] = data
            reference = self.root / (key + "-reference.csv")
            reference.write_bytes(data)
            self.specs.append(PairSpec(reference, sha256(data).hexdigest(),
                                       self.root / (key + "-output.csv")))
        self.link = FakeLink(frames(self.data["CH1"]), frames(self.data["CH2"]) + STATUS)
        self.events = []
        self.interface_failure = False
        self.lease_failure = False

    def execute(self, *, confirmations=CONFIRMATIONS, specs=None, marker=None, evidence=None,
                experimental_s92_guard=False):
        link = self.link
        test = self

        @contextmanager
        def hold():
            test.events.append("lease_enter")
            yield
            test.events.append("lease_exit")
            if test.lease_failure:
                raise OSError("lease release failed")

        @contextmanager
        def claim():
            test.events.append("claim_enter")
            yield link
            test.events.append("claim_exit")
            if test.interface_failure:
                raise OSError("interface release failed")

        # Dedicated deterministic receive-phase control: the fake must recognize
        # the guard as a distinct window from the first S90 response.
        # A log hook is not exposed; infer it from elapsed scripted clock time.
        class Clock:
            def __call__(self):
                # Once CH1 response has completed (>=24s), switch fake to guard.
                link.guarding = (len(link.writes) == 1 and link.clock >= 24.0)
                return link.clock

        a, b = specs or self.specs
        return simulate_pair(a, b, evidence or self.evidence,
                             confirmations=confirmations, hold=hold,
                             census=lambda: {"devices": []},
                             claim=claim, assess=lambda *args: {"blocked": None},
                             clock=Clock(), marker=marker or self.marker,
                             experimental_s92_guard=experimental_s92_guard)

    def test_success_one_claim_two_writes_clean_marker_and_exact_output(self):
        result = self.execute()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(self.link.writes, [csv_request("WaveData163.csv"),
                                            csv_request("WaveData164.csv")])
        self.assertEqual(self.events, ["lease_enter", "claim_enter", "claim_exit", "lease_exit"])
        self.assertFalse(self.marker.exists())
        self.assertEqual(result["marker"], "CLEARED")
        self.assertIsNone(result["same_shot"])
        self.assertEqual(result["acquisition_coherence"], "UNPROVEN")
        self.assertFalse(result["hardware_qualified"])
        for key, spec in zip(("CH1", "CH2"), self.specs):
            self.assertEqual(spec.output.read_bytes(), self.data[key])
            self.assertEqual(result["channels"][key]["integrity"], "PASS")
        self.assertEqual((self.evidence / "preflight_rx.bin").stat().st_size, 0)
        self.assertEqual((self.evidence / "inter_request_guard_rx.bin").stat().st_size, 0)
        self.assertEqual(json.loads((self.evidence / "pair_manifest.json").read_text()), result)

    def test_guard_s92_aborts_before_second_write(self):
        self.link.guard.append(STATUS)
        result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["error"]["stage"], "inter_request_guard")
        self.assertEqual(len(self.link.writes), 1)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.specs[0].output.exists())
        self.assertFalse(self.specs[1].output.exists())

    def test_ch1_wrong_digest_blocks_ch2(self):
        self.link.first = deque([frames(self.data["CH1"][:-1] + b"X")])
        result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["error"]["stage"], "CH1_validate")
        self.assertEqual(len(self.link.writes), 1)
        self.assertTrue(self.marker.exists())

    def test_ch1_corrupt_checksum_blocks_ch2(self):
        stream = bytearray(frames(self.data["CH1"]))
        stream[-1] ^= 1
        self.link.first = deque([bytes(stream)])
        result = self.execute()
        self.assertEqual(result["error"]["stage"], "CH1_response")
        self.assertEqual(len(self.link.writes), 1)
        self.assertTrue(self.marker.exists())

    def test_ch2_failure_retains_marker_and_preserves_raw_ch1(self):
        self.link.second = deque([])
        result = self.execute()
        self.assertEqual(result["error"]["stage"], "CH2_validate")
        self.assertEqual(len(self.link.writes), 2)
        self.assertTrue(result["channels"]["CH1"]["transfer_complete"])
        self.assertFalse(result["channels"]["CH2"]["transfer_complete"])
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue((self.evidence / "CH1_response_rx.bin").exists())
        self.assertFalse(self.specs[0].output.exists())
        self.assertTrue(self.marker.exists())

    def test_ambiguous_first_write_cannot_fallthrough_to_ch2(self):
        self.link.fail_write = 1
        result = self.execute()
        self.assertEqual(len(self.link.writes), 1)
        self.assertEqual(result["error"]["stage"], "CH1_write")
        self.assertTrue(self.marker.exists())

    def test_short_second_write_never_retries(self):
        self.link.short_write = 2
        result = self.execute()
        self.assertEqual(len(self.link.writes), 2)
        self.assertEqual(result["error"]["stage"], "CH2_write")
        self.assertTrue(self.marker.exists())

    def test_preflight_status_blocks_all_writes(self):
        self.link.preflight.append(STATUS)
        result = self.execute()
        self.assertEqual(len(self.link.writes), 0)
        self.assertEqual(result["error"]["stage"], "preflight")
        self.assertTrue(self.marker.exists())

    def test_existing_marker_blocks_before_claim(self):
        self.marker.write_text("previous")
        result = self.execute()
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(result["marker"], "EXISTING")
        self.assertNotIn("claim_enter", self.events)
        self.assertEqual(self.link.writes, [])
        self.assertEqual(self.marker.read_text(), "previous")

    def test_bad_reference_and_destination_block_before_claim(self):
        bad = self.specs[0].reference
        bad.write_bytes(b"wrong")
        result = self.execute()
        self.assertEqual(result["exit_code"], 3)
        self.assertNotIn("claim_enter", self.events)
        self.assertEqual(self.link.writes, [])

    def test_release_error_leaves_marker_and_no_outputs(self):
        self.interface_failure = True
        result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertFalse(result["cleanup"]["interface_released"])
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.specs[0].output.exists())

    def test_ch2_wrong_digest_never_publishes_pair(self):
        tampered = self.data["CH2"][:-1] + bytes([self.data["CH2"][-1] ^ 1])
        self.link.second = deque([frames(tampered)])
        result = self.execute()
        self.assertEqual(result["error"]["stage"], "CH2_validate")
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(result["channels"]["CH1"]["integrity"], "PASS")
        self.assertFalse(result["channels"]["CH1"]["output_published"])
        self.assertTrue(self.marker.exists())

    def test_lease_cleanup_failure_retains_marker(self):
        self.lease_failure = True
        result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertFalse(result["cleanup"]["lease_released"])
        self.assertTrue(self.marker.exists())
        self.assertFalse(any(spec.output.exists() for spec in self.specs))

    def test_duplicate_destination_blocks_without_claim(self):
        a, b = self.specs
        result = self.execute(specs=(a, PairSpec(b.reference, b.expected_sha256, a.output)))
        self.assertEqual(result["exit_code"], 3)
        self.assertEqual(self.link.writes, [])
        self.assertNotIn("claim_enter", self.events)

    def test_ch1_output_collision_during_usb_blocks_ch2(self):
        # Simulates a second writer creating the destination after the host checks.
        original_read = self.link.read
        first = [True]
        def read(*args, **kwargs):
            if first[0]:
                first[0] = False
                self.specs[0].output.write_bytes(b"collision")
            return original_read(*args, **kwargs)
        self.link.read = read
        result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["request_attempts"], 0)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.specs[0].output.read_bytes(), b"collision")

    def test_second_publication_error_is_partial_not_pair_pass(self):
        from hantek_dso5k._pair_session import publish_file as original_publish
        attempted = []
        def publish(path, data, digest):
            attempted.append(path)
            if len(attempted) == 2:
                raise OSError("simulated CH2 disk error")
            return original_publish(path, data, digest)
        with patch("hantek_dso5k._pair_session.publish_file", side_effect=publish):
            result = self.execute()
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["stage"], "publish")
        self.assertTrue(result["channels"]["CH1"]["output_published"])
        self.assertFalse(result["channels"]["CH2"]["output_published"])
        self.assertTrue(self.specs[0].output.exists())
        self.assertFalse(self.specs[1].output.exists())
        self.assertFalse(self.marker.exists())  # USB and cleanup settled before local publication

    def test_interrupted_ch1_read_retains_marker_and_releases_both_resources(self):
        original_read = self.link.read

        def read(*args, **kwargs):
            if self.link.writes:
                raise KeyboardInterrupt()
            return original_read(*args, **kwargs)

        self.link.read = read
        result = self.execute()
        self.assertEqual(result["error"]["type"], "KeyboardInterrupt")
        self.assertEqual(result["request_attempts"], 1)
        self.assertEqual(result["marker"], "RETAINED")
        self.assertTrue(result["cleanup"]["interface_released"])
        self.assertTrue(result["cleanup"]["lease_released"])
        self.assertFalse(any(spec.output.exists() for spec in self.specs))

    def test_evidence_finalization_failure_never_reports_pass(self):
        with patch("hantek_dso5k._pair_session.write_json_new", side_effect=OSError("disk full")):
            result = self.execute()
        self.assertEqual(result["error"]["stage"], "evidence_finalize")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(result["marker"], "CLEARED")
        self.assertTrue(result["cleanup"]["lease_released"])
        self.assertTrue(all(spec.output.exists() for spec in self.specs))
        self.assertTrue((self.evidence / "CH1_response_rx.bin").is_file())

    def test_partial_ch1_response_blocks_ch2(self):
        self.link.first = deque([frames(self.data["CH1"])[:-1]])
        result = self.execute()
        self.assertEqual(result["error"]["stage"], "CH1_response")
        self.assertEqual(result["request_attempts"], 1)
        self.assertEqual(result["marker"], "RETAINED")

    def test_ch2_disconnection_preserves_verified_ch1(self):
        self.link.fault_on_second_read = True
        result = self.execute()
        self.assertEqual(result["error"]["stage"], "CH2_response")
        self.assertEqual(result["request_attempts"], 2)
        self.assertTrue(result["channels"]["CH1"]["transfer_complete"])
        self.assertEqual(result["marker"], "RETAINED")
        self.assertFalse(any(spec.output.exists() for spec in self.specs))

    def test_all_receive_windows_are_twelve_seconds(self):
        result = self.execute()
        self.assertEqual(result["exit_code"], 0, result)
        self.assertGreaterEqual(self.link.clock, 48)
        self.assertLess(self.link.clock, 50)

    def scripted_guard(self, events, *, overrun=False, timeout_overrun=False):
        """Deliver relative host-read completions only while CH1 owns the guard."""
        original = self.link.read
        script = deque(events)
        guard_start = None

        def read(size, *, timeout_ms):
            nonlocal guard_start
            if len(self.link.writes) != 1 or not self.link.guarding:
                return original(size, timeout_ms=timeout_ms)
            if guard_start is None:
                guard_start = self.link.clock
            until = self.link.clock + timeout_ms / 1000
            if script and (overrun or guard_start + script[0][0] <= until):
                delay, payload = script.popleft()
                self.link.clock = guard_start + delay
                if isinstance(payload, BaseException):
                    raise payload
                return payload
            if timeout_overrun and not script:
                until += 0.010
            self.link.clock = until
            raise TimeoutError()

        self.link.read = read

    def check_guard_failure(self, events, reason, **transport):
        self.scripted_guard(events, **transport)
        result = self.execute(experimental_s92_guard=True)
        guard = result["inter_request_guard"]
        self.assertEqual(result["status"], "ERROR", result)
        self.assertEqual(result["error"]["stage"], "inter_request_guard")
        self.assertEqual(guard["outcome"], "FAIL")
        self.assertIn(reason, guard["stop_reason"])
        self.assertEqual(result["request_attempts"], 1)
        self.assertEqual(self.link.writes, [csv_request("WaveData163.csv")])
        self.assertEqual(result["channels"]["CH2"]["request_attempts"], 0)
        self.assertTrue(result["cleanup"]["lease_released"])
        self.assertTrue(result["cleanup"]["interface_released"])
        self.assertEqual(result["marker"], "RETAINED")
        self.assertTrue(self.marker.is_file())
        self.assertFalse(any(spec.output.exists() for spec in self.specs))
        self.assertFalse((self.evidence / "CH2_request_attempt.bin").exists())
        return guard

    def check_guard_success(self, events, first, final):
        self.scripted_guard(events)
        result = self.execute(experimental_s92_guard=True)
        guard = result["inter_request_guard"]
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["schema_version"], "0.2-s92-qualification")
        self.assertEqual(guard["policy"], "experimental_single_s92_restart")
        self.assertEqual(guard["classification"], "transfer_status_origin_unresolved")
        self.assertEqual(guard["outcome"], "CH2_ELIGIBLE")
        self.assertEqual(guard["recognized_status_frames"], 1)
        self.assertAlmostEqual(guard["first_byte_observed"] - guard["original_start"], first)
        self.assertAlmostEqual(guard["final_byte_observed"] - guard["original_start"], final)
        self.assertEqual(guard["quiet_start"], guard["final_byte_observed"])
        self.assertEqual(guard["quiet_deadline"], guard["quiet_start"] + 12)
        self.assertEqual(guard["original_deadline"], guard["original_start"] + 12)
        self.assertEqual(guard["absolute_deadline"], guard["original_start"] + 24)
        self.assertIsNone(guard["stop_reason"])
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(result["retries"], 0)
        self.assertFalse(self.marker.exists())
        self.assertTrue(all(spec.output.is_file() for spec in self.specs))
        return guard

    def test_experimental_guard_at_acq07h_host_relative_timing(self):
        # Exact frame and timing from the documented run, with synthetic file payloads.
        self.check_guard_success([(2.9731583, STATUS)], 2.9731583, 2.9731583)

    def test_experimental_guard_early_middle_late(self):
        for when in (0.001, 6.0, 11.9):
            with self.subTest(when=when):
                self.setUp()
                self.check_guard_success([(when, STATUS)], when, when)

    def test_experimental_guard_fragmentation_at_each_boundary(self):
        for offset in range(1, len(STATUS)):
            with self.subTest(offset=offset):
                self.setUp()
                self.check_guard_success([(2, STATUS[:offset]), (3, STATUS[offset:])], 2, 3)

    def test_experimental_guard_all_bytes_separately(self):
        self.check_guard_success([(float(i + 1), bytes([value])) for i, value in enumerate(STATUS)], 1, 7)

    def test_experimental_guard_clean_is_inconclusive_with_normal_cleanup(self):
        result = self.execute(experimental_s92_guard=True)
        self.assertEqual((result["status"], result["exit_code"]), ("INCONCLUSIVE", 7))
        self.assertEqual(result["inter_request_guard"]["outcome"], "INCONCLUSIVE")
        self.assertEqual(result["channels"]["CH2"]["request_attempts"], 0)
        self.assertEqual(len(self.link.writes), 1)
        self.assertTrue(result["cleanup"]["interface_released"])
        self.assertTrue(result["cleanup"]["lease_released"])
        self.assertEqual(result["marker"], "CLEARED")
        self.assertFalse(self.marker.exists())
        self.assertFalse(any(spec.output.exists() for spec in self.specs))
        self.assertEqual(json.loads((self.evidence / "pair_manifest.json").read_text()), result)

    def test_experimental_guard_rejects_trailing_and_coalesced_duplicate_bytes(self):
        for suffix in (b"X", STATUS):
            with self.subTest(suffix=suffix):
                self.setUp()
                guard = self.check_guard_failure([(2, STATUS + suffix)], "expected only one exact S92")
                self.assertEqual(guard["recognized_status_frames"], 0)

    def test_experimental_guard_rejects_each_nonmatching_field(self):
        for offset in range(len(STATUS)):
            with self.subTest(offset=offset):
                self.setUp()
                altered = bytes(value ^ (i == offset) for i, value in enumerate(STATUS))
                self.check_guard_failure([(2, altered)], "expected only one exact S92")

    def test_experimental_guard_rejects_duplicate_or_other_restarted_traffic(self):
        for payload in (STATUS, b"X", STATUS[:1]):
            with self.subTest(payload=payload):
                self.setUp()
                guard = self.check_guard_failure([(2, STATUS), (3, payload)], "restarted quiet")
                self.assertEqual(guard["recognized_status_frames"], 1)

    def test_experimental_guard_does_not_scan_past_wrong_marker(self):
        self.check_guard_failure([(2, b"X" + STATUS)], "expected only one exact S92")

    def test_experimental_guard_rejects_incomplete_frame(self):
        self.check_guard_failure([(2, STATUS[:-1])], "incomplete S92")

    def test_experimental_guard_rejects_completion_beyond_original_deadline(self):
        self.check_guard_failure([(11.9, STATUS[:2]), (12.01, STATUS[2:])],
                                 "original guard deadline", overrun=True)

    def test_experimental_guard_rejects_transport_overrun_beyond_absolute_deadline(self):
        self.check_guard_failure([(12, STATUS), (24.01, b"X")],
                                 "absolute inter-request guard deadline", overrun=True)

    def test_experimental_guard_rejects_timeout_overrun_beyond_absolute_deadline(self):
        self.check_guard_failure([(12, STATUS)], "absolute inter-request guard deadline",
                                 timeout_overrun=True)

    def test_experimental_guard_requires_final_quiet_poll_after_journal_stall(self):
        clock = lambda: self.link.clock
        self.link.read = lambda *args, **kwargs: STATUS

        def log(event, **fields):
            if event == "recognized_status":
                self.link.clock += 12

        report = qualification_guard_result()
        with self.assertRaisesRegex(ReceiveError, "without a final receive poll"):
            observe_qualification_guard(self.link, clock, BytesIO(), log, report)
        self.assertEqual(report["outcome"], "FAIL")
        self.assertEqual(self.link.writes, [])

    def test_experimental_guard_rejects_non_timeout_and_cancellation_in_both_phases(self):
        for after_status in (False, True):
            for factory, reason in ((lambda: OSError("disconnected"), "disconnected"),
                                    (KeyboardInterrupt, "KeyboardInterrupt")):
                with self.subTest(after_status=after_status, reason=reason):
                    self.setUp()
                    events = [(1, STATUS)] if after_status else []
                    self.check_guard_failure([*events, (2, factory())], reason)

    def test_experimental_guard_cleanup_fault_during_inconclusive_path(self):
        for resource in ("interface", "lease"):
            with self.subTest(resource=resource):
                self.setUp()
                setattr(self, resource + "_failure", True)
                result = self.execute(experimental_s92_guard=True)
                self.assertEqual(result["status"], "ERROR")
                self.assertEqual(result["error"]["stage"], resource + "_cleanup")
                self.assertEqual(result["channels"]["CH2"]["request_attempts"], 0)
                self.assertEqual(len(self.link.writes), 1)
                self.assertEqual(result["marker"], "RETAINED")
                self.assertFalse(result["cleanup"][resource + "_released"])
                self.assertFalse(any(spec.output.exists() for spec in self.specs))

    def test_experimental_qualification_still_rejects_startup_s92(self):
        self.link.preflight.append(STATUS)
        result = self.execute(experimental_s92_guard=True)
        self.assertEqual(result["inter_request_guard"]["outcome"], "NOT_RUN")
        self.assertEqual(result["error"]["stage"], "preflight")
        self.assertEqual(self.link.writes, [])
        self.assertEqual(result["marker"], "RETAINED")

    def test_experimental_guard_publication_fault_after_two_valid_transfers(self):
        from hantek_dso5k._pair_session import publish_file

        def publish(path, data, digest):
            if path == self.specs[1].output:
                raise OSError("disk full")
            return publish_file(path, data, digest)

        self.scripted_guard([(3, STATUS)])
        with patch("hantek_dso5k._pair_session.publish_file", side_effect=publish):
            result = self.execute(experimental_s92_guard=True)
        self.assertEqual(result["error"]["stage"], "publish")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["request_attempts"], 2)
        self.assertEqual(result["marker"], "CLEARED")
        self.assertTrue(result["channels"]["CH1"]["output_published"])
        self.assertEqual(self.specs[0].output.read_bytes(), self.data["CH1"])
        self.assertFalse(result["channels"]["CH2"]["output_published"])
        self.assertFalse(self.specs[1].output.exists())

    def test_strict_manifest_has_legacy_guard_shape(self):
        result = self.execute()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["schema_version"], "0.1-offline")
        self.assertEqual(set(result["inter_request_guard"]),
                         {"complete", "raw_bytes", "recognized_status_frames", "primary_frames_seen"})

    def test_controller_refuses_live_defaults(self):
        with self.assertRaises(PairBlocked):
            simulate_pair(*self.specs, self.evidence, confirmations=CONFIRMATIONS)


if __name__ == "__main__":
    unittest.main()
