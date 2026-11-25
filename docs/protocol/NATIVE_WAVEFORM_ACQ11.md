# ACQ-11 Workbench native acquisition

The isolated `hantek_dso5k._acq11` module operates the retained 3.40.0 (151009.0) DSO5102P with both probes attached to the compensation output. It expects CH1 10× and CH2 1×, both channels on and DC-coupled, edge rising on CH1, Normal acquisition and MATH off. It applies only allowlisted scale/probe fields to a freshly read S81 record when configuration differs. It verifies those fields after S91, captures and analyzes the displayed response, then restores the original allowlisted fields from another fresh S81. It verifies the full configuration and original running or stopped condition. Stopped codes 0 and 5 are distinct observations. [Development authorization](../adr/0006-workbench-acquisition-development.md)

In the approved Windows environment, use a clean pinned source checkout and a new private directory on the interruption-state volume.

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:HANTEK_ACQ11_SOURCE = '<approved-full-commit>'
python -B -m hantek_dso5k._acq11 full --authorization ACQ-11 --evidence '<fresh-local-evidence-directory>'
```

`state` reads configuration only. `running` exercises setting, running acquisition and restore. `full` also sends one software SINGLE key, checks for Stop and retrieves both stopped slots before restoring. No old acquisition can be recovered by restoring settings. The procedure does not publish a release or replace the established command surface.

The adapter uses the existing lease, exact-S92 guard and verified finalization. It allows an empty startup or one exact S92 followed by a fresh 12-second quiet interval. Subsequent commands require complete reply framing and a 550 ms quiet response tail. It retains raw S01/S11/S13/S22/S82 exchanges, settings and outcomes. A failed or ambiguous operation prevents automatic follow-up writes and retains the marker. No automatic reset, retry or firmware action occurs.

ACQ-11's aggregate PASS covers waveform acquisition, expected periodicity and relative probe evidence, and configuration restoration. Check `running_validation.voltage`, `single_validation.voltage` and `ch1_vpp_crosscheck` independently. Vpp, extrema and mean use the documented 25 display codes/division and S81 offsets and probe factors. Voltage values are provisional when the observed amplitude differs from the nominal fixture or the device readout disagrees. The recorded window and count give the relative sample interval. Probe assignment is supported by the measured BNC amplitude ratio. CH2 S22 values can be unavailable while native waveform data are present. Neither periodicity nor the shared response proves same-shot acquisition. [Pinned independent protocol reference](https://github.com/kooscode/hantek-dso5000p-linux/blob/b0facf139fb1b3da0a8c3ad0fa77a9eda76e1c46/protocol/PROTOCOL.md#L72-L139)
