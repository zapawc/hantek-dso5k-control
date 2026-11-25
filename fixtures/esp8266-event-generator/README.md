# ACQ-12 ESP8266 event generator

The verified fixture is a DOIT ESP8266MOD development board with an ESP8266EX, 4 MB SPI flash and a Silicon Labs CP2102 USB bridge. Build for PlatformIO's NodeMCU 1.0 target, which matches this board's flash size and D5–D7 pin mapping. The firmware uses ESP8266 timer 1 to schedule GPIO register changes. Wi-Fi is disabled during setup.

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

The serial protocol is 115200 baud, 8N1, with ASCII requests ending in LF or CRLF. Each nonempty request gets a newline-terminated JSON object.

| Request | Effect |
|---|---|
| `INFO` | Report firmware name, version and protocol version. |
| `STATUS` | Report active state, last accepted ID, last completed ID and GPIO input level mask. Zero IDs mean none since boot. |
| `EVENT <id>` | Start one event with a decimal ID from 1 through 65535. |
| `IDLE` or `STOP` | Cancel a pending event and drive all outputs low. |

An accepted event claims its ID even if stopped. The firmware rejects IDs at or below the last accepted value until reset. Use distinct IDs across resets to keep physical captures distinguishable. If the acknowledgement is lost, query status and treat the acquisition as indeterminate. A completion report is software evidence for the timer sequence.

## Event format

D7 rises at event start and stays high for a nominal 500 µs. Both analog channels start their first pulse 1 ms after the trigger. The first pulse lasts 600 µs on D5 and 800 µs on D6, identifying the channel. Sixteen 1 ms slots follow, MSB first. Both analog outputs rise at each slot start and stay high for 200 µs for zero or 400 µs for one. All outputs are low by the end of the nominal 18 ms sequence.

`expected_pulses` gives edge pairs relative to EXT. `decode_pulses` accepts measured rising and falling edges in microseconds, checks the channel signature and slot spacing, and returns channel and event ID. Compare both results with the requested ID and expected channel order. Timer interrupt latency, probe loading and the scope sample interval require physical measurement for timing claims.

## Proposed supervised wiring

| Signal | Header on fixture | DSO5102P connection | Probe switch |
|---|---|---|---|
| CH1 pattern | D5 / GPIO14 | CH1 tip after 330 Ω series resistor | 1× |
| CH2 pattern | D6 / GPIO12 | CH2 tip after 330 Ω series resistor | 1× |
| External trigger | D7 / GPIO13 | EXT TRIG tip after 330 Ω series resistor | 10× |
| Common return | GND | Probe ground clips at the common fixture ground point | — |

Fit one 10 kΩ pull-down from each GPIO to fixture GND on the GPIO side of its series resistor. The pull-downs bias the signals low during reset. Use short leads and USB power only. Confirm a shared ground reference before attaching the earth-referenced probes, with any previous DUT or source disconnected. Keep clips on fixture GND. Nominal GPIO swing is 0–3.3 V. With a compatible 10× probe the expected EXT BNC swing is about 0.33 V. Verify attenuation and triggering on the bench. Hantek specifies 200 mV EXT sensitivity and 1 V EXT/5 sensitivity in the DC-to-100-MHz band. The manual and product sheet differ on threshold adjustment range. Neither supplies a distinct maximum voltage or impedance for EXT in the reviewed sections. This procedure uses only the attenuated logic signal on EXT. CH1 and CH2 are specified at 1 MΩ in parallel with 20 pF nominal. See the [Hantek manual](https://www.hantek.com/Product/5000P/DSO5000P_Manual.pdf) and [DSO5102P specifications](https://www.hantek.com/products/detail/96).

With the owner present after approval, record the original scope settings and establish exclusive ownership. Use the front panel to select EDGE, EXT, rising, DC, Normal and a threshold near +0.15 V at the EXT BNC. Set both channels to DC and match the 1× probe menu settings. Start at about 1 V/div and 2 ms/div, with at least 18 ms of record after the trigger. Confirm readiness on the instrument after pressing SINGLE. Leave the fixture idle for one bounded no-trigger trial, then perform two trials with distinct IDs. Preserve the stopped dual waveform before any rearm. Use the existing qualified stopped retrieval or frozen export. Compare both event IDs and channel signatures. Allow one repeat for an operator-correctable setup issue and abort if readiness, ownership, or preservation cannot be established.

Use `STOP` to idle the fixture. For a physical stop, disconnect fixture USB power and remove the tips before the common ground. Restore scope settings and original run state and verify before resuming telemetry. Instrument-side automated external trigger control and independent timing comparison require additional qualification.
