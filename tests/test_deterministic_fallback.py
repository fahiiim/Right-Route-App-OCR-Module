import unittest

from main import classify_route_quality, extract_route_information_deterministic


class DeterministicFallbackTests(unittest.TestCase):
    def test_deterministic_parser_produces_ordered_segments(self):
        ocr_text = """
        Permit Type: Oversize / Overweight
        START: I-29 SB at Sisseton, SD
        THEN I-90 EB near Sioux Falls, SD
        THEN SD-11 SB in Sioux Falls, SD
        END: SD-42 EB at Sioux Falls, SD
        """

        route_info = extract_route_information_deterministic(ocr_text)
        segments = route_info.get("route_segments", [])
        intersections = route_info.get("intersection", [])

        self.assertIsInstance(route_info, dict)
        self.assertGreaterEqual(len(segments), 3)
        self.assertEqual(
            len(intersections),
            max(len(segments) - 1, 0),
        )
        for idx, entry in enumerate(intersections):
            self.assertIn(" and ", entry)
            self.assertNotIn(",", entry)
            expected = f"{segments[idx].split(',', 1)[0].strip()} and {segments[idx + 1].split(',', 1)[0].strip()}"
            self.assertEqual(entry, expected)
        self.assertEqual(classify_route_quality(route_info), "complete-route")


if __name__ == "__main__":
    unittest.main()
