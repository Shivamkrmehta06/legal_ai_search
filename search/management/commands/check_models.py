"""Send one real request to each suitable model and report which ones work."""

from concurrent.futures import ThreadPoolExecutor
import logging
import time

from django.core.management.base import BaseCommand

from search.services.ai_wrapper import AIWrapperError, ModelTarget, complete_structured
from search.services.model_catalog import list_models, list_providers
from search.services.query_analyzer import GEMINI_SCHEMA, SYSTEM_INSTRUCTIONS, LegalQuery

TEST_QUERY = "Supreme Court decisions on cheque bounce under Section 138 of the NI Act"
ATTEMPTS = 2
RETRY_DELAY_SECONDS = 5


def model_works(provider: str, model: str) -> bool:
    for attempt in range(ATTEMPTS):
        try:
            complete_structured(
                [ModelTarget(provider, model)], SYSTEM_INSTRUCTIONS, TEST_QUERY, LegalQuery, GEMINI_SCHEMA
            )
            return True
        except AIWrapperError:
            # A busy model may recover, so one failure doesn't rule it out.
            if attempt + 1 < ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    return False


class Command(BaseCommand):
    help = "Test each suitable AI model with one real request. This uses your API quota."

    def handle(self, *args, **options):
        logging.getLogger("search.services.ai_wrapper").setLevel(logging.ERROR)
        for provider in list_providers():
            if not provider["configured"]:
                self.stdout.write(f"{provider['label']}: skipped, no API key or switched off.")
                continue
            models = [model["id"] for model in list_models(provider["id"], only_tested=False)]
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(lambda model: model_works(provider["id"], model), models))
            working = [model for model, works in zip(models, results) if works]
            self.stdout.write(f"{provider['label']}: {len(working)} of {len(models)} models work.")
            for model, works in zip(models, results):
                self.stdout.write(f"  {'OK  ' if works else 'FAIL'} {model}")
            self.stdout.write(f"{provider['id'].upper()}_AVAILABLE_MODELS={','.join(working)}")
