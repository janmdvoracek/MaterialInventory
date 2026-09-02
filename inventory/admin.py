from django.contrib import admin

from .models import StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'movement_type', 'material', 'quantity', 'created_by', 'work_order')
    list_filter = ('movement_type', 'material')
    search_fields = ('material__name', 'material__sku', 'notes')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)
