from django.contrib import admin

from inventory.models import StockMovement

from .models import MachineUsage, WorkOrder


class MovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    readonly_fields = ("material", "location", "movement_type", "quantity", "created_by", "created_at")
    can_delete = False


class MachineUsageInline(admin.TabularInline):
    model = MachineUsage
    extra = 0
    readonly_fields = ("machine", "hours", "created_at")
    can_delete = False


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "created_by", "description")
    inlines = [MovementInline, MachineUsageInline]
