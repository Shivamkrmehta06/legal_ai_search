import os
from django.shortcuts import render

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from .serializers import SearchRequestSerializer
from .services.ai_wrapper import PROVIDERS
from .services.api_logging import log_api_call
from .services.judgments import judgment_details
from .services.kms_client import KMSClient, KMSClientError
from .services.model_catalog import ModelCatalogError, list_models, list_providers
from .services.search_orchestrator import SearchOrchestrator


# The APIs below are public. Ignoring login cookies stops an admin session in the
# same browser from making them demand a CSRF token, which the search page doesn't send.


def home(request):
    return render(request, "search/index.html")


@api_view(["POST"])
@authentication_classes([])
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

    log_api_call(
        "Site POST /api/search/",
        request.data,
        {
            "answered_by": result["query_analysis_provider"],
            "request_type": result["query_analysis"].get("request_type"),
            "judgment_count": result["judgment_count"],
            "requested_info": result["requested_info"],
            "judgments": [judgment.get("title") for judgment in result["judgments"]],
            "response_message": result["response_message"],
        },
        status=status.HTTP_200_OK,
    )
    return Response({"success": True, **result}, status=status.HTTP_200_OK)


@api_view(["GET"])
@authentication_classes([])
def judgment_detail(request, case_id):
    """Everything KMS holds on one judgment, loaded when a user clicks More."""
    client = KMSClient()
    if not client.is_configured():
        return Response({"success": False, "detail": "The legal database is not connected."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        judgment = judgment_details(client, str(case_id))
    except KMSClientError:
        return Response(
            {"success": False, "detail": "This judgment could not be loaded. Please try again."},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"success": True, "judgment": judgment})


@api_view(["GET"])
@authentication_classes([])
def domains(request):
    """Expose the configured KMS domain registry for the domain selector."""
    try:
        result = KMSClient().list_domains()
    except KMSClientError as exc:
        return Response({"success": False, "detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"success": True, **result})


@api_view(["GET"])
@authentication_classes([])
def providers(request):
    """List the AI providers and whether each has an API key."""
    return Response({"success": True, "providers": list_providers()})


@api_view(["GET"])
@authentication_classes([])
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
