from django.contrib import admin

from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder


# Line items are edited only through this inline, never registered on their own.
class MovementInline(admin.TabularInline):
    model = StockMovement
    extra = 1
    fields = ('material', 'movement_type', 'quantity')


class MachineUsageInline(admin.TabularInline):
    model = MachineUsage
    extra = 1
    fields = ('machine', 'hours', 'tons')


# Fuel is its own table, so its own inline — and, like every other row type, it
# is never registered top-level: a fill-up exists only as part of a job, and a
# second „Tankování" section in the index would shadow the worker form's.
class MachineRefuelInline(admin.TabularInline):
    model = MachineRefuel
    extra = 1
    fields = ('machine', 'litres')


class WorkerHoursInline(admin.TabularInline):
    model = WorkerHours
    extra = 1
    fields = ('user', 'hours')


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'performed_on',
        'created_at',
        'created_by',
        'location',
        'description',
        'status',
        'reviewed_by',
    )
    list_filter = ('status', 'location')
    date_hierarchy = 'performed_on'
    # Review fields are written by `job_approve`.
    readonly_fields = ('created_by', 'reviewed_at', 'reviewed_by')
    # No `filter_horizontal` for collaborators: its JavaScript labels aren't translated to Czech.
    inlines = [MovementInline, MachineUsageInline, MachineRefuelInline, WorkerHoursInline]

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
