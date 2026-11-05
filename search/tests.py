import json
import os
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient


class SearchEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "OPENAI_API_KEY": "",
            "KMS_API_BASE_URL": "",
            "KMS_API_TOKEN": "",
        },
        clear=False,
    )
    def test_returns_matching_local_demo_records_without_kms_credentials(self):
        response = self.client.post("/api/search/", {"query": "cases about contract breach"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["query_analysis_provider"], "heuristic_fallback")
        self.assertTrue(response.data["retrieval"]["is_demo"])
        self.assertTrue(response.data["retrieval"]["results"])
        self.assertTrue(all(item["id"].startswith("demo-") for item in response.data["results"]))

    @patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "fake-gemini-key", "KMS_API_BASE_URL": "", "KMS_API_TOKEN": ""},
        clear=False,
    )
    @patch("requests.post")
    def test_gemini_analysis_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "intent": "case_search",
                                        "is_legal_query": True,
                                        "domain": "criminal",
                                        "court": "Supreme Court",
                                        "act": "Negotiable Instruments Act",
                                        "section": "138",
                                        "legal_issues": ["cheque bounce"],
                                        "keywords": ["cheque bounce", "Section 138"],
                                        "language": "hinglish",
                                        "clarifying_question": None,
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/search/",
            {"query": "cheque bounce hone par bail kaise milti hai under NI act"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["query_analysis_provider"], "gemini")
        self.assertEqual(response.data["query_analysis"]["language"], "hinglish")
        self.assertEqual(response.data["retrieval"]["params"]["lang"], "hinglish")

    def test_rejects_blank_query(self):
        response = self.client.post("/api/search/", {"query": ""}, format="json")

        self.assertEqual(response.status_code, 400)

    @patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "OPENAI_API_KEY": "", "KMS_API_BASE_URL": "", "KMS_API_TOKEN": ""},
        clear=False,
    )
    def test_passes_documented_filters_to_kms_search(self):
        response = self.client.post(
            "/api/search/",
            {
                "query": "dishonour of cheque",
                "language": "hi",
                "limit": 25,
                "is_official": True,
                "version_type": "OFFICIAL_TRANSLATION",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["retrieval"]["params"],
            {"q": "dishonour of cheque", "lang": "hi", "limit": "25", "is_official": "true", "version_type": "OFFICIAL_TRANSLATION"},
        )

    @patch.dict(os.environ, {"KMS_API_BASE_URL": "", "KMS_API_TOKEN": ""}, clear=False)
    def test_domains_is_an_explicitly_labelled_unconfigured_kms_response(self):
        response = self.client.get("/api/domains/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertTrue(response.data["is_mock"])
        self.assertEqual(response.data["results"], [])

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False)
    @patch("search.services.model_catalog.requests.get")
    def test_lists_gemini_catalogue_with_compatibility_flags(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "models": [
                {"name": "models/gemini-flash-lite-latest", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            ]
        }
        mock_get.return_value = mock_response

        response = self.client.get("/api/models/?provider=gemini")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["models"][0]["is_legal_search_compatible"])
        self.assertFalse(response.data["models"][1]["is_legal_search_compatible"])

    @patch.dict(
        os.environ,
        {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key"},
        clear=False,
    )
    @patch("search.services.query_analyzer.requests.post")
    def test_gemini_503_returns_a_labelled_fallback_instead_of_gateway_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 503
        mock_response.text = "This model is currently experiencing high demand."
        mock_response.json.return_value = {"error": {"message": mock_response.text}}
        mock_post.return_value = mock_response

        response = self.client.post("/api/search/", {"query": "cheque bounce bail"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["query_analysis_provider"], "heuristic_fallback")
        self.assertTrue(response.data["success"])

    @patch.dict(
        os.environ,
        {"KMS_API_BASE_URL": "https://kms.example.test", "KMS_API_TOKEN": "test-token"},
        clear=False,
    )
    @patch("search.services.kms_client.requests.get")
    def test_returns_normalized_live_kms_results(self, mock_get):
        from search.services.kms_client import KMSClient

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "count": 1,
            "results": [{"id": "kms-1", "title": "Source-provided title"}],
        }
        mock_get.return_value = mock_response

        response = KMSClient().search({"q": "contract breach"})

        self.assertFalse(response["is_mock"])
        self.assertEqual(response["results"], [{"id": "kms-1", "title": "Source-provided title"}])
        self.assertEqual(response["source_metadata"], {"count": 1})
        mock_get.assert_called_once_with(
            "https://kms.example.test/api/v1/search/",
            headers={"Authorization": "Token test-token"},
            params={"q": "contract breach"},
            timeout=10.0,
        )
