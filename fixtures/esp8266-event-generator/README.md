# ACQ-12 ESP8266 event generator

The USB-powered DOIT ESP8266MOD board has an ESP8266EX, 4 MB SPI flash and a Silicon Labs CP2102 USB bridge. PlatformIO's NodeMCU 1.0 target matches the flash size and D5–D7 pin mapping. The firmware schedules GPIO changes with ESP8266 timer 1 and disables Wi-Fi during setup.

## Supervised wiring

| Signal | Fixture header | DSO5102P connector | Physical probe |
|---|---|---|---|
| CH1 pattern | D5 / GPIO14 | CH1 | 1× |
| CH2 pattern | D6 / GPIO12 | CH2 | 1× |
| External trigger | D7 / GPIO13 | EXT TRIG | 10× |
| Common return | GND | All probe ground clips | — |

The bench fixture uses direct passive probes with no series or pull-down resistors. The operator confirmed a common fixture ground and USB power only. GPIO outputs are nominally 0–3.3 V. The EXT probe attenuates that to about 0.33 V at the BNC, above Hantek's 200 mV EXT sensitivity. The scope's input grounds are earth-referenced. Keep the scope stopped during fixture resets and uploads, when the GPIO can float. This envelope uses the attenuated EXT path. It has no qualification for driving the EXT BNC directly at 1×. [DSO5102P specifications](https://www.hantek.com/products/detail/96) · [DSO5000P manual](https://www.hantek.com/Product/5000P/DSO5000P_Manual.pdf)

Use `STOP` to idle the fixture. For a physical stop, disconnect fixture USB power and remove the probe tips before the common ground. Restore the original scope settings and stopped or running condition through fresh readback. An overwritten waveform cannot be recovered from settings.

## Build and serial operation

```powershell
pio run
python -m esptool --chip esp8266 --port COM10 --no-stub flash-id
python -m esptool --chip esp8266 --port COM10 --baud 115200 write-flash 0x0 .pio/build/nodemcuv2/firmware.bin
python fixture.py --port COM10 info
python fixture.py --port COM10 status
python -m unittest discover -s .
```

Use esptool 5.1.0 and pyserial 3.5. Confirm the chip identity against the authorized fixture before uploading. COM10 is this bench's port assignment.

Serial is 115200 baud, 8N1, with ASCII requests ending in LF or CRLF. Each nonempty request gets a newline-terminated JSON object.

| Request | Effect |
|---|---|
| `INFO` | Report firmware name, version and protocol version. |
| `STATUS` | Report activity, last accepted ID, last completed single-event ID and GPIO input level mask. Zero IDs mean none since boot. |
| `EVENT <id>` | Start one event with a decimal ID from 1 through 65535. |
| `PATTERN <id>` | Repeat a diagnostic event 150 times at a nominal 40 ms period. The diagnostic ends after about six seconds. |
| `IDLE` or `STOP` | Cancel activity and drive all outputs low. |

An accepted command claims its ID even if stopped. The firmware rejects IDs at or below the last accepted value until reset. Use distinct IDs across resets. If an acknowledgement is lost, query status and record the acquisition as indeterminate. Firmware status gives execution evidence, while the captured pulse sequence provides physical event identity.

## Event format

D7 rises at the event start for a nominal 500 µs. Both analog channels start their first pulse about 1 ms later. That pulse lasts 600 µs on D5 and 800 µs on D6, identifying the channel. Sixteen nominal 1 ms slots follow, MSB first. Both analog outputs rise at each slot and stay high for 200 µs for zero or 400 µs for one. All outputs are low at the end of the nominal 18 ms sequence.

`expected_pulses` gives nominal edge pairs relative to EXT. `decode_pulses` checks observed widths and adjacent slot spacing. The decoder evaluates neighboring edges to allow the observed systematic timer overhead, while retaining a tolerance below half the separation between valid widths. The capture analyzer compares the decoded ID and signature with the requested event and expected channel. Timing derived from a scope record is relative and remains subject to its sample interval, probe loading and clock uncertainty.

## Scope acquisition and service

Use the retained DSO5102P ordinary YT 4K profile. The runner sets CH1 and CH2 to 1×, DC coupling and distinct scales. It uses EXT, rising edge and the retained trigger mode. The front panel displayed 96 mV for the baseline threshold code. SEC/DIV keys apply the physical timebase. S11 timebase readback alone does not confirm that the timebase changed.

Run on Windows from the clean tested source with the usual Hantek WinUSB dependencies and pyserial. The runner accepts a SHA-256 of the scope's independently reviewed Ready display region. Obtain a reference by running one no-event trial and reviewing its preserved screenshot at the correct physical setup. The runner requires matching display pixels, graphics integrity and the expected waiting state before it sends an event. It preserves the stopped native analog records first, or uses the stopped CSV fallback when the reviewed CSV menu and media are available.

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:HANTEK_ACQ12_SOURCE = git rev-parse HEAD
python -B fixtures/esp8266-event-generator/bench.py --port COM10 --mode external --overwrite-stopped --evidence <new-private-directory>
python -B fixtures/esp8266-event-generator/bench.py --port COM10 --mode external --ready-digest <reviewed-digest> --event <new-id> --overwrite-stopped --evidence <new-private-directory>
python -B fixtures/esp8266-event-generator/bench.py serve --fixture-port COM10 --ready-digest <reviewed-digest> --live --source-commit $env:HANTEK_ACQ12_SOURCE --evidence <new-private-service-directory>
```

The fixture-enabled launch adds `external` to the existing local service worker. The legacy API accepts `{"kind":"external","event_id":42,"path":"auto","allow_overwrite":true}`. The Workbench API accepts `{"kind":"hantek.external","arguments":{"event_id":42,"path":"auto","allow_overwrite":true},"timeout_s":160}`. Use the private discovery file and the existing serialized job interface. Explicit overwrite consent accounts for the starting scope's stopped acquisition. The default Hantek service launch retains its previous capabilities.

The test harness preserves screen frames, raw waveform replies, sample records, decoded event provenance, relative edge timing, and verified restoration results. An observed File List popup can block SINGLE after a stopped export. The runner uses the CSV control only with the observed popup signature and corroborating stopped menu state. Unknown display states require supervised reconciliation. Fixture firmware, scope acquisition and long-running service scope remain limited to the demonstrated setup.
