from io import StringIO
import json
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
import requests
from rest_framework.test import APIClient

from search.services.ai_wrapper import AIWrapperError, ModelTarget, build_model_chain, complete_structured
from search.services.api_logging import log_api_call
from search.services.kms_client import KMSClient
from search.services.kms_router import INFO_TABLES, answer_query
from search.services.query_analyzer import LegalQuery, _fallback_analysis, analyze_query


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


CASE_A = {"id": "11111111-1111-1111-1111-111111111111", "canonical_title": "A v B", "summary": "On cheque", "court_name": "Supreme Court"}
CASE_B = {"id": "22222222-2222-2222-2222-222222222222", "canonical_title": "C v D", "summary": "Other", "court_name": "High Court"}


def fake_kms(failing=(), by_name=None):
    """Answer like KMS: Section 138 of the NI Act links to two cases."""
    def get(url, headers, params, timeout):
        path = url.replace("https://kms.example.test", "")
        if path in failing:
            raise requests.ConnectionError("down")
        response = MagicMock()
        if path == "/api/v1/statutes/":
            body = {"results": [{"id": "statute-1", "short_name": "NI_ACT"}]}
        elif path == "/api/v1/statutory-provisions/":
            body = {"results": [{"id": "provision-138", "provision_code": "138"}]}
        elif path == "/api/v1/case-statute-links/" and "case" not in params:
            body = {"results": [{"id": "l1", "case": CASE_A["id"], "case_title": "A v B"}, {"id": "l2", "case": CASE_B["id"], "case_title": "C v D"}]}
        elif path == "/api/v1/courts/":
            body = {"results": [{"id": "court-1"}]}
        elif path == "/api/v1/legal-cases/":
            if params.get("court"):
                body = {"results": [CASE_A]}
            else:
                body = {"results": (by_name or {}).get(params.get("search"), [])}
        elif path == f"/api/v1/legal-cases/{CASE_A['id']}/":
            body = CASE_A
        elif path == f"/api/v1/legal-cases/{CASE_B['id']}/":
            body = CASE_B
        elif path == "/api/v1/case-judges/":
            body = {"results": [{"id": "j1", "judge_name": "Judge A"}]}
        elif path == "/api/v1/case-parties/":
            body = {"results": [{"id": "p1", "party_name": "Party A"}]}
        elif path == "/api/v1/legal-documents/":
            body = {"results": [{"id": "doc-1"}]}
        elif path == "/api/v1/case-paragraphs/":
            body = {"results": [{"id": "para-1", "paragraph_text": "Passage"}]}
        else:
            body = {"results": []}
        response.json.return_value = body
        return response
    return get


def analysed(**fields):
    return {"request_type": "judgment_search", "is_legal_query": True, "intent": "case_search", "keywords": [], "legal_issues": [], **fields}


@patch.dict(os.environ, {"KMS_API_BASE_URL": "https://kms.example.test", "KMS_API_TOKEN": "token"}, clear=False)
class APISelectionTests(TestCase):
    def ask(self, query, analysis, failing=(), by_name=None):
        with patch("search.services.kms_client.requests.get", side_effect=fake_kms(failing, by_name)):
            return answer_query(KMSClient(), query, analysis)

    @staticmethod
    def called(result):
        return {call["endpoint"] for call in result["retrieval"]["api_calls"]}

    def test_asking_for_judges_calls_only_the_judges_api_after_finding_the_case(self):
        result = self.ask("who were the judges in A v B", analysed(case_name="A v B", requested_info=["judges"]), by_name={"A v B": [CASE_A]})

        self.assertEqual(self.called(result), {"/api/v1/legal-cases/", "/api/v1/case-judges/"})
        self.assertEqual(result["judgments"][0]["judges"][0]["judge_name"], "Judge A")
        self.assertNotIn("parties", result["judgments"][0])

    def test_asking_for_a_summary_only_reads_the_case(self):
        result = self.ask("summary of A v B", analysed(case_name="A v B", requested_info=["summary"]), by_name={"A v B": [CASE_A]})

        self.assertEqual(self.called(result), {"/api/v1/legal-cases/", f"/api/v1/legal-cases/{CASE_A['id']}/"})
        self.assertEqual(result["judgments"][0]["summary"], "On cheque")
        self.assertNotIn("court_name", result["judgments"][0])

    def test_judgment_list_only_calls_the_law_apis_and_returns_headings(self):
        result = self.ask("NI Act 138 judgments", analysed(act="NI Act", section="138"))

        self.assertEqual(self.called(result), {"/api/v1/statutes/", "/api/v1/statutory-provisions/", "/api/v1/case-statute-links/"})
        self.assertEqual(result["judgments"], [{"id": CASE_A["id"], "title": "A v B"}, {"id": CASE_B["id"], "title": "C v D"}])

    def test_asking_for_several_things_calls_only_those_apis(self):
        result = self.ask("judges and parties in A v B", analysed(case_name="A v B", requested_info=["judges", "parties"]), by_name={"A v B": [CASE_A]})

        self.assertEqual(self.called(result), {"/api/v1/legal-cases/", "/api/v1/case-judges/", "/api/v1/case-parties/"})

    def test_requested_information_for_an_act_covers_each_case(self):
        result = self.ask("judges in NI Act 138 cases", analysed(act="NI Act", section="138", requested_info=["judges"]))

        self.assertTrue(all("judges" in judgment for judgment in result["judgments"]))

    def test_details_are_left_for_more_when_too_many_cases_match(self):
        with patch("search.services.kms_router.MAX_CASES_WITH_DETAILS", 1):
            result = self.ask("judges in NI Act 138 cases", analysed(act="NI Act", section="138", requested_info=["judges"]))

        self.assertNotIn("/api/v1/case-judges/", self.called(result))
        self.assertIn("Click More", result["response_message"])

    def test_information_without_a_case_asks_for_the_case_and_calls_nothing(self):
        result = self.ask("who was the judge", analysed(requested_info=["judges"]))

        self.assertEqual(result["retrieval"]["api_calls"], [])
        self.assertIn("name the case", result["response_message"])

    def test_unknown_requested_information_is_ignored(self):
        result = self.ask("A v B", analysed(case_name="A v B", requested_info=["users", "audit_logs"]), by_name={"A v B": [CASE_A]})

        self.assertEqual(self.called(result), {"/api/v1/legal-cases/"})

    def ask_with_law_records(self, query, analysis, statutes):
        """Ask while KMS holds these statutes, and Section 138 belongs to the NI Act."""
        fake = fake_kms()

        def get(url, headers, params, timeout):
            response = fake(url, headers, params, timeout)
            if url.endswith("/statutes/"):
                response.json.return_value = {"results": statutes}
            elif url.endswith("/statutory-provisions/"):
                response.json.return_value = {
                    "results": [{"id": "provision-138", "provision_code": "138", "statute_official_name": "Negotiable Instruments Act, 1881"}]
                }
            return response

        with patch("search.services.kms_client.requests.get", side_effect=get):
            return answer_query(KMSClient(), query, analysis)

    def test_section_written_inside_the_act_name_is_still_searched(self):
        result = self.ask("اسم القاضي بموجب قانون ني 138", analysed(act="NE Act 138", requested_info=["judges"]))

        self.assertIn("/api/v1/statutory-provisions/", self.called(result))
        self.assertEqual(result["judgment_count"], 2)
        self.assertEqual(result["understood_law"], {"act": "NE Act", "section": "138"})

    def test_year_in_the_act_name_is_not_taken_for_a_section(self):
        result = self.ask("NI Act 1881 judgments", analysed(act="Negotiable Instruments Act, 1881"))

        self.assertNotIn("/api/v1/statutory-provisions/", self.called(result))
        self.assertEqual(result["understood_law"]["act"], "Negotiable Instruments Act, 1881")

    def test_section_written_in_other_scripts_is_understood(self):
        for query in ("قانون ني ١٣٨", "एनआई एक्ट १३८"):
            result = self.ask(query, analysed(act="NI Act", section="138"))
            self.assertEqual(result["judgment_count"], 2, query)

    def test_page_names_the_act_kms_found(self):
        result = self.ask_with_law_records(
            "NE Act 138",
            analysed(act="NE Act", section="138"),
            [{"id": "statute-1", "short_name": "NI_ACT", "official_name": "Negotiable Instruments Act, 1881"}],
        )

        self.assertEqual(result["results_title"], "Judgments on Section 138 of the Negotiable Instruments Act, 1881")

    def test_unmatched_act_is_not_given_another_acts_judgments_silently(self):
        result = self.ask_with_law_records("Income Tax Act 138", analysed(act="Income Tax Act", section="138"), [])

        self.assertEqual(result["results_title"], "Judgments on Section 138 of the Negotiable Instruments Act, 1881")
        self.assertIn('could not match "Income Tax Act"', result["response_message"])
        self.assertNotIn("of the Income Tax Act", result["response_message"])

    def test_case_not_found_says_so(self):
        result = self.ask("judges in X v Y", analysed(case_name="X v Y", requested_info=["judges"]))

        self.assertIn("could not find a case", result["response_message"])
        self.assertEqual(result["judgments"], [])

    def test_several_matching_cases_ask_the_user_to_pick(self):
        result = self.ask("judges in A", analysed(case_name="State", requested_info=[]), by_name={"State": [CASE_A, CASE_B]})

        self.assertEqual(result["judgment_count"], 2)
        self.assertIn("Click More", result["response_message"])

    def test_results_title_follows_the_question(self):
        one_case = {"A v B": [CASE_A]}
        answer = self.ask("judges in A v B", analysed(case_name="A v B", requested_info=["judges"]), by_name=one_case)
        self.assertEqual(answer["results_title"], "Case details")

        several = self.ask("State cases", analysed(case_name="State"), by_name={"State": [CASE_A, CASE_B]})
        self.assertEqual(several["results_title"], "Matching cases")

        topic = self.ask("cheque", analysed(), by_name={"cheque": [CASE_A]})
        self.assertEqual(topic["results_title"], "Related judgments")

        nothing = self.ask("nothing here", analysed())
        self.assertIsNone(nothing["results_title"])

    def test_section_the_user_never_mentioned_is_ignored(self):
        result = self.ask("NI Act judgments", analysed(act="NI Act", section="138"))

        self.assertNotIn("/api/v1/statutory-provisions/", self.called(result))

    def test_court_narrows_the_law_results(self):
        result = self.ask("NI Act judgments", analysed(act="NI Act", court="Supreme Court"))

        self.assertEqual([judgment["id"] for judgment in result["judgments"]], [CASE_A["id"]])

    def test_topic_narrows_the_law_results(self):
        result = self.ask("director liability NI Act", analysed(act="NI Act"), by_name={"director": [CASE_B]})

        self.assertEqual([judgment["id"] for judgment in result["judgments"]], [CASE_B["id"]])

    def test_topic_search_retries_with_the_users_words_only(self):
        result = self.ask("cheque question", analysed(legal_issues=["long phrase"], keywords=["application"]), by_name={"cheque": [CASE_A], "application": [CASE_B]})

        self.assertEqual([judgment["id"] for judgment in result["judgments"]], [CASE_A["id"]])

    def test_failed_table_is_reported_not_shown_as_empty(self):
        result = self.ask(
            "judges", analysed(case_name="A v B", requested_info=["judges"]), failing={"/api/v1/case-judges/"}, by_name={"A v B": [CASE_A]}
        )

        self.assertEqual(result["judgments"][0]["judges"], [])
        self.assertEqual(result["judgments"][0]["unavailable_sections"], ["judges"])

    def test_failed_search_is_reported(self):
        result = self.ask("A v B", analysed(case_name="A v B"), failing={"/api/v1/legal-cases/"})

        self.assertTrue(result["retrieval"]["unavailable"])

    def test_only_reads_from_kms(self):
        with patch("requests.post") as mock_post, patch("requests.put") as mock_put, patch("requests.delete") as mock_delete:
            self.ask("NI Act 138", analysed(act="NI Act", section="138", requested_info=["judges"]))

        mock_post.assert_not_called()
        mock_put.assert_not_called()
        mock_delete.assert_not_called()

    def test_more_loads_every_table_for_one_judgment(self):
        with patch("search.services.kms_client.requests.get", side_effect=fake_kms()):
            response = APIClient().get(f"/api/judgments/{CASE_A['id']}/")

        self.assertEqual(response.status_code, 200)
        judgment = response.data["judgment"]
        for key in (*INFO_TABLES, "passages", "summary"):
            self.assertIn(key, judgment)
        self.assertEqual(judgment["parties"][0]["party_name"], "Party A")

    def test_more_rejects_an_invalid_case_id(self):
        self.assertEqual(APIClient().get("/api/judgments/not-a-case/").status_code, 404)

    def test_more_reports_a_failure(self):
        with patch("search.services.kms_client.requests.get", side_effect=fake_kms(failing={f"/api/v1/legal-cases/{CASE_A['id']}/"})):
            response = APIClient().get(f"/api/judgments/{CASE_A['id']}/")

        self.assertEqual(response.status_code, 502)


@patch.dict(
    os.environ,
    {"AI_MODEL_CHAIN": "gemini:first", "AI_DISABLED_PROVIDERS": "", "GEMINI_API_KEY": "key", "KMS_API_BASE_URL": "https://kms.example.test", "KMS_API_TOKEN": "token"},
    clear=False,
)
class ResponseScopeTests(TestCase):
    def search(self, query, request_type, is_legal=True):
        analysis = {"intent": "general_search", "is_legal_query": is_legal, "request_type": request_type, "language": "en", "act": "NI Act", "section": "138"}
        with patch("search.services.ai_wrapper.requests.post", return_value=gemini_response(text=json.dumps(analysis))), patch(
            "search.services.kms_client.requests.get", side_effect=fake_kms()
        ) as mock_get:
            response = APIClient().post("/api/search/", {"query": query}, format="json")
        return response, mock_get

    def test_unrelated_question_explains_the_service_and_skips_kms(self):
        response, mock_get = self.search("what is the weather today", "out_of_scope", is_legal=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("judgments related to any Act", response.data["response_message"])
        self.assertIsNone(response.data["results_title"])
        self.assertEqual(response.data["judgments"], [])
        mock_get.assert_not_called()

    def test_legal_question_says_no_advice_and_still_gives_judgments(self):
        response, _ = self.search("how do I file under section 138", "legal_question")

        self.assertIn("rather than legal advice", response.data["response_message"])
        self.assertEqual(response.data["results_title"], "Judgments on Section 138 of the NI Act")
        self.assertEqual(response.data["judgment_count"], 2)

    def test_judgment_request_says_what_was_found(self):
        response, _ = self.search("NI Act 138 orders", "judgment_search")

        self.assertEqual(response.data["response_message"], "Found 2 judgments related to Section 138 of the NI Act.")
        self.assertEqual(response.data["results_title"], "Judgments on Section 138 of the NI Act")

    def test_keyword_fallback_treats_order_as_a_judgment_request(self):
        self.assertEqual(_fallback_analysis("orders on bail").intent, "case_search")
        self.assertEqual(_fallback_analysis("orders on bail").request_type, "judgment_search")


class ApiLoggingTests(TestCase):
    def test_shows_request_and_response_without_the_token(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"results": [{"id": "case-1"}]}
        with patch.dict(os.environ, {"KMS_API_BASE_URL": "https://kms.example.test", "KMS_API_TOKEN": "secret-token"}, clear=False), patch(
            "search.services.kms_client.requests.get", return_value=response
        ), self.assertLogs("api_calls", level="INFO") as logs:
            KMSClient().get("/api/v1/legal-cases/", {"search": "cheque"})

        output = "\n".join(logs.output)
        self.assertIn("KMS GET /api/v1/legal-cases/ 200", output)
        self.assertIn("cheque", output)
        self.assertIn("case-1", output)
        self.assertNotIn("secret-token", output)

    def test_failed_ai_call_is_shown_with_its_key_hidden(self):
        error = RuntimeError("rejected key secret-key-123")
        error.status_code = 500
        with patch.dict(os.environ, {"GROQ_API_KEY": "secret-key-123", "GROQ_API_KEYS": "", "AI_DISABLED_PROVIDERS": ""}, clear=False), patch(
            "search.services.ai_wrapper._call", side_effect=error
        ), self.assertLogs("api_calls", level="INFO") as logs, self.assertRaises(AIWrapperError):
            complete_structured([ModelTarget("groq", "model-1")], "system", "my question", LegalQuery, {})

        output = "\n".join(logs.output)
        self.assertIn("AI groq:model-1 (key #1)", output)
        self.assertIn("my question", output)
        self.assertIn("FAILED", output)
        self.assertNotIn("secret-key-123", output)

    def test_long_text_is_cut_unless_asked_for_in_full(self):
        with patch.dict(os.environ, {"API_LOG_MAX_CHARS": "10"}, clear=False), self.assertLogs("api_calls", level="INFO") as logs:
            log_api_call("Test", "x" * 50, "ok")
        self.assertIn("40 more characters", logs.output[0])

        with patch.dict(os.environ, {"API_LOG_MAX_CHARS": "0"}, clear=False), self.assertLogs("api_calls", level="INFO") as logs:
            log_api_call("Test", "x" * 50, "ok")
        self.assertIn("x" * 50, logs.output[0])
