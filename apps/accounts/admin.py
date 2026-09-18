from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group as AuthGroup

from .models import User

# Djangos Berechtigungsgruppen werden nicht benutzt, die Gruppen dieses
# Projekts stehen in apps.groups. Zwei gleichnamige Eintraege im Admin
# waeren nur verwirrend.
admin.site.unregister(AuthGroup)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "full_name", "personnel_number", "is_accounting", "is_active")
    list_filter = ("is_accounting", "is_active", "is_superuser")
    search_fields = ("email", "first_name", "last_name", "display_name", "personnel_number")
    ordering = ("email",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Zeiterfassung",
            {"fields": ("display_name", "personnel_number", "is_accounting")},
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "Zeiterfassung",
            {"fields": ("email", "display_name", "personnel_number", "is_accounting")},
        ),
    )
