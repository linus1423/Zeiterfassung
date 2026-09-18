from django.contrib import admin

from .models import Activity, Group, GroupMembership


class MembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0
    autocomplete_fields = ("user",)


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "cost_center", "is_active", "created_at")
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
    list_display = ("user", "group", "role", "joined_at")
    list_filter = ("group", "role")
    autocomplete_fields = ("user",)
