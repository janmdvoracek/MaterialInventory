from django.db import models

RETIRE_HELP_TEXT = (
    'Odškrtnutím lokaci vyřadíte místo smazání — zmizí z nabídky ve formuláři '
    'Zpracování, ale historie zůstane zachována. Smazání je zablokováno, jakmile '
    'lokaci nese nějaké zpracování, právě proto, aby historie nemohla zmizet.'
)


class Location(models.Model):
    """Where a job was worked. A label on the job, not a stock location — nothing is balanced per location."""

    name = models.CharField(max_length=100, unique=True, verbose_name='název')
    is_active = models.BooleanField(default=True, help_text=RETIRE_HELP_TEXT, verbose_name='aktivní')

    class Meta:
        ordering = ['name']
        verbose_name = 'lokace'
        verbose_name_plural = 'lokace'

    def __str__(self):
        return self.name
