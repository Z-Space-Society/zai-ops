from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class ZaiUserAdmin(UserAdmin):
    """Admin for the DID-keyed member model.

    Extends Django's UserAdmin so the standard auth fieldsets still work, and
    surfaces the atproto identity fields (`did`, `pds_url`, `last_seen`).
    """

    list_display = ("username", "did", "pds_url", "last_seen", "is_staff")
    search_fields = ("username", "did")
    readonly_fields = ("did", "last_seen", "last_login", "date_joined")

    # Add the atproto identity fields to UserAdmin's default fieldsets.
    fieldsets = UserAdmin.fieldsets + (
        ("ATProto identity", {"fields": ("did", "pds_url", "last_seen")}),
    )
