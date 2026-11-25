import io
import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from hantek_dso5k import _dw_profile as dw
from hantek_dso5k._acq11 import Session
from hantek_dso5k._framing import STATUS, frame
from hantek_dso5k.service import LocalServer, Service, Unsupported, write_discovery
from hantek_dso5k.service_client import Client
from hantek_dso5k.service_monitor import plot_artifact
from test_service import settings


class PlatformTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.clock = Mock(return_value=20.)
        self.worker = Service(self.folder, interval=.25, stale_after=10, measurements=["frequency", "vpp"],
                              source="both", clock=self.clock, instrument_id="bench-scope")
        self.worker.state, self.worker.connected = "MONITORING", True
        self.worker._settings(settings())
        self.server = LocalServer(self.worker, "dummy-token")
        self.addCleanup(self.server.server_close)
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        config = self.folder / "client.json"
        write_discovery(config, {"endpoint": f"http://127.0.0.1:{self.server.server_port}", "token": "dummy-token"})
        self.dw = Client(config, profile=True)
        self.legacy = Client(config)
        self.snapshot = {"kind": "hantek.snapshot", "arguments": {}, "timeout_s": 30}

    def assert_problem(self, req, status, code):
        with self.assertRaises(HTTPError) as caught:
            req()
        with caught.exception as response:
            self.assertEqual(response.code, status)
            self.assertEqual(response.headers.get_content_type(), "application/problem+json")
            body = json.load(response)
        self.assertEqual((body["status"], body["code"]), (status, code))

    def test_identity_reconnection_and_capabilities(self):
        first = self.dw.json("identity")
        self.assertEqual(first, self.dw.json("identity"))
        self.assertEqual(first["instrument_id"], "bench-scope")
        self.assertEqual(first["profile_version"], "0.1")
        next_instance = Service(self.folder, interval=.25, stale_after=10, measurements=["frequency"],
                                instrument_id="bench-scope")
        self.assertNotEqual(first["instance_id"], next_instance.instance_id)
        self.assertEqual(first["instrument_id"], next_instance.instrument_id)
        caps = self.dw.json("capabilities")
        self.assertTrue(all(not job["cancel_supported"] and job["max_timeout_s"] > 0 for job in caps["jobs"]))
        self.assertFalse(caps["extensions"][dw.EXT]["external_arm_ready_qualified"])
        self.assertEqual(self.dw.json("settings")["values"], self.legacy.json("settings")["settings"])

    def test_external_job_is_scoped_to_the_opt_in_worker(self):
        request = {"kind": "hantek.external", "arguments": {"event_id": 42, "allow_overwrite": True},
                   "timeout_s": 120}
        self.assert_problem(lambda: self.dw.json("jobs", request), 409, "operation_conflict")
        self.worker.external_fixture = Mock()
        caps = self.dw.json("capabilities")
        self.assertTrue(caps["extensions"][dw.EXT]["external_arm_ready_qualified"])
        self.assertIn("hantek.external", [job["kind"] for job in caps["jobs"]])
        self.assertIn("external", self.legacy.json("capabilities")["operations"])
        self.assert_problem(lambda: self.dw.json("jobs", {
            **request, "arguments": {"event_id": True, "allow_overwrite": True},
        }), 422, "invalid_arguments")
        self.assert_problem(lambda: self.dw.json("jobs", {
            **request, "arguments": {"event_id": 42},
        }), 409, "operation_conflict")
        admission = self.dw.json("jobs", request)
        self.assertEqual(admission["state"], "queued")
        job = self.worker.job(admission["job_id"])
        self.assertEqual(job["request"]["event_id"], 42)
        self.assertEqual(job["request"]["path"], "auto")

    def test_auth_host_origin_and_fault_cache(self):
        for headers, status, code in [
            ({}, 401, "invalid_credentials"),
            ({"Authorization": "Bearer dummy-token", "Host": "example.test"}, 400, "invalid_host"),
            ({"Authorization": "Bearer dummy-token", "Origin": "null"}, 403, "invalid_origin"),
        ]:
            request = Request(self.dw.endpoint + dw.PREFIX + "/identity", headers=headers)
            self.assert_problem(lambda: self.dw.opener.open(request, timeout=2), status, code)
        self.worker.state, self.worker.connected, self.worker.error = "FAULT", False, {"type": "RuntimeError"}
        self.assertEqual(self.dw.json("status")["state"], "fault")
        self.assertIsNotNone(self.dw.json("settings")["configuration_id"])
        self.assertEqual(self.dw.json("telemetry"), {"samples": []})
        self.assert_problem(lambda: self.dw.json("jobs", self.snapshot), 503, "instrument_unavailable")

    def test_concurrent_cache_reads_do_not_poll(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            documents = list(pool.map(lambda i: (self.dw.json("telemetry") if i % 2
                                                 else self.legacy.json("measurements")), range(24)))
        self.assertEqual(len(documents), 24)
        self.assertEqual(self.worker.poll_count, 0)

    def test_shared_queue_location_and_shutdown_admission(self):
        request = Request(self.dw.endpoint + dw.PREFIX + "/jobs", headers=self.dw.headers,
                          data=json.dumps(self.snapshot).encode())
        with self.dw.opener.open(request, timeout=2) as response:
            self.assertEqual(response.status, 202)
            job_id = json.load(response)["job_id"]
            self.assertEqual(response.headers["Location"], f"{dw.PREFIX}/jobs/{job_id}")
        self.assertEqual(self.legacy.json(f"jobs/{job_id}")["state"], "QUEUED")
        with self.assertRaises(HTTPError) as caught:
            self.legacy.json("jobs", {"kind": "snapshot"})
        self.assertEqual(caught.exception.code, 409)
        self.assert_problem(lambda: self.dw.json(f"jobs/{job_id}/cancel", {}), 404, "not_found")
        self.assertEqual(self.dw.json("shutdown", {})["state"], "stopping")
        self.assertEqual(self.dw.json(f"jobs/{job_id}")["state"], "cancelled")
        self.assertIsNone(self.worker.pending)
        self.assert_problem(lambda: self.dw.json("jobs", self.snapshot), 409, "operation_conflict")

    def test_invalid_timeout_unknown_options_and_overwrite_refusal(self):
        for payload in ({**self.snapshot, "timeout_s": True}, {**self.snapshot, "timeout_s": 61},
                        {**self.snapshot, "arguments": {"running": True}}):
            self.assert_problem(lambda: self.dw.json("jobs", payload), 422, "invalid_arguments")
        self.worker._settings(settings(5))
        self.assert_problem(lambda: self.dw.json("jobs", {"kind": "hantek.triggered",
                                                          "arguments": {}, "timeout_s": 90}),
                            409, "operation_conflict")
        self.assertIsNone(self.worker.pending)

    def test_admitted_job_timeout_cannot_begin_device_work(self):
        job = self.worker.submit({"kind": "snapshot"}, budget=10)
        self.clock.return_value = 31.
        session = Mock(evidence=self.folder, settled=True)
        result, fatal = self.worker._perform(session, job["id"])
        session.settings.assert_not_called()
        self.assertEqual(result["error"]["type"], "JobTimeout")
        self.assertIsNone(fatal)

    def test_distinct_sources_zero_staleness_and_last_valid(self):
        self.worker.next_wave = float("inf")
        session = Mock()
        session.measurement.side_effect = ["1kHz", "0Hz"]
        with patch("hantek_dso5k.service.utc", return_value="2026-01-01T00:00:00+00:00"):
            self.worker._poll(session, 0)
        self.clock.return_value = 21.
        with patch("hantek_dso5k.service.utc", return_value="2026-01-01T00:00:01+00:00"):
            self.worker._poll(session, 0)
        metric = {"vpp_v": 0., "frequency_hz": None, "clipped": False, "physical_voltage_verified": True,
                  "sample_count": 20, "sample_interval_s": .01, "coverage_s": .19}
        sample = {"availability": {"CH1": True, "CH2": True}, "timing": None, "acquisition_id": "host-id",
                  "derived_measurements": {"CH1": metric, "CH2": metric}}
        with patch("hantek_dso5k.service.snapshot", return_value=sample):
            self.worker._poll_waveform(session)
        records = {row["series_id"]: row for row in self.dw.json("telemetry")["samples"]}
        ambiguous = records["ch1.frequency.s22_instrument_cache"]
        zero = records["ch1.vpp.native_waveform"]
        self.assertEqual((ambiguous["value"], ambiguous["validity"]), (None, "ambiguous"))
        self.assertEqual(ambiguous["last_valid"]["value"], 1000)
        self.assertNotEqual(ambiguous["last_valid"]["observed_at"], ambiguous["observed_at"])
        self.assertEqual((zero["value"], zero["validity"], zero["device_freshness"]), (0., "valid", "unknown"))
        self.assertEqual(zero["extensions"][dw.EXT]["sample_count"], 20)
        self.assertEqual(len(self.legacy.json("measurements")["measurements"]), 1)
        self.worker.state = "CAPTURING"
        self.assertTrue(all(row["stale"] for row in self.dw.json("telemetry")["samples"]))
        self.worker.state, self.worker.refresh_after = "MONITORING", 22.
        self.assertTrue(all(row["stale"] for row in self.dw.json("telemetry")["samples"]))

    def test_restoration_failure_keeps_successful_capture_artifact(self):
        item = self.worker.submit({"kind": "triggered", "path": "auto", "allow_overwrite": True}, budget=90)
        ident = item["id"]
        capture = self.folder / ident
        capture.mkdir()
        data = b"waveform"
        (capture / "waveform.json").write_bytes(data)
        self.worker.jobs[ident].update(state="FAILED", result={"acquisition_outcome": "succeeded",
            "restoration_outcome": "failed", "artifacts": {"waveform.json": {
                "bytes": len(data), "sha256": sha256(data).hexdigest()}},
            "error": {"type": "RuntimeError"}})
        received = self.dw.json(f"jobs/{ident}")
        result = received["result"]
        self.assertEqual((result["acquisition"], result["restoration"]), ("succeeded", "failed"))
        artifact_id = result["artifacts"][0]["artifact_id"]
        route = f"jobs/{ident}/artifacts/{artifact_id}"
        self.assertEqual(self.dw.request(route), data)
        for altered in (b"overlong file", b"samework"):
            (capture / "waveform.json").write_bytes(altered)
            self.assert_problem(lambda: self.dw.request(route), 409, "artifact_integrity_mismatch")
        self.assertEqual(self.dw.json(f"jobs/{ident}"), received)

    def test_terminal_plot_keeps_flat_zero_and_engineering_units(self):
        lines = plot_artifact(b'{"channels":[{"channel":"CH1","engineering":{"values":[0,0,0]}}]}',
                              "application/json")
        self.assertEqual(lines, ["CH1 [V (configured scale)] +++"])

    def test_monitor_rotation_keeps_job_artifacts_and_current_fault_evidence(self):
        session = Session(Mock(), self.folder, io.StringIO())
        job = self.folder / "independent-job"
        job.mkdir()
        (job / "waveform.json").write_text("preserved", encoding="utf-8")
        for index in range(81):
            with self.worker._monitor_evidence(session):
                (session.evidence / f"{index}.rx").write_bytes(b"monitor")
        self.assertEqual(session.evidence, self.folder)
        self.assertEqual(len(list(self.folder.glob("monitor-*"))), 4)
        self.assertFalse((self.folder / "monitor-0" / "0.rx").exists())
        self.assertTrue((self.folder / "monitor-1" / "80.rx").exists())
        self.assertTrue((job / "waveform.json").exists())


class TimingTests(TestCase):
    def exercise(self, replies, *, rapid=True):
        with TemporaryDirectory() as directory:
            elapsed = [0.]

            def read(size, *, timeout_ms):
                stop = elapsed[0] + timeout_ms / 1000.
                if replies and replies[0][0] <= stop:
                    elapsed[0], reply = replies.pop(0)
                    return reply
                elapsed[0] = stop
                raise TimeoutError

            link = Mock(write=lambda wire: len(wire), read=read)
            journal = io.StringIO()
            session = Session(link, Path(directory), journal, clock=lambda: elapsed[0])
            session.rapid_reads = rapid
            result = session.measurement(1, 0)
            fields = json.loads(journal.getvalue().splitlines()[-1])
            return result, elapsed[0], fields

    def test_complete_primary_and_fragmented_s92_restart_quiet(self):
        response = frame(0xA2, b"1KHz")
        value, elapsed, result = self.exercise([(0.01, response), (.10, STATUS[:2]), (.17, STATUS[2:])])
        self.assertEqual(value, "1KHz")
        self.assertEqual(result["status_offsets"], [len(response)])
        self.assertGreaterEqual(elapsed, .72)
        self.assertLess(elapsed, .73)
        conservative = self.exercise([(.01, response)], rapid=False)[1]
        self.assertGreaterEqual(conservative, 1.1)
        self.assertLess(conservative, 1.105)

    def test_wrong_opcode_and_late_status_do_not_admit_next_command(self):
        response = frame(0xA2, b"1KHz")
        with self.assertRaisesRegex(RuntimeError, "unexpected opcode"):
            self.exercise([(.01, response), (.1, frame(0x81, settings()))])
        with self.assertRaisesRegex(RuntimeError, "quiet response tail"):
            self.exercise([(.30, response), (.75, STATUS)])
