from django.contrib import admin

from .models import Location, Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "unit_of_measure", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "sku")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
