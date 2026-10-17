"""The sections of an entry page, each owning its rows end to end.

A section is declared once, in `JOB_SECTIONS` or `REFUEL_SECTIONS`, and knows
how to render (`row_form`, `picker`, the sum and mirror links), how its rows
come off a recorded entry (`recorded_rows`), which retired catalog records
those rows keep selectable (`row_form_kwargs`), and how to store them again
(`write`). `entry.EntryPage` drives all of it.
"""

from dataclasses import dataclass
from decimal import Decimal

from .forms import MachineUsageForm, MovementItemForm, RefuelRowForm, WorkerHoursForm
from .models import MachineRefuel, MachineUsage, StockMovement, WorkerHours


def _trim(value):
    """A stored Decimal without trailing zeros, so the stricter form accepts it as `initial`.

    Avoids normalize() turning 100.00 into 1E+2.
    """
    normalized = value.normalize()
    return normalized.quantize(Decimal(1)) if normalized.as_tuple().exponent > 0 else normalized


def _trimmed(row):
    """A row's Decimals passed through `_trim`; None (unknown) stays None."""
    return {key: _trim(value) if isinstance(value, Decimal) else value for key, value in row.items()}


@dataclass(frozen=True, kw_only=True)
class Section:
    """One row section of an entry page. `prefix` also names its row buttons (`add_<prefix>`).

    `picker` is the one field per row a section may name only once
    (`UniqueChoiceFormSet`), refused with `duplicate_error`; it is also the
    catalog picker `keep` widens (see `forms._offer_recorded`).

    `sum_from` names another section whose `sum_field` total this section's
    single row can be filled with (the `sum_<prefix>` button): a screening job
    weighs only what comes out.

    `mirror_from` names another section whose `mirror_source` total is copied
    into this section's `mirror_field` on every row that names its picker:
    the machines usually process exactly what the job consumed.
    """

    prefix: str
    context_name: str
    title: str
    row_form: type
    picker: str
    duplicate_error: str
    sum_from: str = ''
    sum_field: str = ''
    mirror_from: str = ''
    mirror_source: str = ''
    mirror_field: str = ''

    def recorded_rows(self, entry, author):
        """The section's stored rows on `entry`, as the row form takes them."""
        raise NotImplementedError

    def row_form_kwargs(self, recorded, *, author, viewer):
        """Each row form's kwargs: `keep` the records `recorded` names, even retired ones."""
        return {'keep': [row[self.picker] for row in recorded]}

    def write(self, entry, rows, author):
        """Replace the section's stored rows on `entry` with `rows`. Call inside `transaction.atomic()`."""
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class WorkersSection(Section):
    """The collaborators and their hours. The author's own hours are the job's own field, not a row here."""

    def recorded_rows(self, entry, author):
        return [{'user': row.user_id, 'hours': _trim(row.hours)} for row in entry.worker_hours.exclude(user=author)]

    def row_form_kwargs(self, recorded, *, author, viewer):
        # People aren't retired like catalog records; the list depends on who writes for whom.
        return {'user': author, 'viewer': viewer}

    def write(self, entry, rows, author):
        # Derived from the hours rows, so the two can't disagree.
        entry.collaborators.set(row['user'] for row in rows)
        entry.worker_hours.exclude(user=author).delete()
        WorkerHours.objects.bulk_create(
            WorkerHours(work_order=entry, user=row['user'], hours=row['hours']) for row in rows
        )


@dataclass(frozen=True, kw_only=True)
class MaterialSection(Section):
    """One direction of the job's line items: consumed or produced, as typed (positive)."""

    movement_type: str

    def _sign(self):
        # Consumed quantities are stored negative, so a material's net is a plain Sum.
        return -1 if self.movement_type == StockMovement.MovementType.TRANSFORM_CONSUME else 1

    def recorded_rows(self, entry, author):
        return [
            {'material': movement.material_id, 'quantity': _trim(abs(movement.quantity))}
            for movement in entry.movements.filter(movement_type=self.movement_type)
        ]

    def write(self, entry, rows, author):
        entry.movements.filter(movement_type=self.movement_type).delete()
        StockMovement.objects.bulk_create(
            StockMovement(
                work_order=entry,
                created_by=author,
                material=row['material'],
                quantity=self._sign() * row['quantity'],
                movement_type=self.movement_type,
            )
            for row in rows
        )


@dataclass(frozen=True, kw_only=True)
class MachineUsageSection(Section):
    """The machines a job ran: meter reading and tonnage, either possibly unknown."""

    def recorded_rows(self, entry, author):
        return [
            _trimmed({'machine': usage.machine_id, 'hours': usage.hours, 'tons': usage.tons})
            for usage in entry.machine_usages.all()
        ]

    def write(self, entry, rows, author):
        entry.machine_usages.all().delete()
        MachineUsage.objects.bulk_create(
            MachineUsage(work_order=entry, machine=row['machine'], hours=row['hours'], tons=row['tons']) for row in rows
        )


@dataclass(frozen=True, kw_only=True)
class RefuelSection(Section):
    """A refuelling's fill-ups: machine, optional source, litres."""

    def recorded_rows(self, entry, author):
        return [
            _trimmed({'machine': fill_up.machine_id, 'source': fill_up.source_id, 'litres': fill_up.litres})
            for fill_up in entry.machine_refuels.all()
        ]

    def row_form_kwargs(self, recorded, *, author, viewer):
        return {
            **super().row_form_kwargs(recorded, author=author, viewer=viewer),
            'keep_source': [row['source'] for row in recorded if row['source']],
        }

    def write(self, entry, rows, author):
        entry.machine_refuels.all().delete()
        MachineRefuel.objects.bulk_create(
            MachineRefuel(refuelling=entry, machine=row['machine'], source=row['source'], litres=row['litres'])
            for row in rows
        )


# In render order. A new section only needs adding here.
JOB_SECTIONS = (
    WorkersSection(
        prefix='workers',
        context_name='worker_formset',
        title='Spolupracovníci',
        row_form=WorkerHoursForm,
        picker='user',
        duplicate_error='Pracovník „{name}“ je vybraný víckrát. Sečtěte hodiny do jednoho řádku.',
    ),
    MaterialSection(
        prefix='consumed',
        context_name='consumed_formset',
        title='Spotřebováno',
        row_form=MovementItemForm,
        picker='material',
        duplicate_error='Materiál „{name}“ je v této sekci vybraný víckrát. Sečtěte množství do jednoho řádku.',
        movement_type=StockMovement.MovementType.TRANSFORM_CONSUME,
        sum_from='produced',
        sum_field='quantity',
    ),
    MaterialSection(
        prefix='produced',
        context_name='produced_formset',
        title='Vyrobeno',
        row_form=MovementItemForm,
        picker='material',
        duplicate_error='Materiál „{name}“ je v této sekci vybraný víckrát. Sečtěte množství do jednoho řádku.',
        movement_type=StockMovement.MovementType.TRANSFORM_PRODUCE,
    ),
    MachineUsageSection(
        prefix='machines',
        context_name='machine_formset',
        title='Použité stroje',
        row_form=MachineUsageForm,
        picker='machine',
        duplicate_error='Stroj „{name}“ je vybraný víckrát. Zapište ho jen do jednoho řádku.',
        mirror_from='consumed',
        mirror_source='quantity',
        mirror_field='tons',
    ),
)

# The refuelling page's one section.
REFUEL_SECTIONS = (
    RefuelSection(
        prefix='refuels',
        context_name='refuel_formset',
        title='Tankované stroje',
        row_form=RefuelRowForm,
        picker='machine',
        duplicate_error=(
            'Stroj „{name}“ je vybraný víckrát. Sečtěte litry do jednoho řádku, '
            'nebo tankování z jiného zdroje zapište zvlášť.'
        ),
    ),
)
