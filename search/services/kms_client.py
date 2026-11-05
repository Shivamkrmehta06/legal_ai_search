"""Small client for the documented KMS legal search endpoint."""

import os
from typing import Any

import requests


class LocalDemoSearch:
    """Small, clearly identified examples for trying the search UI offline."""

    RECORDS = [
        {
            "id": "demo-ni-138-01",
            "case_title": "Demo record: cheque dishonour and statutory notice",
            "title": "Demo record: cheque dishonour and statutory notice",
            "snippet": "Illustrative record for testing query matching and paragraph citations. It is not a judgment or legal authority.",
            "paragraph_number": "1",
            "page_number": 1,
            "language_code": "en",
            "language_name": "English",
            "version_type": "ORIGINAL",
            "version_type_display": "Original",
            "is_official": False,
            "source_label": "Local demo dataset",
        },
        {
            "id": "demo-ni-138-02",
            "case_title": "Demo record: legally enforceable debt and presumptions",
            "title": "Demo record: legally enforceable debt and presumptions",
            "snippet": "Illustrative record covering a sample Section 138 issue. Replace this example with an authorized KMS result for legal research.",
            "paragraph_number": "2",
            "page_number": 2,
            "language_code": "en",
            "language_name": "English",
            "version_type": "ORIGINAL",
            "version_type_display": "Original",
            "is_official": False,
            "source_label": "Local demo dataset",
        },
        {
            "id": "demo-contract-01",
            "case_title": "Demo record: breach of contract and damages",
            "title": "Demo record: breach of contract and damages",
            "snippet": "Illustrative contract-law record used to demonstrate general legal search across domains.",
            "paragraph_number": "1",
            "page_number": 1,
            "language_code": "en",
            "language_name": "English",
            "version_type": "ORIGINAL",
            "version_type_display": "Original",
            "is_official": False,
            "source_label": "Local demo dataset",
        },
    ]

    @classmethod
    def search(cls, params: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(params.get("q", "")).casefold()
        tokens = [token for token in query.split() if len(token) > 2]
        results = []
        for record in cls.RECORDS:
            text = " ".join(str(value) for value in record.values()).casefold()
            if tokens and not any(token in text for token in tokens):
                continue
            if params.get("lang") and record["language_code"] != params["lang"]:
                continue
            if params.get("version_type") and record["version_type"] != params["version_type"]:
                continue
            if params.get("is_official") == "true" and not record["is_official"]:
                continue
            if params.get("is_official") == "false" and record["is_official"]:
                continue
            results.append(record)
        limit = int(params.get("limit", 20))
        return results[:limit]


class KMSClientError(Exception):
    """Raised when a configured KMS request cannot be fulfilled."""


class KMSClient:
    endpoint_path = "/api/v1/search/"

    def list_domains(self) -> dict[str, Any]:
        """Return configured legal domains from the KMS API when available."""
        return self._get_collection("/api/v1/legal-domains/")

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        base_url = os.getenv("KMS_API_BASE_URL")
        token = os.getenv("KMS_API_TOKEN")

        if not base_url or not token:
            return {
                "is_mock": False,
                "is_demo": True,
                "message": "Showing local demo records. Connect KMS to search your organization's legal knowledge base.",
                "endpoint": self.endpoint_path,
                "params": params,
                "results": LocalDemoSearch().search(params),
            }

        payload = self._request(self.endpoint_path, params)

        return {
            "is_mock": False,
            "message": None,
            "endpoint": self.endpoint_path,
            "params": params,
            "results": self._extract_results(payload),
            "source_metadata": self._extract_metadata(payload),
        }

    def _get_collection(self, endpoint_path: str) -> dict[str, Any]:
        base_url = os.getenv("KMS_API_BASE_URL")
        token = os.getenv("KMS_API_TOKEN")
        if not base_url or not token:
            return {
                "is_mock": True,
                "message": "KMS is not configured.",
                "results": [],
            }
        payload = self._request(endpoint_path, {})
        return {
            "is_mock": False,
            "message": None,
            "results": self._extract_results(payload),
            "source_metadata": self._extract_metadata(payload),
        }

    def _request(self, endpoint_path: str, params: dict[str, Any]) -> Any:
        base_url = os.getenv("KMS_API_BASE_URL")
        token = os.getenv("KMS_API_TOKEN")
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}{endpoint_path}",
                headers={"Authorization": f"Token {token}"},
                params=params,
                timeout=float(os.getenv("KMS_API_TIMEOUT_SECONDS", "10")),
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise KMSClientError("The KMS Legal Knowledge API request failed.") from exc

    @staticmethod
    def _extract_results(payload: Any) -> list[Any]:
        """Normalize documented and conventional collection envelopes safely.

        An unfamiliar response is returned as a single source record so source
        fields are preserved rather than transformed into invented legal data.
        """
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("results", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
            return [payload]
        return [payload]

    @staticmethod
    def _extract_metadata(payload: Any) -> dict[str, Any] | None:
        """Pass through pagination/count metadata when provided by KMS."""
        if not isinstance(payload, dict):
            return None
        metadata = {
            key: payload[key]
            for key in ("count", "next", "previous", "page", "total")
            if key in payload
        }
        return metadata or None
