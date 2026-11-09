"""Coordinate query understanding, endpoint selection, and KMS retrieval."""

from .kms_client import KMSClient
from .kms_router import answer_query
from .query_analyzer import analyze_query


OUT_OF_SCOPE_MESSAGE = (
    "We help you find judgments related to any Act or section, so we can't answer this question. "
    "Try asking, for example: judgments on Section 138 of the NI Act."
)


class SearchOrchestrator:
    def __init__(self, kms_client: KMSClient | None = None):
        self.kms_client = kms_client or KMSClient()

    def search(
        self,
        query: str,
        provider: str | None = None,
        model: str | None = None,
        filters: dict | None = None,
    ) -> dict:
        analysis, analysis_provider = analyze_query(
            query,
            provider_override=provider,
            model_override=model,
        )
        params = self._build_search_params(query, analysis, filters or {})
        answer = {"judgments": [], "judgment_count": 0, "requested_info": [], "response_message": None, "results_title": None}
        if analysis.get("request_type") == "out_of_scope" or not analysis.get("is_legal_query", True):
            # Searching for an unrelated question could only return misleading records.
            answer["response_message"] = OUT_OF_SCOPE_MESSAGE
            retrieval = {"is_mock": False, "skipped": True, "message": None, "api_calls": [], "results": []}
        elif self.kms_client.is_configured():
            # A failed KMS keeps the query analysis: demo records never stand in for real sources.
            answered = answer_query(self.kms_client, query, analysis)
            retrieval = answered.pop("retrieval")
            analysis = {**analysis, **answered.pop("understood_law")}
            answer = answered
        else:
            retrieval = self.kms_client.search(params)
        return {
            "original_query": query,
            "query_analysis": analysis,
            "query_analysis_provider": analysis_provider,
            **answer,
            "retrieval": retrieval,
            "results": retrieval["results"],
        }

    @staticmethod
    def _build_search_params(query: str, analysis: dict, filters: dict) -> dict:
        """Map only fields documented by the supplied KMS OpenAPI contract."""
        params = {"q": query}
        language = filters.get("language") or analysis.get("language")
        if language:
            params["lang"] = language
        for field in ("limit", "is_official", "version_type", "case", "document", "legal_document"):
            value = filters.get(field)
            if value is not None:
                params[field] = str(value).lower() if isinstance(value, bool) else str(value)
        return params
