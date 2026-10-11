# Localization

The interface is entirely Czech, through three mechanisms:

| Layer | Mechanism |
|---|---|
| App copy: labels, buttons, messages | **hardcoded Czech strings** in the source |
| Django's own strings: admin, auth, validation errors | `LANGUAGE_CODE = 'cs'` and Django's shipped catalogs |
| Django strings it never translated | `locale/cs/LC_MESSAGES/django.po`, holding Django msgids only |

> **Keep new UI text hardcoded.** Don't introduce `{% trans %}` or `gettext` for app copy. A half-migrated i18n setup is worse than either approach. Full i18n would be a deliberate project.

`LocaleMiddleware` is not installed, so the language is fixed: an English phone still gets Czech.

## Numbers render with a comma

`{{ row.hours }}` renders `12,50`, so tests asserting on rendered numbers need the comma.

**Form inputs are not localised.** Fields default to `localize=False`, so `<input type="number">` takes `12.50`, and a comma is rejected with „Zadejte číslo.". Leave it that way: `localize=True` turns the input into a text field and costs phone users the numeric keypad.

**Code outside templates must localise itself:**

- The mass-balance error passes its totals through `formats.localize`.
- The CSV exports use `number_format(value, decimal_pos=N)`. That comma is also why the files are `;`-delimited (Excel under `cs` splits on `;`), with a UTF-8 BOM so „Štěrk" isn't mojibake.

## Dates

Czech `DATE_INPUT_FORMATS` doesn't list ISO, but Django appends `%Y-%m-%d` to every locale, so `<input type="date">` values parse. Bound forms re-render the submitted string, so filters survive pagination.

**The trap:** an *unbound* date field with a Python `date` as initial renders `14.08.2026`, which `<input type="date">` shows as blank. `WorkOrderForm.performed_on` is exactly that case, so its widget sets the format:

```python
performed_on = forms.DateField(
    initial=timezone.localdate,
    widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
)
```

Any new pre-filled date field needs the same. `PerformedOnTests` asserts the rendered `value=`.

Templates use explicit formats (`|date:"Y-m-d H:i"`, 24-hour). A bare `{{ some_date }}` would render `14. srpna 2026`. The app has no `datetime-local` inputs; those show AM/PM on English phones regardless of `lang="cs"`.

## Time zones

`USE_TZ` is on, so the database stores UTC and `TIME_ZONE` affects only rendering. The date filters compare `performed_on`, a plain `DateField`, so no job can slip to the previous UTC day. Python code formatting a datetime must call `timezone.localtime()`; nothing does today.

## Every `ModelChoiceField` needs an `empty_label`

Django's default, `- Select an option -`, renders in English even under `cs`. Set it explicitly:

| Form | A blank choice means | Wording |
|---|---|---|
| job-form rows | it *is* the label | `Materiál`, `Stroj`, `Pracovník` |
| filters | "don't filter" | `Všechny stroje`, `Všechny materiály`, `Všichni pracovníci`, `Všichni uživatelé`, `Všechny lokace` |

Row selects sit side by side with no visible labels, so the empty option does the labelling. For the same reason, the row number boxes carry a unit placeholder (`t`, `mth`, `l`, `h`) and the full name as `title`, which is also their accessible name.

`EmptyLabelTests` fails if a field forgets. `ModelMultipleChoiceField` has no blank option and needs nothing.

## The admin

A new model or `ModelAdmin` needs all three of these, or it shows English somewhere:

1. **App labels.** `verbose_name` on each `AppConfig`: `Materiály`, `Stroje`, `Lokace`, `Zakázky`, `Uživatelé`. (`auth.Group` is unregistered, which removes Django's own section.)
2. **Model and field names.** `verbose_name`/`verbose_name_plural` in every `Meta`, and a lowercase `verbose_name` on every field. Label changes need a migration (`AlterField`).
3. **`LOCALE_PATHS`.** `locale/cs/LC_MESSAGES/django.po` supplies Django strings missing from its `cs` catalog: `- Select an option -`, the delete confirmation, the `date_hierarchy` label on `WorkOrderAdmin`, the user-add help text, and the screen-reader-only `Search %(name)s` / `Pagination %(name)s` headings. Django msgids only; never app strings.

**The runtime reads the compiled `.mo`.** Both files are committed. After editing the `.po`, run:

```bash
python manage.py compilemessages -l cs --ignore=.venv
```

Keep the `--ignore`, or it recompiles every catalog in the virtualenv. `AdminCzechTests` fails if the `.mo` is missing or stale.

**`filter_horizontal` can't be translated.** Its labels come from the `djangojs` catalog, which ignores `LOCALE_PATHS`. `WorkOrderAdmin` therefore uses a plain multi-select for `collaborators`.
