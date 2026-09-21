from django.contrib import admin

from .models import (
    Activity,
    Group,
    GroupChangeRequest,
    GroupMembership,
    PeriodConfirmation,
    PeriodLock,
)


class MembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0
    autocomplete_fields = ("user",)


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "cost_center", "month_start_day", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "cost_center")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline, ActivityInline]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "is_active", "sort_order")
    list_filter = ("group", "is_active")
    search_fields = ("name",)


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "role", "source", "joined_at")
    list_filter = ("group", "role", "source")
    autocomplete_fields = ("user",)


@admin.register(PeriodLock)
class PeriodLockAdmin(admin.ModelAdmin):
    list_display = ("group", "period_start", "period_end", "closed_by", "closed_at")
    list_filter = ("group",)
    autocomplete_fields = ("closed_by",)


@admin.register(GroupChangeRequest)
class GroupChangeRequestAdmin(admin.ModelAdmin):
    """Nur zum Nachsehen: entschieden wird im Tool, damit es protokolliert wird."""

    list_display = ("user", "from_group", "to_group", "status", "created_at")
    list_filter = ("status", "from_group", "to_group")
    autocomplete_fields = ("user",)


@admin.register(PeriodConfirmation)
class PeriodConfirmationAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "period_start", "period_end", "confirmed_at", "entry_count")
    list_filter = ("group",)
    autocomplete_fields = ("user",)
