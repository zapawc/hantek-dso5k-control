import unittest

from hantek_dso5k._framing import ProtocolError, frame
from hantek_dso5k._waveform import ch1_waveform
from hantek_dso5k.native_waveform import MAX_CAPTURE_BYTES, decode_native_waveform


S92 = bytes.fromhex("53 04 00 92 01 00 EA")


def start(count):
    return frame(0x82, b"\x00" + count.to_bytes(3, "little"))


def chunk(selector, samples):
    return frame(0x82, bytes([1, selector]) + samples)


def unavailable(channel):
    return frame(0x82, bytes([3, channel]))


def terminal(selector):
    return frame(0x82, bytes([2, selector]))


PAIR = (start(3), chunk(4, b"\x81\x00"), chunk(4, b"\x7f"),
        start(2), chunk(4, b"\x11\x88"), unavailable(2), terminal(4))


class NativeWaveformTests(unittest.TestCase):
    def assertRejected(self, *transfers, selector=4):
        with self.assertRaises(ProtocolError):
            decode_native_waveform(transfers, selector=selector)

    def test_valid_pair_and_separate_math_availability(self):
        decoded = decode_native_waveform(PAIR, selector=4)
        self.assertEqual(decoded.selector, 4)
        self.assertEqual([ch.channel for ch in decoded.channels], ["CH1", "CH2", "MATH"])
        self.assertEqual([ch.samples for ch in decoded.channels], [(-127, 0, 127), (17, -120), None])
        self.assertTrue(decoded.terminal_seen)
        self.assertEqual(decoded.status_offsets, ())

    def test_analog_unavailable_and_math_raw_data(self):
        decoded = decode_native_waveform(
            [unavailable(0), start(1), chunk(4, b"\x80"), start(1), chunk(4, b"\x16"), terminal(4)],
            selector=4,
        )
        self.assertEqual([ch.samples for ch in decoded.channels], [None, (-128,), (22,)])
        missing = decode_native_waveform([unavailable(0), unavailable(1), unavailable(2), terminal(4)], selector=4)
        self.assertTrue(missing.terminal_seen)
        self.assertTrue(all(ch.samples is None for ch in missing.channels))

    def test_single_channel_profiles_preserve_ch1_grammar(self):
        for selector, name in [(0, "CH1"), (1, "CH2")]:
            with self.subTest(selector=selector):
                parts = [start(2), chunk(selector, b"\x01\xff"), terminal(selector)]
                complete = decode_native_waveform(parts, selector=selector)
                self.assertEqual((complete.channels[0].channel, complete.channels[0].samples), (name, (1, -1)))
                self.assertTrue(complete.terminal_seen)
                missing = decode_native_waveform([unavailable(selector)], selector=selector)
                self.assertIsNone(missing.channels[0].samples)
                self.assertFalse(missing.terminal_seen)
                self.assertRejected(unavailable(selector), terminal(selector), selector=selector)
        self.assertEqual(ch1_waveform([start(2), chunk(0, b"\x01\xff"), terminal(0)]), ("complete", (1, -1)))
        self.assertEqual(ch1_waveform([unavailable(0)]), ("unavailable", ()))

    def test_status_boundary_and_fragmented_or_coalesced_delivery(self):
        parts = (S92, PAIR[0], S92, *PAIR[1:], S92, S92)
        wire = b"".join(parts)
        expected = decode_native_waveform([wire], selector=4)
        self.assertEqual(expected.status_offsets, (0, len(S92 + PAIR[0]), len(wire) - 14, len(wire) - 7))
        for i in range(len(wire) + 1):
            with self.subTest(split=i):
                self.assertEqual(decode_native_waveform([wire[:i], wire[i:]], selector=4), expected)
        self.assertEqual(decode_native_waveform((bytes([b]) for b in wire), selector=4), expected)
        self.assertRejected(S92)
        self.assertRejected(*PAIR[:-1], S92)

    def test_status_shaped_sample_bytes_remain_data(self):
        decoded = decode_native_waveform([start(len(S92)), chunk(0, S92), terminal(0)], selector=0)
        self.assertEqual(decoded.status_offsets, ())
        self.assertEqual(len(decoded.channels[0].samples), len(S92))

    def test_wrong_s92_payload_length_checksum_and_unknown_opcode(self):
        for unexpected in [frame(0x92, b"\x01\x01"), frame(0x92, b"\x01"),
                           S92[:-1] + b"\x00", frame(0xA2, b"?")]:
            with self.subTest(unexpected=unexpected):
                self.assertRejected(*PAIR, unexpected)
                self.assertRejected(PAIR[0], unexpected, *PAIR[1:])

    def test_selector_and_channel_marker_must_match(self):
        self.assertRejected(PAIR[0], chunk(0, b"abc"), *PAIR[3:])
        self.assertRejected(*PAIR[:-1], terminal(1))
        self.assertRejected(unavailable(1), unavailable(0), unavailable(2), terminal(4))
        self.assertRejected(unavailable(0), unavailable(2), unavailable(1), terminal(4))
        with self.assertRaises(ValueError):
            decode_native_waveform(PAIR, selector=True)

    def test_duplicate_and_unexpected_blocks(self):
        self.assertRejected(unavailable(0), unavailable(0), *PAIR[3:])
        self.assertRejected(PAIR[0], PAIR[0], *PAIR[1:])
        self.assertRejected(PAIR[0], unavailable(0), *PAIR[1:])
        self.assertRejected(*PAIR[:-1], unavailable(2), PAIR[-1])
        self.assertRejected(PAIR[1], *PAIR)

    def test_exact_counts_and_coverage(self):
        self.assertRejected(start(0), *PAIR[1:])
        self.assertRejected(start(40001), *PAIR[1:])
        self.assertRejected(start(2), chunk(4, b"abc"), *PAIR[3:])
        self.assertRejected(start(4), chunk(4, b"abc"), *PAIR[3:])
        self.assertRejected(start(1), chunk(4, b""), *PAIR[3:])
        self.assertRejected(start(10001), chunk(4, bytes(10001)), *PAIR[3:])
        self.assertRejected(*PAIR[:-2], terminal(4))

    def test_terminal_and_trailing_data(self):
        self.assertRejected(*PAIR[:-1])
        self.assertRejected(*PAIR, terminal(4))
        self.assertRejected(*PAIR, b"!")
        self.assertRejected(*PAIR, b"S\x04")
        self.assertRejected(b"!" + b"".join(PAIR))

    def test_truncated_sample_frame_and_bad_framing(self):
        self.assertRejected(PAIR[0], PAIR[1][:-2])
        self.assertRejected(b"\x53\x01\x00\x82")
        self.assertRejected(b"\x53\xff\xff\x82")
        self.assertRejected(PAIR[0][:-1] + bytes([PAIR[0][-1] ^ 1]), *PAIR[1:])
        self.assertRejected(frame(0x82, b"\x00\x03\x00"), *PAIR[1:])
        self.assertRejected(frame(0x82), *PAIR)

    def test_capture_bound(self):
        self.assertRejected(bytes(MAX_CAPTURE_BYTES), b"x")
