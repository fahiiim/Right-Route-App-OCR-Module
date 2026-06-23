import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def setUp(self):
        api.extracted_data.clear()

    @patch("api.process_document")
    def test_extract_response_contract(self, mock_process_document):
        mock_process_document.return_value = {
            "filename": "sample.pdf",
            "route_information": {
                "start_location": "Sisseton, South Dakota",
                "end_location": "Sioux Falls, South Dakota",
                "route_segments": ["I-29, Sisseton, South Dakota", "I-90, Sioux Falls, South Dakota"],
                "intersection": ["I-29 and I-90, Sioux Falls, South Dakota"],
                "permit_type": "Oversize / Overweight Single Trip",
            },
            "extracted_text": "sample text",
        }

        response = self.client.post(
            "/api/ocr/extract",
            files={"file": ("sample.pdf", b"fake-binary", "application/pdf")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(set(payload.keys()), {"success", "filename", "route_information"})
        self.assertIsInstance(payload["success"], bool)
        self.assertIsInstance(payload["filename"], str)
        self.assertIsInstance(payload["route_information"], dict)

        route_information = payload["route_information"]
        expected_keys = {
            "start_location",
            "end_location",
            "route_segments",
            "intersection",
            "permit_type",
        }
        self.assertEqual(set(route_information.keys()), expected_keys)
        self.assertIsInstance(route_information["route_segments"], list)
        self.assertIsInstance(route_information["intersection"], list)

    @patch("api.process_document_text")
    def test_extract_accepts_text_input(self, mock_process_document_text):
        mock_process_document_text.return_value = {
            "filename": "input-string",
            "route_information": {
                "start_location": "Sisseton, South Dakota",
                "end_location": "Sioux Falls, South Dakota",
                "route_segments": ["I-29, Sisseton, South Dakota", "I-90, Sioux Falls, South Dakota"],
                "intersection": ["I-29 and I-90, Sioux Falls, South Dakota"],
                "permit_type": "Oversize / Overweight Single Trip",
            },
            "extracted_text": "sample text",
        }

        response = self.client.post(
            "/api/ocr/extract-text",
            data={"text": "sample text"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"success", "filename", "route_information"})

    def test_supported_formats_contract(self):
        response = self.client.get("/api/supported-formats")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("supported_formats", payload)
        self.assertIsInstance(payload["supported_formats"], list)


if __name__ == "__main__":
    unittest.main()
