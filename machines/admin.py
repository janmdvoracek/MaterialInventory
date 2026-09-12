from django.contrib import admin

from .models import Machine


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ('name', 'hourly_rate', 'rate_per_ton', 'is_active')
    list_editable = ('hourly_rate', 'rate_per_ton', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
