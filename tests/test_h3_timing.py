"""Unit tests for continuation timing, frame accounting, and sequence assembly."""

import pathlib
import tempfile
import unittest

from h3_lab.continuation import (
    calculate_extension_timing,
    calculate_sequence_frames,
    is_valid_context_length,
    is_valid_target_frames,
    snap_context_length,
    snap_target_frames,
)


class TestH3Timing(unittest.TestCase):
    def test_target_frame_grid_17k_plus_5(self):
        # Valid native frame counts: 5 + 17*k -> 124, 175, 226, 294, 362
        valid_counts = [124, 175, 226, 294, 362]
        for count in valid_counts:
            self.assertTrue(is_valid_target_frames(count), f"{count} should be valid native frame count")

        # Invalid counts
        self.assertFalse(is_valid_target_frames(100))
        self.assertFalse(is_valid_target_frames(120))
        self.assertFalse(is_valid_target_frames(180))

        # Snapping up
        self.assertEqual(snap_target_frames(120), 124)
        self.assertEqual(snap_target_frames(170), 175)

    def test_context_length_grid_51k_plus_39(self):
        # Valid context lengths: 39 + 51*k -> 39, 90, 141, 192...
        valid_contexts = [39, 90, 141, 192]
        for c in valid_contexts:
            self.assertTrue(is_valid_context_length(c), f"{c} should be valid context length")

        self.assertFalse(is_valid_context_length(40))
        self.assertFalse(is_valid_context_length(50))

        # Snapping with boundaries
        self.assertEqual(snap_context_length(39, max_source_frames=175, target_frames=175), 39)
        self.assertEqual(snap_context_length(100, max_source_frames=175, target_frames=175), 90)

    def test_exact_continuation_timing_fixture(self):
        """
        Fixture assertion:
        Target: 175 frames
        Context: 39 frames
        Net added frames: 136 frames (5.6667 s at 24 fps)
        Audio ticks: 39 frames * 40 / 24 = 65 ticks.
        """
        timing = calculate_extension_timing(target_frames=175, context_length=39, source_frames=175)
        self.assertEqual(timing["target_frames"], 175)
        self.assertEqual(timing["context_frames"], 39)
        self.assertEqual(timing["net_new_frames"], 136)
        self.assertEqual(timing["audio_context_ticks"], 65)
        self.assertAlmostEqual(timing["net_new_seconds"], 136 / 24, places=3)

    def test_447_frame_sequence_fixture(self):
        """
        Mandatory fixture:
        Starting with 175 frames and adding two such continuations yields 447 unique frames, 18.625s.
        175 + 136 + 136 = 447 frames.
        """
        total_frames = calculate_sequence_frames(first_take_frames=175, extension_takes_count=2,
                                                context_length=39, target_frames=175)
        self.assertEqual(total_frames, 447, "Sequence with start + 2 extensions must equal exactly 447 frames")
        duration = total_frames / 24.0
        self.assertEqual(duration, 18.625, "447 frames at 24 fps must equal exactly 18.625 seconds")


if __name__ == "__main__":
    unittest.main()
