"""Coordinate query understanding, endpoint selection, and KMS retrieval."""

from .kms_client import KMSClient
from .query_analyzer import analyze_query


class SearchOrchestrator:
    """The endpoint router remains narrow until more KMS endpoints exist."""

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
        retrieval = self.kms_client.search(params)
        return {
            "original_query": query,
            "query_analysis": analysis,
            "query_analysis_provider": analysis_provider,
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
