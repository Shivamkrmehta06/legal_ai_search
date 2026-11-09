from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import KeyMaster


@api_view(["GET"])
@permission_classes([IsAdminUser])
def key_list(request):
    """List stored keys for admins, showing only the last four characters."""
    keys = KeyMaster.objects.select_related("provider")
    return Response(
        {
            "success": True,
            "keys": [
                {
                    "id": key.id,
                    "provider": key.provider.slug,
                    "provider_active": key.provider.is_active,
                    "key": key.masked_key,
                    "label": key.label,
                    "priority": key.priority,
                    "is_active": key.is_active,
                }
                for key in keys
            ],
        }
    )
