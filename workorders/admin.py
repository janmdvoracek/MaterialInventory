from decimal import Decimal

from django import forms
from django.contrib import admin
from django.core.validators import (
    DecimalValidator,
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
    StepValueValidator,
)

from .forms import (
    DESCRIPTION_MAX_LENGTH,
    PHOTO_MAX_COUNT,
    MachineUsageForm,
    MovementItemForm,
    RefuelRowForm,
    WorkerHoursForm,
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


class _PlacesValidator(DecimalValidator):
    """Django's decimal-places check, on the value without trailing zeros.

    The admin re-posts a stored 2 as 2.00, which is not two decimal places.
    """

    def __call__(self, value):
        super().__call__(value.normalize())


class CappedInline(admin.TabularInline):
    """An inline holding its number fields to the entry forms' rules (`rules`: field -> that form's field).

    The bounds, the step and the decimal places, read off the entry form's own
    field, so a value saved here is never refused when the entry is next opened
    on `job_edit` or `refuel_edit`.
    """

    rules = {}
    # Fields stored signed: the bounds cap the size either way, and there is no step.
    signed = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        rule = self.rules.get(db_field.name)
        if rule is None:
            return field
        low = -rule.max_value if db_field.name in self.signed else rule.min_value
        field.validators += [
            MaxValueValidator(rule.max_value),
            MinValueValidator(low),
            _PlacesValidator(None, rule.decimal_places),
        ]
        if rule.step_size is not None and db_field.name not in self.signed:
            field.validators.append(StepValueValidator(rule.step_size, offset=rule.min_value))
        # Set outright: the widget already carries the column's own step (0.001).
        step = rule.step_size or Decimal(1).scaleb(-rule.decimal_places)
        field.widget.attrs.update(max=str(rule.max_value), min=str(low), step=str(step))
        return field


class MovementAdminForm(forms.ModelForm):
    """A line item whose sign matches its type: consumed negative, produced positive, never zero.

    Materiál sums the signed quantities, and `job_edit` reads the typed amount back with `abs()`.
    """

    sign_errors = {
        StockMovement.MovementType.TRANSFORM_CONSUME: 'Spotřeba se zapisuje záporným množstvím.',
        StockMovement.MovementType.TRANSFORM_PRODUCE: 'Výroba se zapisuje kladným množstvím.',
    }

    class Meta:
        model = StockMovement
        fields = ('material', 'movement_type', 'quantity')

    def clean(self):
        cleaned_data = super().clean()
        movement_type, quantity = cleaned_data.get('movement_type'), cleaned_data.get('quantity')
        if movement_type is None or quantity is None:
            return cleaned_data
        consumed = movement_type == StockMovement.MovementType.TRANSFORM_CONSUME
        if (quantity < 0) != consumed or quantity == 0:
            self.add_error('quantity', self.sign_errors[movement_type])
        return cleaned_data


# Line items are edited only through this inline, never registered on their own.
class MovementInline(CappedInline):
    model = StockMovement
    form = MovementAdminForm
    extra = 1
    fields = ('material', 'movement_type', 'quantity')
    rules = {'quantity': MovementItemForm.base_fields['quantity']}
    # Consumed rows are stored negative.
    signed = ('quantity',)


class MachineUsageInline(CappedInline):
    model = MachineUsage
    extra = 1
    fields = ('machine', 'hours', 'tons')
    rules = {'hours': MachineUsageForm.base_fields['hours'], 'tons': MachineUsageForm.base_fields['tons']}


# A refuelling's rows, edited only as an inline of `RefuellingAdmin`, never registered top-level.
class MachineRefuelInline(CappedInline):
    model = MachineRefuel
    extra = 1
    fields = ('machine', 'source', 'litres')
    rules = {'litres': RefuelRowForm.base_fields['litres']}


# The author's own hours too: the job form's „Moje hodiny" follows the same rules as a row.
class WorkerHoursInline(CappedInline):
    model = WorkerHours
    extra = 1
    fields = ('user', 'hours')
    rules = {'hours': WorkerHoursForm.base_fields['hours']}


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
        # The job's editable fields. Not `collaborators`: it follows the hours
        # rows (`WorkOrder.derive_collaborators`), and is shown read-only.
        fields = ('performed_on', 'location', 'description', 'notes', 'status')

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
    # Review fields are written by `job_approve`; collaborators follow the hours rows.
    readonly_fields = ('created_by', 'reviewed_at', 'reviewed_by', 'collaborators')
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

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # After the inlines, so the hours rows it reads are the saved ones.
        form.instance.derive_collaborators()


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
