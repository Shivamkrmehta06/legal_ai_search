from django import forms
from django.contrib import admin

from .crypto import fingerprint_key
from .models import KeyMaster, ModelProvider


class KeyMasterForm(forms.ModelForm):
    new_key = forms.CharField(
        label="API key",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Saved encrypted and never shown again. Leave blank to keep the current key.",
    )

    class Meta:
        model = KeyMaster
        fields = ["provider", "label", "new_key", "priority", "is_active"]

    def clean_new_key(self):
        new_key = self.cleaned_data["new_key"].strip()
        if not new_key and not self.instance.pk:
            raise forms.ValidationError("Enter the API key.")
        if new_key:
            duplicates = KeyMaster.objects.filter(key_fingerprint=fingerprint_key(new_key))
            if duplicates.exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("This key has already been saved.")
        return new_key

    def save(self, commit=True):
        if self.cleaned_data["new_key"]:
            self.instance.set_key(self.cleaned_data["new_key"])
        return super().save(commit=commit)


class KeyMasterInline(admin.TabularInline):
    model = KeyMaster
    fields = ["masked_key", "label", "priority", "is_active"]
    readonly_fields = ["masked_key"]
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ModelProvider)
class ModelProviderAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    inlines = [KeyMasterInline]


@admin.register(KeyMaster)
class KeyMasterAdmin(admin.ModelAdmin):
    form = KeyMasterForm
    list_display = ["provider", "masked_key", "label", "priority", "is_active", "updated_at"]
    list_filter = ["provider", "is_active"]
    search_fields = ["label", "provider__name"]
