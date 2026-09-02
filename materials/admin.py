from django.contrib import admin

from .models import Machine, Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'is_active')
    list_editable = ('is_active',)
    list_filter = ('is_active',)
    search_fields = ('name', 'sku')


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ('name', 'hourly_rate', 'rate_per_ton', 'is_active')
    list_editable = ('hourly_rate', 'rate_per_ton', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
