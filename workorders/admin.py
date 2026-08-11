from django.contrib import admin

from inventory.models import StockMovement

from .models import MachineUsage, WorkOrder


class MovementInline(admin.TabularInline):
    model = StockMovement
    extra = 1
    fields = ('material', 'location', 'movement_type', 'quantity', 'notes', 'created_at')
    readonly_fields = ('created_at',)


class MachineUsageInline(admin.TabularInline):
    model = MachineUsage
    extra = 1
    fields = ('machine', 'hours', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_at', 'created_by', 'description')
    readonly_fields = ('created_by',)
    filter_horizontal = ('collaborators',)
    inlines = [MovementInline, MachineUsageInline]

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, StockMovement) and not instance.created_by_id:
                instance.created_by = request.user
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()
