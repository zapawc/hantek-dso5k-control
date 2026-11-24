"""ACQ-06 simulated sequential S10 controller; no live device access."""
from collections import deque
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k._file_replay import CHUNK_BYTES, csv_request
from hantek_dso5k._pair_session import PairBlocked, PairS10IO, PairSpec, PROFILES, simulate_pair
from hantek_dso5k._retrieve import CONFIRMATIONS


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

    def execute(self, *, confirmations=CONFIRMATIONS, specs=None, marker=None, evidence=None):
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
                             clock=Clock(), marker=marker or self.marker)

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

    def test_controller_refuses_live_defaults(self):
        with self.assertRaises(PairBlocked):
            simulate_pair(*self.specs, self.evidence, confirmations=CONFIRMATIONS)


if __name__ == "__main__":
    unittest.main()
