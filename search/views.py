import os
from django.shortcuts import render

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import SearchRequestSerializer
from .services.kms_client import KMSClient, KMSClientError
from .services.model_catalog import ModelCatalogError, list_gemini_models, list_openrouter_models
from .services.query_analyzer import QueryAnalysisError
from .services.search_orchestrator import SearchOrchestrator


def home(request):
    return render(request, "search/index.html")


@api_view(["POST"])
def search_query(request):
    serializer = SearchRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        result = SearchOrchestrator().search(
            serializer.validated_data["query"],
            provider=serializer.validated_data.get("provider"),
            model=serializer.validated_data.get("model"),
            filters={
                key: value
                for key, value in serializer.validated_data.items()
                if key in {"language", "limit", "is_official", "version_type", "case", "document", "legal_document"}
            },
        )
    except QueryAnalysisError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    except KMSClientError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({"success": True, **result}, status=status.HTTP_200_OK)


@api_view(["GET"])
def domains(request):
    """Expose the configured KMS domain registry for the domain selector."""
    try:
        result = KMSClient().list_domains()
    except KMSClientError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"success": True, **result})


@api_view(["GET"])
def free_models(request):
    """List the selected provider's models and their legal-search compatibility."""
    provider = request.query_params.get("provider", "gemini")
    if provider not in {"gemini", "openrouter"}:
        return Response(
            {"success": False, "detail": "provider must be gemini or openrouter."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        if provider == "gemini":
            models = list_gemini_models()
        else:
            models = list_openrouter_models()
    except ModelCatalogError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response({"success": True, "provider": provider, "models": models})
