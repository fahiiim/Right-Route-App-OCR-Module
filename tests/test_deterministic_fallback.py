import unittest

from main import classify_route_quality, extract_route_information_deterministic, normalize_route_information


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
            road_pair = f"{segments[idx].split(',', 1)[0].strip()} and {segments[idx + 1].split(',', 1)[0].strip()}"

            next_parts = [part.strip() for part in segments[idx + 1].split(",") if part.strip()]
            if len(next_parts) >= 3:
                expected = f"{road_pair}, {next_parts[1]}, {next_parts[-1]}"
            elif len(next_parts) >= 2:
                expected = f"{road_pair}, {next_parts[-1]}"
            else:
                expected = road_pair

            self.assertEqual(entry, expected)
        self.assertEqual(classify_route_quality(route_info), "complete-route")

    def test_intersection_uses_route_codes_with_city_state(self):
        route_info = {
            "start_location": "Greenville, North Carolina",
            "end_location": "Greenville, North Carolina",
            "route_segments": [
                "West 10th Street (SR-1598), Greenville, North Carolina",
                "Charles Boulevard (SR-1707), Greenville, North Carolina",
            ],
            "intersection": [
                "West 10th Street (SR-1598) and Charles Boulevard (SR-1707), Greenville, North Carolina"
            ],
            "permit_type": "Oversize / Overweight Single Trip",
        }

        normalized = normalize_route_information(route_info)
        self.assertEqual(
            normalized.get("intersection"),
            ["SR-1598 and SR-1707, Greenville, North Carolina"],
        )


if __name__ == "__main__":
    unittest.main()
