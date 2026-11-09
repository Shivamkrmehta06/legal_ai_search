from django.db import models

from .crypto import decrypt_key, encrypt_key, fingerprint_key


class ModelProvider(models.Model):
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class KeyMaster(models.Model):
    provider = models.ForeignKey(ModelProvider, on_delete=models.CASCADE, related_name="keys")
    label = models.CharField(max_length=100, blank=True)
    encrypted_key = models.TextField(editable=False)
    key_fingerprint = models.CharField(max_length=64, unique=True, editable=False)
    key_hint = models.CharField(max_length=8, editable=False)
    priority = models.PositiveIntegerField(default=0, help_text="Lower numbers are tried first.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__name", "priority", "id"]
        verbose_name = "key master"
        verbose_name_plural = "key master"

    def __str__(self):
        return f"{self.provider} ({self.masked_key})"

    @property
    def masked_key(self) -> str:
        return f"••••{self.key_hint}"

    def set_key(self, api_key: str) -> None:
        self.encrypted_key = encrypt_key(api_key)
        self.key_fingerprint = fingerprint_key(api_key)
        self.key_hint = api_key[-4:]

    def get_key(self) -> str:
        return decrypt_key(self.encrypted_key)
