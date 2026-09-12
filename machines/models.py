from django.db import models

RETIRE_HELP_TEXT = (
    'Odškrtnutím stroj vyřadíte místo smazání — zmizí z nabídek ve formuláři '
    'Zpracování, ale historie zůstane zachována. Smazání je zablokováno, jakmile '
    'má stroj nějaké využití, právě proto, aby historie nemohla zmizet.'
)


class Machine(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name='název')
    is_active = models.BooleanField(default=True, help_text=RETIRE_HELP_TEXT, verbose_name='aktivní')
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Kč/hod. Ve Strojích se násobí motohodinami. Prázdné = stroj se po hodinách neúčtuje.',
        verbose_name='hodinová sazba',
    )
    rate_per_ton = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Kč za tunu zpracovaného materiálu. Ve Strojích se násobí tunami. Prázdné = stroj se po tunách neúčtuje.',
        verbose_name='cena za tunu',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'stroj'
        verbose_name_plural = 'stroje'

    def __str__(self):
        return self.name
