from django.contrib import admin

from .models import MachineUsage, StockMovement, WorkerHours, WorkOrder


# StockMovement is deliberately edited here and nowhere else — it is not
# registered as a model of its own. A line item never exists apart from the job
# it belongs to, and this inline already edits them, so registering it too put a
# second top-level section in the admin index holding one model that was
# reachable from the job anyway. Everything about a job is edited in one place,
# under Zakázky. `accounts.tests.AdminIndexTests` asserts the section stays gone.
class MovementInline(admin.TabularInline):
    model = StockMovement
    extra = 1
    fields = ('material', 'movement_type', 'quantity')


class MachineUsageInline(admin.TabularInline):
    model = MachineUsage
    extra = 1
    fields = ('machine', 'hours', 'tons')


class WorkerHoursInline(admin.TabularInline):
    model = WorkerHours
    extra = 1
    fields = ('user', 'hours')


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'performed_on', 'created_at', 'created_by', 'description', 'status', 'reviewed_by')
    list_filter = ('status',)
    date_hierarchy = 'created_at'
    # Who reviewed it and when is written by `job_approve`; leaving them
    # editable here would let the two disagree about what happened.
    readonly_fields = ('created_by', 'reviewed_at', 'reviewed_by')
    # Deliberately not `filter_horizontal`: that widget builds its labels in
    # JavaScript from strings ("Choose %s by selecting them...") that Django's
    # Czech catalogs don't translate, so it renders half-English. The plain
    # multi-select is fully Czech and the staff list is short.
    inlines = [MovementInline, MachineUsageInline, WorkerHoursInline]

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
