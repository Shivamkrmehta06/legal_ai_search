from io import StringIO
import json
import os
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
import requests
from rest_framework.test import APIClient

from search.services.ai_wrapper import ModelTarget, build_model_chain
from search.services.query_analyzer import _fallback_analysis, analyze_query


@patch.dict(
    os.environ,
    {"AI_MODEL_CHAIN": "", "AI_DISABLED_PROVIDERS": "groq,cerebras,mistral,cohere,deepseek,xai,openai,anthropic"},
    clear=False,
)
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
        self.assertTrue(response.data["query_analysis_provider"].startswith("gemini:"))
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

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-key", "GEMINI_AVAILABLE_MODELS": ""}, clear=False)
    @patch("search.services.model_catalog.requests.get")
    def test_lists_only_tested_gemini_models(self, mock_get):
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
        self.assertEqual([model["id"] for model in response.data["models"]], ["gemini-flash-lite-latest"])

    @patch.dict(
        os.environ,
        {"AI_PROVIDER": "gemini", "AI_MODEL_CHAIN": "", "GEMINI_API_KEY": "test-key"},
        clear=False,
    )
    @patch("search.services.ai_wrapper.requests.post")
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


LEGAL_QUERY_JSON = json.dumps({"intent": "case_search", "is_legal_query": True, "language": "en"})


def gemini_response(status_code=200, text=LEGAL_QUERY_JSON):
    response = MagicMock()
    response.ok = status_code == 200
    response.status_code = status_code
    response.text = "Provider error"
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return response


def openai_client_mock(text=LEGAL_QUERY_JSON):
    client = MagicMock()
    client.return_value.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=text))
    ]
    return client


def http_error(status_code):
    error = RuntimeError(f"status {status_code}")
    error.status_code = status_code
    return error


@patch.dict(
    os.environ,
    {
        "AI_PROVIDER": "auto",
        "AI_MODEL_CHAIN": "gemini:first,groq:vendor/backup:free",
        "AI_TOTAL_TIMEOUT_SECONDS": "",
        "AI_DISABLED_PROVIDERS": "",
        "GROQ_AVAILABLE_MODELS": "",
        "CEREBRAS_API_KEY": "",
        "CEREBRAS_API_KEYS": "",
        "MISTRAL_API_KEY": "",
        "MISTRAL_API_KEYS": "",
        "COHERE_API_KEY": "",
        "COHERE_API_KEYS": "",
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_API_KEYS": "",
        "XAI_API_KEY": "",
        "XAI_API_KEYS": "",
        "GEMINI_API_KEY": "gemini-key",
        "GEMINI_API_KEYS": "",
        "GOOGLE_API_KEY": "",
        "GROQ_API_KEY": "groq-key",
        "GROQ_API_KEYS": "",
        "OPENAI_API_KEY": "",
        "OPENAI_API_KEYS": "",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_API_KEYS": "",
    },
    clear=False,
)
class AIWrapperTests(TestCase):
    @patch("search.services.ai_wrapper.requests.post")
    def test_uses_first_model_when_it_works(self, mock_post):
        mock_post.return_value = gemini_response()

        analysis, used = analyze_query("cheque bounce")

        self.assertEqual(used, "gemini:first")
        self.assertEqual(analysis["intent"], "case_search")
        self.assertEqual(mock_post.call_count, 1)

    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_moves_to_other_provider_when_first_model_fails(self, mock_post, mock_openai):
        mock_post.return_value = gemini_response(status_code=503)

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "groq:vendor/backup:free")
        self.assertEqual(mock_openai.call_args.kwargs["base_url"], "https://api.groq.com/openai/v1")

    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_moves_on_when_a_model_returns_invalid_json(self, mock_post, mock_openai):
        mock_post.return_value = gemini_response(text="not json")

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "groq:vendor/backup:free")

    @patch.dict(os.environ, {"GEMINI_API_KEYS": "backup-1, backup-2"}, clear=False)
    @patch("search.services.ai_wrapper.requests.post")
    def test_exhausted_key_moves_to_next_key_on_same_model(self, mock_post):
        mock_post.side_effect = [gemini_response(status_code=429), gemini_response()]

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "gemini:first")
        keys = [call.kwargs["headers"]["x-goog-api-key"] for call in mock_post.call_args_list]
        self.assertEqual(keys, ["gemini-key", "backup-1"])

    @patch.dict(os.environ, {"GEMINI_API_KEYS": "backup-1"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_server_error_skips_other_keys_and_changes_model(self, mock_post, mock_openai):
        mock_post.return_value = gemini_response(status_code=500)

        _, used = analyze_query("cheque bounce")

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(used, "groq:vendor/backup:free")

    @patch.dict(os.environ, {"GEMINI_API_KEYS": "backup-1"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_changes_provider_when_all_keys_are_exhausted(self, mock_post, mock_openai):
        mock_post.return_value = gemini_response(status_code=429)

        _, used = analyze_query("cheque bounce")

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(used, "groq:vendor/backup:free")

    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_selected_model_is_tried_first_with_chain_as_backup(self, mock_post, mock_openai):
        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError("down")
        mock_post.return_value = gemini_response()

        _, used = analyze_query("cheque bounce", provider_override="groq", model_override="vendor/chosen")

        self.assertEqual(
            mock_openai.return_value.chat.completions.create.call_args_list[0].kwargs["model"],
            "vendor/chosen",
        )
        self.assertEqual(used, "gemini:first")

    @patch.dict(os.environ, {"AI_MODEL_CHAIN": "groq:vendor/backup"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    def test_openai_style_schema_requires_every_field_for_strict_mode(self, mock_openai):
        analyze_query("cheque bounce")

        kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
        schema = kwargs["response_format"]["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertCountEqual(schema["required"], schema["properties"])

    @patch.dict(
        os.environ,
        {"AI_MODEL_CHAIN": "anthropic:claude-opus-5-5", "ANTHROPIC_API_KEY": "key-1", "ANTHROPIC_API_KEYS": "key-2"},
        clear=False,
    )
    @patch("search.services.anthropic_provider.anthropic.Anthropic")
    def test_claude_uses_official_sdk_and_rotates_keys(self, mock_anthropic):
        parsed = MagicMock(intent="case_search")
        parsed.model_dump.return_value = {"intent": "case_search"}
        mock_anthropic.return_value.messages.parse.side_effect = [
            http_error(402),
            MagicMock(parsed_output=parsed),
        ]

        analysis, used = analyze_query("cheque bounce")

        self.assertEqual(used, "anthropic:claude-opus-5-5")
        self.assertEqual(analysis["intent"], "case_search")
        self.assertEqual([call.kwargs["api_key"] for call in mock_anthropic.call_args_list], ["key-1", "key-2"])
        self.assertEqual(mock_anthropic.call_args.kwargs["max_retries"], 0)

    @patch.dict(
        os.environ,
        {"AI_MODEL_CHAIN": "anthropic:claude-opus-5-5", "ANTHROPIC_API_KEY": "key-1"},
        clear=False,
    )
    @patch("search.services.anthropic_provider.anthropic.Anthropic")
    def test_claude_refusal_is_treated_as_a_model_failure(self, mock_anthropic):
        mock_anthropic.return_value.messages.parse.return_value = MagicMock(parsed_output=None, stop_reason="refusal")

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "heuristic_fallback")

    @patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False)
    def test_skips_providers_without_a_key(self):
        self.assertEqual(build_model_chain(), [ModelTarget("gemini", "first")])

    @patch.dict(os.environ, {"AI_MODEL_CHAIN": "deepseek:deepseek-chat", "DEEPSEEK_API_KEY": "key"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    def test_deepseek_uses_json_mode_and_describes_the_schema_in_the_prompt(self, mock_openai):
        _, used = analyze_query("cheque bounce")

        kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(used, "deepseek:deepseek-chat")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertIn("is_legal_query", kwargs["messages"][0]["content"])
        self.assertIn("JSON", kwargs["messages"][0]["content"])

    @patch.dict(os.environ, {"AI_MODEL_CHAIN": "mistral:mistral-small", "MISTRAL_API_KEY": "key"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    def test_new_provider_uses_its_own_address(self, mock_openai):
        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "mistral:mistral-small")
        self.assertEqual(mock_openai.call_args.kwargs["base_url"], "https://api.mistral.ai/v1")

    @patch.dict(os.environ, {"AI_MODEL_CHAIN": "mistral:mistral-large-4", "MISTRAL_API_KEY": "key"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    def test_reads_answer_from_response_with_thinking_blocks(self, mock_openai):
        mock_openai.return_value.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=[
                {"type": "thinking", "thinking": []},
                {"type": "text", "text": LEGAL_QUERY_JSON},
            ]))
        ]

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "mistral:mistral-large-4")

    @patch.dict(os.environ, {"AI_DISABLED_PROVIDERS": "groq, openai"}, clear=False)
    def test_disabled_provider_is_hidden_and_skipped_despite_its_key(self):
        self.assertEqual(build_model_chain(), [ModelTarget("gemini", "first")])

        response = APIClient().get("/api/providers/")

        configured = {item["id"]: item["configured"] for item in response.data["providers"]}
        self.assertFalse(configured["groq"])

    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_labelled_fallback_when_every_model_fails(self, mock_post, mock_openai):
        mock_post.return_value = gemini_response(status_code=503)
        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError("down")

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "heuristic_fallback")

    @patch.dict(os.environ, {"AI_TOTAL_TIMEOUT_SECONDS": "0"}, clear=False)
    @patch("search.services.ai_wrapper.requests.post")
    def test_stops_trying_models_when_out_of_time(self, mock_post):
        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "heuristic_fallback")
        mock_post.assert_not_called()

    @patch.dict(os.environ, {"AI_MODEL_CHAIN": "openrouter:model"}, clear=False)
    def test_malformed_chain_uses_the_fallback(self):
        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "heuristic_fallback")

    @patch.dict(os.environ, {"AI_PROVIDER": "not-a-provider"}, clear=False)
    @patch("search.services.ai_wrapper.requests.post")
    def test_unknown_ai_provider_setting_still_uses_the_models(self, mock_post):
        mock_post.return_value = gemini_response()

        _, used = analyze_query("cheque bounce")

        self.assertEqual(used, "gemini:first")

    def test_keyword_fallback_matches_whole_words(self):
        self.assertEqual(_fallback_analysis("breach of contract").intent, "general_search")
        self.assertEqual(_fallback_analysis("Section 138 of the NI Act").intent, "statute_search")
        self.assertEqual(_fallback_analysis("cases on bail").intent, "case_search")

    @patch.dict(os.environ, {"KMS_API_BASE_URL": "", "KMS_API_TOKEN": ""}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI", new_callable=openai_client_mock)
    @patch("search.services.ai_wrapper.requests.post")
    def test_search_still_answers_when_every_model_fails(self, mock_post, mock_openai):
        mock_post.side_effect = requests.ConnectionError("network down")
        mock_openai.return_value.chat.completions.create.side_effect = RuntimeError("down")

        response = APIClient().post("/api/search/", {"query": "breach of contract"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["query_analysis_provider"], "heuristic_fallback")
        self.assertTrue(response.data["results"])

    @patch.dict(os.environ, {"KMS_API_BASE_URL": "https://kms.example.test", "KMS_API_TOKEN": "token"}, clear=False)
    @patch("search.services.kms_client.requests.get", side_effect=requests.ConnectionError("down"))
    @patch("search.services.ai_wrapper.requests.post")
    def test_kms_outage_keeps_analysis_and_never_shows_demo_records(self, mock_post, mock_get):
        mock_post.return_value = gemini_response()

        response = APIClient().post("/api/search/", {"query": "cheque bounce"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["retrieval"]["unavailable"])
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["query_analysis"]["intent"], "case_search")

    def test_providers_endpoint_reports_which_are_configured(self):
        response = APIClient().get("/api/providers/")

        configured = {item["id"]: item["configured"] for item in response.data["providers"]}
        self.assertEqual({name for name, is_set in configured.items() if is_set}, {"gemini", "groq"})
        self.assertEqual(len(configured), 9)

    @patch("search.services.ai_wrapper.OpenAI")
    def test_untested_provider_lists_every_suitable_model(self, mock_openai):
        mock_openai.return_value.models.list.return_value = [
            MagicMock(id="whisper-large"),
            MagicMock(id="llama-b"),
            MagicMock(id="llama-a"),
        ]

        response = APIClient().get("/api/models/?provider=groq")

        self.assertEqual([model["id"] for model in response.data["models"]], ["llama-a", "llama-b"])

    @patch.dict(os.environ, {"GROQ_AVAILABLE_MODELS": "llama-b, not-in-catalogue"}, clear=False)
    @patch("search.services.ai_wrapper.OpenAI")
    def test_available_models_setting_limits_the_list(self, mock_openai):
        mock_openai.return_value.models.list.return_value = [MagicMock(id="llama-a"), MagicMock(id="llama-b")]

        response = APIClient().get("/api/models/?provider=groq")

        self.assertEqual([model["id"] for model in response.data["models"]], ["llama-b"])

    @patch("search.management.commands.check_models.RETRY_DELAY_SECONDS", 0)
    @patch("search.management.commands.check_models.list_providers")
    @patch("search.management.commands.check_models.list_models")
    @patch("search.services.ai_wrapper.requests.post")
    def test_check_models_prints_the_working_models(self, mock_post, mock_list_models, mock_list_providers):
        mock_list_providers.return_value = [{"id": "gemini", "label": "Google Gemini", "configured": True}]
        mock_list_models.return_value = [{"id": "good"}, {"id": "bad"}]
        mock_post.side_effect = lambda url, **kwargs: gemini_response(status_code=200 if "/good:" in url else 404)
        output = StringIO()

        call_command("check_models", stdout=output)

        self.assertIn("1 of 2 models work", output.getvalue())
        self.assertIn("GEMINI_AVAILABLE_MODELS=good\n", output.getvalue())

    def test_rejects_removed_provider(self):
        response = APIClient().post("/api/search/", {"query": "x", "provider": "openrouter"}, format="json")

        self.assertEqual(response.status_code, 400)
