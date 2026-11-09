import os

from django.core.management.base import BaseCommand

from key_management.services import import_environment_keys
from search.services.ai_wrapper import PROVIDERS


class Command(BaseCommand):
    help = "Save API keys from environment variables into the key master table, encrypted."

    def handle(self, *args, **options):
        disabled = {name.strip().lower() for name in os.getenv("AI_DISABLED_PROVIDERS", "").split(",")}
        names = {slug: provider.label for slug, provider in PROVIDERS.items()}
        for row in import_environment_keys(names, disabled):
            status = "active" if row["active"] else "switched off"
            self.stdout.write(f"{row['provider']}: {row['found']} found, {row['added']} added, {status}")
