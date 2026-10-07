from django import forms
from django.contrib import admin
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator

from .forms import (
    DESCRIPTION_MAX_LENGTH,
    LITRES_MAX,
    METER_READING_MAX,
    TONS_MAX,
    WORKER_HOURS_MAX,
    clean_photo_upload,
)
from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours, WorkOrder, discard_photo


class CappedInline(admin.TabularInline):
    """An inline holding its number columns to the job form's upper bounds.

    `caps` maps a field name to its limit. Without them a value saved here would
    be refused the next time the job is opened on `job_edit`, the same reason
    `WorkOrderAdminForm` repeats the description cap.
    """

    caps = {}
    # Fields stored signed, so the cap bounds the size either way.
    signed = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        limit = self.caps.get(db_field.name)
        if limit is not None:
            field.validators.append(MaxValueValidator(limit))
            field.widget.attrs['max'] = str(limit)
            if db_field.name in self.signed:
                field.validators.append(MinValueValidator(-limit))
                field.widget.attrs['min'] = str(-limit)
        return field


# Line items are edited only through this inline, never registered on their own.
class MovementInline(CappedInline):
    model = StockMovement
    extra = 1
    fields = ('material', 'movement_type', 'quantity')
    caps = {'quantity': TONS_MAX}
    # Consumed rows are stored negative.
    signed = ('quantity',)


class MachineUsageInline(CappedInline):
    model = MachineUsage
    extra = 1
    fields = ('machine', 'hours', 'tons')
    caps = {'hours': METER_READING_MAX, 'tons': TONS_MAX}


# Fuel is its own table, so its own inline — and, like every other row type, it
# is never registered top-level: a fill-up exists only as part of a job, and a
# second „Tankování" section in the index would shadow the worker form's.
class MachineRefuelInline(CappedInline):
    model = MachineRefuel
    extra = 1
    fields = ('machine', 'litres')
    caps = {'litres': LITRES_MAX}


class WorkerHoursInline(CappedInline):
    model = WorkerHours
    extra = 1
    fields = ('user', 'hours')
    caps = {'hours': WORKER_HOURS_MAX}


class WorkOrderAdminForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The job form's cap, not the column's 255: a longer description saved
        # here would be refused the next time the job is opened on `job_edit`.
        description = self.fields['description']
        description.max_length = DESCRIPTION_MAX_LENGTH
        description.validators.append(MaxLengthValidator(DESCRIPTION_MAX_LENGTH))
        description.widget.attrs['maxlength'] = str(DESCRIPTION_MAX_LENGTH)

    def clean_photo(self):
        # Shrunk like one sent through the job form, or an upload here would
        # be the one full-size photo on a disk sized for small ones.
        return clean_photo_upload(self.cleaned_data.get('photo'))


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    form = WorkOrderAdminForm
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
        if change and 'photo' in form.changed_data:
            # The admin writes `photo` straight through its own widget, so
            # `views._apply_photo` is not in play; without this, replacing or
            # clearing a photo here would leave the old file on disk. A job
            # deleted from the admin is covered by the post_delete receiver.
            discard_photo(WorkOrder.objects.get(pk=obj.pk).photo)
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
