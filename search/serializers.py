from rest_framework import serializers

from .services.ai_wrapper import PROVIDERS


class SearchRequestSerializer(serializers.Serializer):
    """Validate the public search request."""

    query = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        max_length=2_000,
    )
    provider = serializers.ChoiceField(
        choices=tuple(PROVIDERS),
        required=False,
    )
    model = serializers.CharField(required=False, allow_blank=False, max_length=200)
    language = serializers.CharField(required=False, allow_blank=False, max_length=20)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)
    is_official = serializers.BooleanField(required=False)
    version_type = serializers.ChoiceField(
        choices=("ORIGINAL", "OFFICIAL_TRANSLATION", "HUMAN_TRANSLATION", "AI_TRANSLATION"),
        required=False,
    )
    case = serializers.UUIDField(required=False)
    document = serializers.UUIDField(required=False)
    legal_document = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs.get("model") and not attrs.get("provider"):
            raise serializers.ValidationError({"provider": "Select a provider when choosing a model."})
        return attrs
