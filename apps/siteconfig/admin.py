from django.contrib import admin

from .models import FooterLink

# SiteSettings steht nicht im Django-Admin: die Zeile wird über die Seite
# "Darstellung" gepflegt, wo das Logo geprüft wird, bevor es gespeichert wird.


@admin.register(FooterLink)
class FooterLinkAdmin(admin.ModelAdmin):
    list_display = ("label", "url", "position")
    ordering = ("position", "label")
