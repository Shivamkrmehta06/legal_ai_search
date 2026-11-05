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
  "provider": "openrouter",
  "model": "provider/model-id"
}
```

The KMS OpenAPI contract also supports these optional search filters. They are
passed through only when supplied: `language` (sent as `lang`), `limit`
(1–100), `is_official`, and `version_type` (`ORIGINAL`,
`OFFICIAL_TRANSLATION`, `HUMAN_TRANSLATION`, or `AI_TRANSLATION`). Advanced
integrations can additionally send a KMS UUID for `case`, `document`, or
`legal_document`.

For OpenRouter, select a model marked `is_legal_search_compatible: true`.

## Free-model catalogue

`GET /api/models/?provider=gemini` reads the account's current Gemini model
catalogue. Only models with `is_legal_search_compatible: true` have been
verified for this backend's structured legal-query JSON request; the frontend
should disable the others rather than presenting unsupported audio/image/research
models as legal-query choices.

`GET /api/models/?provider=openrouter` returns OpenRouter's live model catalogue
with JSON structured-output compatibility and pricing metadata. Set
`OPENROUTER_API_KEY` to enable it.

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

`results` is the frontend-friendly top-level list. `retrieval` retains the KMS
endpoint, request parameters, and source metadata for debugging.

`GET /api/domains/` proxies KMS's `/api/v1/legal-domains/` registry so a
configuration-driven domain selector can be built without hard-coding domains.

## States frontend must handle

- `retrieval.is_mock: true`: KMS is not configured. `results` is always empty;
  no legal data has been retrieved.
- `query_analysis_provider: gemini` or `openrouter:<model>`: an LLM
  parsed the query.
- `query_analysis_provider: heuristic_fallback`: no configured LLM was used or
  the configured model was temporarily unavailable (for example a Gemini 503).
  Display results normally, but
  do not represent the extracted filters as AI-verified.
- HTTP `400`: invalid request body.
- HTTP `502`: a configured LLM or KMS service could not be reached.

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

### OpenRouter cloud provider

Set `OPENROUTER_API_KEY` and choose a compatible model returned by
`GET /api/models/?provider=openrouter`. OpenRouter model access and costs depend
on the selected upstream model/provider.
