from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from hantek_dso5k._framing import frame
from hantek_dso5k._service_capture import Unsupported, s22_probe, snapshot, triggered
from hantek_dso5k.service import LocalServer, Service, measurement_value, owned, parse_job
from hantek_dso5k.service_client import Client


def settings(state=3, *, scale=7):
    raw = bytearray(208)
    for index, value in {0: 1, 1: scale, 5: 1, 10: 1, 11: scale,
                         15: 1, 20: state, 160: 15, 206: 48, 207: 1}.items():
        raw[index] = value
    return bytes(raw)


def slot(counts):
    return frame(0x82, b"\x00" + len(counts).to_bytes(3, "little")) + frame(0x82, b"\x01\x04" + bytes(counts))


def wait_for(predicate, duration=3):
    deadline = monotonic() + duration
    while monotonic() < deadline:
        if predicate():
            return
        sleep(.005)
    raise AssertionError("Test condition did not become true")


class ServiceTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def make(self, **kwargs):
        return Service(self.folder, interval=.01, stale_after=2, measurements=["frequency"], **kwargs)

    def test_display_values_units_and_unavailable_zero(self):
        self.assertEqual(measurement_value("1.000KHz", "Hz"), (1000., None))
        self.assertEqual(measurement_value("-320mV", "V"), (-.32, None))
        self.assertEqual(measurement_value("1.00ms", "s"), (.001, None))
        self.assertEqual(measurement_value("?", "V"), (None, "device_unavailable"))
        self.assertEqual(measurement_value("0.000Hz", "Hz"), (None, "zero_unverified"))
        self.assertEqual(measurement_value("2.2V", "Hz"), (None, "invalid_display_value"))

    def test_cache_ages_and_separates_last_valid_from_unavailable(self):
        now = [0.]
        service = self.make(clock=lambda: now[0])
        service.state = "MONITORING"
        session = Mock()
        session.settings.return_value = settings()
        session.measurement.side_effect = ["1kHz", "0Hz"]
        service._poll(session, 0)
        first = service.latest()["measurements"][0]
        self.assertEqual(first["value"], 1000)
        self.assertEqual(first["device_freshness"], "UNKNOWN")
        now[0] = 3
        self.assertTrue(service.latest()["measurements"][0]["stale"])
        service._poll(session, 0)
        second = service.latest()["measurements"][0]
        self.assertIsNone(second["value"])
        self.assertEqual(second["last_good"]["value"], 1000)
        self.assertEqual(second["last_good"]["age_s"], 3)
        self.assertTrue(second["last_good"]["stale"])
        self.assertEqual(second["configuration"]["s81_sha256"], first["configuration"]["s81_sha256"])
        service.state = "CAPTURING"
        self.assertTrue(service.latest()["measurements"][0]["stale"])

    def test_monitor_pauses_for_job_refuses_parallel_job_and_resumes(self):
        service = self.make()
        session = Mock()
        session.settings.return_value = settings()
        session.measurement.return_value = "1kHz"
        started, complete = Event(), Event()

        def capture(*args):
            started.set()
            self.assertTrue(complete.wait(3))
            return {"same_shot": "UNPROVEN"}

        session.evidence = self.folder
        with patch("hantek_dso5k.service.snapshot", side_effect=capture):
            worker = Thread(target=service.worker, args=(lambda svc: svc.loop(session),))
            worker.start()
            try:
                wait_for(lambda: service.status()["poll_count"] >= 1)
                request = service.submit({"kind": "snapshot"})
                self.assertTrue(started.wait(3))
                polls = service.status()["poll_count"]
                self.assertEqual(service.status()["state"], "CAPTURING")
                self.assertTrue(service.latest()["measurements"][0]["stale"])
                with self.assertRaises(Unsupported):
                    service.submit({"kind": "snapshot"})
                sleep(.025)
                self.assertEqual(service.status()["poll_count"], polls)
                complete.set()
                wait_for(lambda: service.status()["poll_count"] > polls)
                self.assertEqual(service.job(request["id"])["state"], "SUCCEEDED")
                self.assertEqual(service.status()["resume_count"], 1)
            finally:
                complete.set()
                service.shutdown()
                worker.join(3)
                self.assertFalse(worker.is_alive())
        self.assertEqual(service.status()["state"], "STOPPED")
        self.assertFalse(service.status()["connected"])

    def test_shutdown_cancels_queued_without_starting_it(self):
        service = self.make()
        service.state = "MONITORING"
        queued = service.submit({"kind": "snapshot"})
        service.shutdown()
        self.assertEqual(service.job(queued["id"])["state"], "CANCELLED")
        with self.assertRaises(Unsupported):
            service.submit({"kind": "snapshot"})

    def test_owned_worker_finalizes_after_both_releases(self):
        service = self.make()
        marker = self.folder / "state" / "interruption.json"
        evidence = self.folder / "private"
        evidence.mkdir()
        service.evidence = evidence
        actions = []

        @contextmanager
        def hold():
            actions.append("lease")
            yield
            actions.append("release_lease")

        @contextmanager
        def claim():
            self.assertTrue(marker.exists())
            actions.append("claim")
            yield object()
            actions.append("release_interface")

        with patch("hantek_dso5k.service.Session.startup"), patch.object(service, "loop"):
            service.worker(lambda svc: owned(svc, "synthetic", hold=hold, claim=claim,
                                            screen=lambda: {"blocked": None}, marker=marker))
        self.assertEqual(actions, ["lease", "claim", "release_interface", "release_lease"])
        result = json.loads((evidence / "native_manifest.json").read_bytes())
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(marker.exists())

    def test_connection_failure_retains_marker_and_fault_blocks_work(self):
        service = self.make()
        marker = self.folder / "state" / "interruption.json"
        evidence = self.folder / "private"
        evidence.mkdir()
        service.evidence = evidence

        @contextmanager
        def hold():
            yield

        @contextmanager
        def claim():
            raise OSError("simulated connection failure")
            yield

        service.worker(lambda svc: owned(svc, "synthetic", hold=hold, claim=claim,
                                        screen=lambda: {"blocked": None}, marker=marker))
        self.assertTrue(marker.exists())
        self.assertEqual(service.status()["state"], "FAULT")
        with self.assertRaises(Unsupported):
            service.submit({"kind": "snapshot"})

    def test_unsettled_capture_fault_disables_polling_and_retains_outcome(self):
        service = self.make()
        session = Mock(settled=False, evidence=self.folder)
        session.settings.return_value = settings()
        service.state = "MONITORING"
        job = service.submit({"kind": "snapshot"})
        with patch("hantek_dso5k.service.snapshot", side_effect=OSError("short read")):
            service.worker(lambda svc: svc.loop(session))
        self.assertEqual(service.status()["state"], "FAULT")
        self.assertEqual(service.job(job["id"])["state"], "FAILED")
        session.measurement.assert_not_called()

    def test_triggered_preserves_waveform_then_verifies_restore(self):
        session = Mock(settled=True)
        baseline, frozen = settings(), settings(5)
        session.settings.side_effect = {"single_1": frozen, "before_restore": frozen,
                                        "restored": baseline}.__getitem__
        session.set_fields.side_effect = [baseline, frozen]
        marked = []
        result = {}
        with patch("hantek_dso5k._service_capture.snapshot",
                   return_value={"availability": {"CH1": True, "CH2": True}}):
            triggered(session, baseline, "job", {"path": "auto", "allow_overwrite": False},
                      result, marked.append)
        self.assertEqual(marked, [True, False])
        self.assertEqual(result["method"], "native_s02")
        self.assertTrue(result["restoration_verified"])
        session.key.assert_any_call("restore_run_state", 19)

    def test_stopped_overwrite_and_unverified_profile_require_explicit_supported_operation(self):
        session = Mock()
        request = {"path": "auto", "allow_overwrite": False}
        with self.assertRaisesRegex(Unsupported, "overwrite"):
            triggered(session, settings(5), "job", request, {}, Mock())
        raw = bytearray(settings())
        raw[188] = 2
        with self.assertRaisesRegex(Unsupported, "profile"):
            triggered(session, raw, "job", request, {}, Mock())
        session.set_fields.assert_not_called()
        session.key.assert_not_called()

    def test_snapshot_keeps_unknown_scale_raw_only(self):
        session = Mock(evidence=self.folder, count=1)
        wire = slot([1, 2]) + slot([3, 4]) + frame(0x82, b"\x03\x02") + frame(0x82, b"\x02\x04")
        session.native_wire.return_value = wire
        (self.folder / "001-snapshot.rx").write_bytes(wire)
        self.assertTrue(snapshot(session, settings(scale=8), "job")["availability"]["CH2"])
        result = json.loads((self.folder / "waveform.json").read_bytes())
        for channel in result["channels"][:2]:
            self.assertIsNone(channel["engineering"])
            self.assertIsNotNone(channel["raw"])

    def test_optional_s22_comparison_requires_running_and_restores_fixture_edits(self):
        session = Mock(settled=True)
        with self.assertRaisesRegex(Unsupported, "running"):
            s22_probe(session, settings(5), {"configure_fixture": True}, {}, Mock())
        session.set_fields.assert_not_called()
        base = settings()
        current = bytearray(base)
        current[11], current[15] = 9, 0
        current = bytes(current)
        session.set_fields.side_effect = [current, base]
        session.settings.side_effect = [current, base]
        session.measurement.return_value = "?"
        calls = []
        result = {}
        s22_probe(session, base, {"configure_fixture": True}, result, calls.append)
        self.assertEqual(calls, [True, False])
        self.assertTrue(result["restoration_verified"])
        self.assertEqual(result["configured"]["channels"]["CH2"]["software_probe"], 1)
        self.assertEqual(len(result["reads"]), 8)
        session.measurement.assert_any_call(4, 1, mode=1)

    def test_restore_failure_keeps_service_faulted(self):
        service = self.make()
        session = Mock(settled=True, evidence=self.folder)
        session.settings.return_value = settings()
        service.state = "MONITORING"
        job = service.submit(parse_job({"kind": "triggered"}))

        def failed_restore(session, baseline, job_id, request, result, restoration_required):
            restoration_required(True)
            raise RuntimeError("Restoration readback differs")

        with patch("hantek_dso5k.service.triggered", side_effect=failed_restore):
            service.worker(lambda svc: svc.loop(session))
        self.assertEqual(service.status()["state"], "FAULT")
        self.assertTrue(service.restoration_owed)
        self.assertEqual(service.job(job["id"])["result"]["error"]["detail"], "Restoration readback differs")
        session.measurement.assert_not_called()

    def test_http_auth_api_serialization_and_concurrent_clients(self):
        service = self.make()
        service.state = "MONITORING"
        with LocalServer(service, "token") as server:
            config = self.folder / "client.json"
            config.write_text(json.dumps({"endpoint": f"http://127.0.0.1:{server.server_port}", "token": "token"}))
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
            thread.start()
            try:
                client = Client(config)
                inactive = Client(config)
                inactive.headers["Authorization"] = "Bearer wrong"
                with self.assertRaises(HTTPError) as failure:
                    inactive.json("status")
                self.assertEqual(failure.exception.code, 401)
                statuses = []
                readers = [Thread(target=lambda: statuses.append(Client(config).json("status"))) for _ in range(4)]
                for reader in readers:
                    reader.start()
                for reader in readers:
                    reader.join(3)
                self.assertEqual(len(statuses), 4)
                self.assertTrue(all(row["poll_count"] == 0 for row in statuses))
                key = client.json("jobs", {"kind": "snapshot"})["id"]
                self.assertEqual(client.json("jobs/" + key)["state"], "QUEUED")
                with self.assertRaises(HTTPError) as failure:
                    client.json("jobs", {"kind": "snapshot"})
                self.assertEqual(failure.exception.code, 409)
                self.assertEqual(client.json("shutdown", {}), {"state": "STOPPING"})
                wait_for(lambda: service.stopping)
                self.assertEqual(client.json("jobs/" + key)["state"], "CANCELLED")
            finally:
                server.shutdown()
                thread.join(3)

    def test_invalid_api_jobs_are_rejected(self):
        for request in ([], {"kind": "snapshot", "other": True}, {"kind": "triggered", "path": "other"},
                        {"kind": "triggered", "allow_overwrite": "true"}, {"kind": "run-state", "running": 1}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                parse_job(request)
