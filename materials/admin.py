from django.contrib import admin

from .models import Location, Machine, Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'unit_of_measure', 'category', 'track_stock', 'is_active')
    list_editable = ('category', 'track_stock', 'is_active')
    list_filter = ('category', 'track_stock', 'is_active')
    search_fields = ('name', 'sku')


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_editable = ('is_active',)


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ('name', 'total_hours', 'hourly_rate', 'is_active')
    list_editable = ('total_hours', 'hourly_rate', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
