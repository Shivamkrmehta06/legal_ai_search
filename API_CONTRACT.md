# Legal Search Backend Contract

## Endpoint

`POST /api/search/`

### Request

```json
{
  "query": "Supreme Court decisions about cheque bounce"
}
```

`query` is required and accepts up to 2,000 characters.

Optionally, frontend may select a provider and model:

```json
{
  "query": "Supreme Court decisions about cheque bounce",
  "provider": "groq",
  "model": "model-id"
}
```

The KMS OpenAPI contract also supports these optional search filters. They are
passed through only when supplied: `language` (sent as `lang`), `limit`
(1–100), `is_official`, and `version_type` (`ORIGINAL`,
`OFFICIAL_TRANSLATION`, `HUMAN_TRANSLATION`, or `AI_TRANSLATION`). Advanced
integrations can additionally send a KMS UUID for `case`, `document`, or
`legal_document`.

`provider` is one of `gemini`, `groq`, `cerebras`, `mistral`, `cohere`, `deepseek`,
`xai`, `openai`, or `anthropic`.

## Provider and model catalogue

`GET /api/providers/` lists the providers and whether each has an API key.
`AI_DISABLED_PROVIDERS` makes a provider count as having none, so it is hidden and
skipped until it is removed from that setting.

`GET /api/models/?provider=<provider>` reads that provider's live catalogue and
returns the models suited to query analysis, leaving out music, image, embedding,
and research models. When a provider has a tested list, only those models are
returned. Gemini has one built in, and `<NAME>_AVAILABLE_MODELS` overrides it for
any provider. `python manage.py check_models` tests the models with one real request
each and prints that setting. A model that later fails passes the request to the next
model in the chain.

### Success response

```json
{
  "success": true,
  "original_query": "Supreme Court decisions about cheque bounce",
  "query_analysis": {
    "intent": "case_search",
    "court": "Supreme Court",
    "act": null,
    "section": null
  },
  "query_analysis_provider": "gemini",
  "retrieval": {
    "is_mock": false,
    "endpoint": "/api/v1/search/",
    "params": {"q": "Supreme Court decisions about cheque bounce", "lang": "en"},
    "results": []
  },
  "results": []
}
```

`judgments` is the answer: `id` and `title` for each matching case. If the question
asked for specific information, each judgment also holds `requested` and only those
fields, for up to 10 cases. `response_message` and `results_title` are text for the user, and
`retrieval.api_calls` lists exactly which KMS APIs were called.

`GET /api/judgments/<case id>/` returns everything KMS holds on one judgment. The page
calls it when a user clicks More.

### API selection rules

The AI model only reports what was asked. Fixed rules then choose the APIs, in this order:

1. `request_type` is `out_of_scope`: no API is called.
2. `case_name` is present: `/api/v1/legal-cases/` finds the case.
3. An Act or section is present: `/api/v1/statutes/`, `/api/v1/statutory-provisions/`, and
   `/api/v1/case-statute-links/` find its cases. A section is only used if the user typed it,
   in any script, and is taken out of the Act's name if a model put it there. Results are
   named after the Act KMS found. If the named Act isn't in KMS, the section is still searched
   and `response_message` says the Act could not be matched.
4. Information was asked for with no case, Act, court or issue: no API is called, and the
   user is asked to name the case.
5. Otherwise: `/api/v1/legal-cases/` is searched by topic, then by the user's own words.

`requested_info` then adds only its own API:

| Asked for | KMS API |
| --- | --- |
| `summary`, `court`, `judgment_date`, `case_number`, `citation`, `outcome`, `bench` | `/api/v1/legal-cases/{id}/` |
| `judges` | `/api/v1/case-judges/` |
| `parties` | `/api/v1/case-parties/` |
| `statutes` | `/api/v1/case-statute-links/` |
| `issues` | `/api/v1/case-issue-links/` |
| `domains` | `/api/v1/case-domain-links/` |
| `propositions` | `/api/v1/legal-propositions/` |
| `cases_cited`, `cited_by` | `/api/v1/case-citations/` |
| `citation_aliases` | `/api/v1/citation-aliases/` |
| `legal_documents` | `/api/v1/legal-documents/` |
| `case_documents` | `/api/v1/case-documents/` |
| `versions` | `/api/v1/case-versions/` |
| `metadata_versions` | `/api/v1/case-metadata-versions/` |
| `passages` | `/api/v1/legal-documents/`, then `/api/v1/case-paragraphs/` |

`GET /api/domains/` proxies KMS's `/api/v1/legal-domains/` registry so a
configuration-driven domain selector can be built without hard-coding domains.

## States frontend must handle

- `retrieval.is_mock: true`: KMS is not configured. `results` is always empty;
  no legal data has been retrieved.
- `query_analysis_provider: <provider>:<model>`, such as
  `gemini:gemini-flash-lite-latest`: an LLM parsed the query. This names the model that actually answered, which may be a
  backup rather than the one selected in the request.
- `query_analysis_provider: heuristic_fallback`: no LLM is configured or every
  model in the chain was unavailable (for example a Gemini 503).
  Display results normally, but
  do not represent the extracted filters as AI-verified.
- HTTP `400`: invalid request body.
- `unavailable_sections` on a judgment: those details could not be loaded.
- `retrieval.unavailable: true`: the KMS search failed. The query
  analysis is still returned, `results` is empty, and `retrieval.message` explains
  why. Demo records are never used in place of a failed KMS search.

## Backend configuration

Copy `.env.example` to `.env`; never commit the populated file. A live KMS
search requires both `KMS_API_BASE_URL` and `KMS_API_TOKEN`. The client calls:

```text
GET {KMS_API_BASE_URL}/api/v1/search/?q=<query>&lang=<language>
Authorization: Token <KMS_API_TOKEN>
```

The client maps only the documented search fields from the supplied KMS OpenAPI
specification. Domain, statute, court, and date are not `/search/` query
parameters in that specification; the application retains their AI-derived
context but does not falsely present them as server-side search filters.

### Model chain

Query analysis tries models in order and moves to the next when one errors, times
out, or returns invalid JSON. A model selected in the request is tried first, with
the chain as its backup. Set the order with `AI_MODEL_CHAIN`, for example
`gemini:gemini-flash-lite-latest,groq:model-id`. Providers without an API key are
skipped, and `AI_TOTAL_TIMEOUT_SECONDS` limits the time spent across all models.

### Backup API keys

Each provider reads `<NAME>_API_KEY` and an optional comma-separated
`<NAME>_API_KEYS`, for example `GROQ_API_KEYS`. When a key returns 401, 402, 403, or
429, or a 400 about the key or its credit, the same model is retried with the next
key. The server log names the provider and the key's position, never the key. Keys
are not created automatically: a person creates them, and backups only help when
each has its own usage limit.
