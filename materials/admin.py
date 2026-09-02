from django.contrib import admin

from .models import Machine, Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'unit_of_measure', 'category', 'is_active')
    list_editable = ('category', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name', 'sku')


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ('name', 'total_hours', 'hourly_rate', 'rate_per_ton', 'is_active')
    list_editable = ('total_hours', 'hourly_rate', 'rate_per_ton', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
