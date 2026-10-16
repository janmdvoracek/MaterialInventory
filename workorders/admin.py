from django import forms
from django.contrib import admin
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator

from .forms import (
    DESCRIPTION_MAX_LENGTH,
    LITRES_MAX,
    METER_READING_MAX,
    PHOTO_MAX_COUNT,
    TONS_MAX,
    WORKER_HOURS_MAX,
    clean_photo_upload,
)
from .models import (
    JobPhoto,
    MachineRefuel,
    MachineUsage,
    Refuelling,
    StockMovement,
    WorkerHours,
    WorkOrder,
    discard_photo,
)


class CappedInline(admin.TabularInline):
    """An inline capping its number fields at the job form's bounds (`caps`: field -> limit).

    Otherwise a value saved here would be refused when the job is next opened on `job_edit`.
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


# A refuelling's rows, edited only as an inline of `RefuellingAdmin`, never registered top-level.
class MachineRefuelInline(CappedInline):
    model = MachineRefuel
    extra = 1
    fields = ('machine', 'source', 'litres')
    caps = {'litres': LITRES_MAX}


class WorkerHoursInline(CappedInline):
    model = WorkerHours
    extra = 1
    fields = ('user', 'hours')
    caps = {'hours': WORKER_HOURS_MAX}


class JobPhotoForm(forms.ModelForm):
    class Meta:
        model = JobPhoto
        fields = ('file',)

    def clean_file(self):
        # Shrunk exactly like a job-form upload.
        return clean_photo_upload(self.cleaned_data.get('file'))


class JobPhotoInline(admin.TabularInline):
    model = JobPhoto
    form = JobPhotoForm
    extra = 1
    # The job form's cap, so a job filled up here still accepts edits on `job_edit`.
    max_num = PHOTO_MAX_COUNT


class WorkOrderAdminForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        # Every field on purpose: this form only adds the description cap, and
        # WorkOrderAdmin declares no `fields`, so the admin shows whatever the model has.
        fields = '__all__'  # noqa: DJ007

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The job form's cap, not the column's 255, so the job stays editable on `job_edit`.
        description = self.fields['description']
        description.max_length = DESCRIPTION_MAX_LENGTH
        description.validators.append(MaxLengthValidator(DESCRIPTION_MAX_LENGTH))
        description.widget.attrs['maxlength'] = str(DESCRIPTION_MAX_LENGTH)


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
    inlines = [MovementInline, MachineUsageInline, WorkerHoursInline, JobPhotoInline]

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        # A file swapped on a surviving row: the post_delete receiver can't see
        # this case, so discard the old file here.
        if formset.model is JobPhoto:
            for photo_form in formset.forms:
                if photo_form.instance.pk and 'file' in photo_form.changed_data:
                    discard_photo(JobPhoto.objects.get(pk=photo_form.instance.pk).file)
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, StockMovement) and not instance.created_by_id:
                instance.created_by = request.user
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(Refuelling)
class RefuellingAdmin(admin.ModelAdmin):
    list_display = ('id', 'performed_on', 'created_at', 'created_by', 'status', 'reviewed_by')
    list_filter = ('status',)
    date_hierarchy = 'performed_on'
    # Review fields are written by `refuel_approve`.
    readonly_fields = ('created_by', 'reviewed_at', 'reviewed_by')
    inlines = [MachineRefuelInline]

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
