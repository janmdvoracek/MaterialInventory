from django.db import models

RETIRE_HELP_TEXT = (
    'Odškrtnutím materiál vyřadíte místo smazání — zmizí z nabídek ve formuláři '
    'Zpracování, ale historie zůstane zachována. Smazání je zablokováno, jakmile '
    'má materiál nějakou položku zpracování, právě proto, aby historie nemohla zmizet.'
)


class Material(models.Model):
    sku = models.CharField(max_length=50, unique=True, verbose_name='kód (SKU)')
    name = models.CharField(max_length=200, verbose_name='název')
    is_active = models.BooleanField(default=True, help_text=RETIRE_HELP_TEXT, verbose_name='aktivní')

    class Meta:
        ordering = ['name']
        verbose_name = 'materiál'
        verbose_name_plural = 'materiály'

    def __str__(self):
        return f'{self.name} ({self.sku})'
