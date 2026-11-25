# HANTEK-ADR-006 · Workbench acquisition development

**Status: Accepted for ACQ-11**

ACQ-11 authorizes bounded, iterative development and supervised hardware testing on the retained DSO5102P with its probe-compensation fixture. The owner accepts a possible recoverable acquisition lockup and operator-controlled restart. Capture evidence before reconciliation, preserve the cooperative lease and interruption controls, and stop sending commands when history is ambiguous. Routine recovery within this assignment is supported by recorded evidence. Keep firmware, calibration, electrical configuration outside the agreed fixture, and production defaults outside the experiment.

Use native running and stopped display-window waveforms where demonstrated. Preserve and validate the waveform before modifying its settings or acquisition state. Use frozen export as a separate path when stopped native retrieval is unavailable. Verify restoration from fresh readback and retain the original stopped or running condition. A previously overwritten acquisition cannot be restored by changing settings.

Supervised fixture results establish only the observed Workbench capability. A periodic fixture does not establish same-shot channel identity, absolute calibration, persistent service behavior or unattended reliability. [Qualification levels](0005-proportionate-instrument-qualification.md) · [Workbench workflow](../protocol/NATIVE_WAVEFORM_ACQ11.md)
