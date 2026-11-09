import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from search.services.ai_wrapper import api_keys

from .admin import KeyMasterForm
from .models import KeyMaster, ModelProvider
from .services import stored_api_keys

ENVIRONMENT = {
    "AI_DISABLED_PROVIDERS": "openai",
    "GEMINI_API_KEY": "gemini-secret-1111",
    "GEMINI_API_KEYS": "gemini-secret-2222",
    "GOOGLE_API_KEY": "",
    "GROQ_API_KEY": "groq-secret-3333",
    "GROQ_API_KEYS": "",
    "OPENAI_API_KEY": "openai-secret-4444",
    "OPENAI_API_KEYS": "",
    "OPENROUTER_API_KEY": "router-secret-5555",
    "OPENROUTER_API_KEYS": "",
    "MISTRAL_API_KEY": "",
    "MISTRAL_API_KEYS": "",
    "COHERE_API_KEY": "",
    "COHERE_API_KEYS": "",
    "CEREBRAS_API_KEY": "",
    "CEREBRAS_API_KEYS": "",
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_API_KEYS": "",
    "XAI_API_KEY": "",
    "XAI_API_KEYS": "",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_API_KEYS": "",
}


@patch.dict(os.environ, ENVIRONMENT, clear=False)
class ImportEnvKeysTests(TestCase):
    def test_keys_are_attached_to_their_providers(self):
        call_command("import_env_keys", verbosity=0, stdout=open(os.devnull, "w"))

        counts = {provider.slug: provider.keys.count() for provider in ModelProvider.objects.all()}
        self.assertEqual(counts, {"gemini": 2, "groq": 1, "openai": 1, "openrouter": 1})

    def test_keys_are_encrypted_in_the_table(self):
        call_command("import_env_keys", stdout=open(os.devnull, "w"))

        for entry in KeyMaster.objects.all():
            self.assertNotIn("secret", entry.encrypted_key)
            self.assertIn("secret", entry.get_key())
        self.assertEqual(KeyMaster.objects.get(provider__slug="groq").key_hint, "3333")

    def test_importing_twice_adds_nothing(self):
        call_command("import_env_keys", stdout=open(os.devnull, "w"))
        call_command("import_env_keys", stdout=open(os.devnull, "w"))

        self.assertEqual(KeyMaster.objects.count(), 5)

    def test_disabled_and_retired_providers_are_imported_switched_off(self):
        call_command("import_env_keys", stdout=open(os.devnull, "w"))

        self.assertFalse(ModelProvider.objects.get(slug="openai").is_active)
        self.assertFalse(ModelProvider.objects.get(slug="openrouter").is_active)
        self.assertTrue(ModelProvider.objects.get(slug="gemini").is_active)

    def test_import_keeps_a_provider_switched_on_in_the_admin(self):
        call_command("import_env_keys", stdout=open(os.devnull, "w"))
        ModelProvider.objects.filter(slug="openai").update(is_active=True)

        call_command("import_env_keys", stdout=open(os.devnull, "w"))

        self.assertTrue(ModelProvider.objects.get(slug="openai").is_active)


@patch.dict(os.environ, {**ENVIRONMENT, "AI_DISABLED_PROVIDERS": ""}, clear=False)
class StoredKeysTests(TestCase):
    def make_key(self, slug, key, **fields):
        provider, _ = ModelProvider.objects.get_or_create(slug=slug, defaults={"name": slug})
        entry = KeyMaster(provider=provider, **fields)
        entry.set_key(key)
        entry.save()
        return entry

    def test_empty_table_falls_back_to_environment_keys(self):
        self.assertEqual(api_keys("groq"), ["groq-secret-3333"])

    def test_table_keys_are_used_before_environment_keys(self):
        self.make_key("groq", "table-key-second", priority=2)
        self.make_key("groq", "table-key-first", priority=1)

        self.assertEqual(api_keys("groq"), ["table-key-first", "table-key-second"])

    def test_inactive_key_is_skipped(self):
        self.make_key("groq", "off-key", is_active=False)
        self.make_key("groq", "on-key")

        self.assertEqual(api_keys("groq"), ["on-key"])

    def test_inactive_provider_has_no_keys(self):
        self.make_key("groq", "table-key")
        ModelProvider.objects.filter(slug="groq").update(is_active=False)

        self.assertEqual(api_keys("groq"), [])

    def test_undecryptable_keys_fall_back_to_environment_keys(self):
        entry = self.make_key("groq", "table-key")
        KeyMaster.objects.filter(pk=entry.pk).update(encrypted_key="not-valid")

        self.assertIsNone(stored_api_keys("groq"))
        self.assertEqual(api_keys("groq"), ["groq-secret-3333"])

    def test_admin_form_encrypts_and_rejects_duplicates(self):
        provider = ModelProvider.objects.create(slug="groq", name="Groq")
        data = {"provider": provider.pk, "new_key": "typed-key-9999", "priority": 0, "is_active": True}

        form = KeyMasterForm(data)
        self.assertTrue(form.is_valid())
        entry = form.save()

        self.assertEqual(entry.get_key(), "typed-key-9999")
        self.assertFalse(KeyMasterForm(data).is_valid())

    def test_key_list_is_admin_only_and_masked(self):
        self.make_key("groq", "groq-table-key-7777")
        url = "/api/key-management/keys/"
        client = APIClient()

        self.assertIn(client.get(url).status_code, {401, 403})

        admin = get_user_model().objects.create_superuser("admin", password="test-only-password")
        client.force_authenticate(admin)
        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["keys"][0]["key"], "••••7777")
        self.assertNotIn("groq-table-key", str(response.content))
