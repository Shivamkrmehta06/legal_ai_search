import os
from django.shortcuts import render

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import SearchRequestSerializer
from .services.ai_wrapper import PROVIDERS
from .services.kms_client import KMSClient, KMSClientError
from .services.model_catalog import ModelCatalogError, list_models, list_providers
from .services.search_orchestrator import SearchOrchestrator


def home(request):
    return render(request, "search/index.html")


@api_view(["POST"])
def search_query(request):
    serializer = SearchRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

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
def providers(request):
    """List the AI providers and whether each has an API key."""
    return Response({"success": True, "providers": list_providers()})


@api_view(["GET"])
def free_models(request):
    """List the selected provider's chat models."""
    provider = request.query_params.get("provider", "gemini")
    if provider not in PROVIDERS:
        return Response(
            {"success": False, "detail": f"provider must be one of: {', '.join(PROVIDERS)}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        models = list_models(provider)
    except ModelCatalogError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response({"success": True, "provider": provider, "models": models})
