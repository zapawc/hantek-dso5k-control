import json
import unittest

from hantek_dso5k._host import assess_host, Blocked

PY = r"C:\venv\Scripts\python.exe"
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SCRIPT = r"C:\Projects\hantek-dso5k-control\Run-Qualification.ps1"


def row(pid, parent, exe, argv, created=None):
    return {"pid": pid, "ppid": parent, "exe": exe, "argv": argv,
            "created": created or f"2026-11-19T17:00:{pid % 100:02}Z"}


def snapshot(*others, assignment="no_usbip_service"):
    return {"processes": [row(15, 11, PY, [PY, "-m", "hantek_dso5k", "qualify", "readonly"]),
                          row(11, 0, PS, [PS, "-Command", "Get-Item FW-CAP-06"]), *others],
            "devices": [{"instance": "USB\\VID_049F&PID_505A\\FIXTURE", "status": "OK", "service": "WinUSB"}],
            "assignment": assignment}


class OwnershipTests(unittest.TestCase):
    def test_83112_shape_reproduces_old_false_positive_without_claiming_identity(self):
        source = snapshot(row(83112, 2, PS, [PS, "-Command", "Write-Output", SCRIPT, "FW-CAP-06"]))
        report = assess_host(source, 15, PY)
        self.assertIsNone(report["blocked"])
        findings = {f["pid"]: f for f in report["findings"]}
        self.assertEqual(findings[83112]["classification"], "reference_only")
        self.assertEqual(findings[83112]["parent_pid"], 2)
        self.assertEqual(findings[11]["classification"], "verified_launcher_ancestry")
        self.assertNotIn(SCRIPT, json.dumps(report))
        self.assertNotIn("Write-Output", json.dumps(report))

    def test_own_exact_launcher_excluded_but_independent_client_blocks(self):
        source = snapshot()
        source["processes"][1]["argv"] = [PS, "-File", SCRIPT]
        self.assertIsNone(assess_host(source, 15, PY)["blocked"])
        for args in ([PS, "-File", SCRIPT], [PS, "-Command", f"& '{SCRIPT}'"]):
            with self.subTest(args=args):
                source["processes"].append(row(24, 0, PS, args))
                self.assertIsNotNone(assess_host(source, 15, PY)["blocked"])
                source["processes"].pop()

    def test_python_direct_script_vs_path_reference(self):
        lookup = row(24, 0, PY, [PY, "-c", "print('Hantek viewer.py')"])
        invocation = row(25, 0, PY, [PY, "-B", r"C:\Hantek\Viewer.py"])
        self.assertIsNone(assess_host(snapshot(lookup), 15, PY)["blocked"])
        self.assertIsNotNone(assess_host(snapshot(invocation), 15, PY)["blocked"])

    def test_forwarder_must_target_scope(self):
        unrelated = row(24, 0, "TeamViewer.exe", ["TeamViewer.exe"])
        hyperv = row(25, 0, "vmwp.exe", ["vmwp.exe"])
        qemu = row(26, 0, "qemu-system-x86_64.exe",
                   ["qemu-system-x86_64.exe", "usb-host,vendorid=0x049f,productid=0x505a"])
        self.assertIsNone(assess_host(snapshot(unrelated, hyperv), 15, PY)["blocked"])
        self.assertIsNotNone(assess_host(snapshot(qemu), 15, PY)["blocked"])

    def test_reused_parent_is_not_excluded(self):
        source = snapshot()
        source["processes"][1]["created"] = "2026-11-19T17:05:00Z"
        source["processes"][1]["argv"] = [PS, "-File", SCRIPT]
        self.assertIsNotNone(assess_host(source, 15, PY)["blocked"])

    def test_ambiguous_identity_or_assignment_blocks(self):
        for state in ("unknown", "shared", "attached"):
            self.assertIsNotNone(assess_host(snapshot(assignment=state), 15, PY)["blocked"])
        source = snapshot()
        source["devices"].append(source["devices"][0])
        self.assertIsNotNone(assess_host(source, 15, PY)["blocked"])
        with self.assertRaises(Blocked):
            assess_host(snapshot(), 15, r"C:\other\python.exe")
