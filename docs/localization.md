# Localization

The app is used by Czech depot workers and its interface is entirely in Czech.
This is achieved two ways at once, and the split matters.

| Layer | Mechanism |
|---|---|
| **App copy** — labels, buttons, messages, choice labels, export headers | **Hardcoded Czech strings** in the source |
| **Framework strings** — admin chrome, `contrib.auth`, validation errors | `LANGUAGE_CODE = 'cs'`, using the `.mo` catalogs Django ships |

The two are complementary, not alternatives. `LANGUAGE_CODE` is what turns
`This field is required.` into `Toto pole je vyžadováno.` without anyone writing
it; hardcoding covers everything the app says in its own voice.

> **Keep new user-facing text hardcoded.** Do not introduce `{% trans %}` or
> `gettext` for app copy. A half-migrated i18n setup is worse than either
> approach on its own: some strings move to `.po` files, most don't, and nobody
> can tell which without checking. If full i18n is ever wanted, that is a
> deliberate project, not something to start incidentally.

`LocaleMiddleware` is **deliberately not installed**. The language is fixed, not
negotiated from `Accept-Language`, so a worker with an English phone still gets
a Czech app.

---

The Czech locale has real consequences for numbers, dates, and forms. Read this
section before touching any of them.

## Numbers render with a comma

`{{ movement.quantity }}` renders **`12,50`**, not `12.50`.

So tests asserting on rendered quantities must use the comma:

```python
self.assertContains(response, '12,50')  # correct
self.assertContains(response, '12.50')  # will fail
```

**Form inputs are not localized.** Django form fields default to
`localize=False`, so `NumberInput` still renders and accepts `value="12.50"`
with a dot, and a comma typed into a `DecimalField` is rejected with
`Zadejte číslo.`

Leave it that way. Setting `localize=True` would make the field accept commas,
but it also downgrades the widget from `<input type="number">` to a plain text
input — costing every mobile user their numeric keypad, on an app used almost
entirely from phones. The current asymmetry is the better trade.

## Dates still round-trip

Czech `DATE_INPUT_FORMATS` is `%d.%m.%Y`-first and does not list ISO. Django
appends `%Y-%m-%d` to every locale's list anyway, so the `<input type="date">`
widgets on the history and time-worked filters parse normally. Bound forms
re-render the raw submitted string, so filters survive pagination.

The one trap: an **unbound** date field with a Python `date` as `initial`
renders as `14.08.2026`, which `<input type="date">` rejects as invalid and
displays blank. No form does this today. If you add one, pass the initial value
as an ISO string.

## Keep explicit date formats in templates

Timestamps use `|date:"Y-m-d H:i"`, which is locale-independent. A bare
`{{ movement.created_at }}` would render `14. srpna 2026` instead.

## Every `ModelChoiceField` needs an `empty_label`

Django 6 defaults it to `- Select an option -`, which is **not a translatable
string**. It renders in English even under `cs` and cannot be fixed by the
locale — only by setting it explicitly.

The wording depends on what a blank choice *means*:

| Form type | Blank means | Wording |
|---|---|---|
| Entry (receive, ship, adjust, transform) | "you must choose" | `Vyberte materiál`, `Vyberte lokalitu`, `Vyberte stroj` |
| Filter (history, hours, machine usage) | "don't filter by this" | `Všechny materiály`, `Všechny lokality`, `Všichni uživatelé`, `Všechny stroje`, `Všichni pracovníci` |

Filter wording matches the `Všechny typy` choice that
`HistoryFilterForm.__init__` sets for `movement_type`.

`EmptyLabelTests`, in both `inventory/tests.py` and `workorders/tests.py`, fails
if a new field forgets. `ModelMultipleChoiceField` — `collaborators`, for
example — has no blank option and needs nothing.

## Time zones

`USE_TZ` stays on, so **the database stores UTC**. `TIME_ZONE` affects only
rendering and the day boundaries that `__date` lookups use for the history date
filters.

Templates localize automatically. **Python code does not.** Anything formatting
a datetime outside a template must call `timezone.localtime()` explicitly — the
CSV export does, so its timestamps match the ones on screen.

## The CSV export

`inventory/views.py::movement_history_export` targets **Czech Excel, not
RFC 4180**. Four deliberate deviations:

| Decision | Reason |
|---|---|
| Semicolon separator | The Windows list separator under a Czech locale. Commas would put every row in one cell. |
| Comma decimals (`-12,500`) | Otherwise Excel reads quantities as text. |
| `dd.mm.yyyy hh:mm:ss` dates | Czech order, so Excel parses them as dates rather than leaving them as text. |
| UTF-8 **BOM** | Without it Excel assumes windows-1250 and mangles every diacritic in the material names. |

`ExportExcelCompatibilityTests` asserts all four against the raw response bytes.

**Anything machine-reading this file needs `delimiter=';'` and
`decode('utf-8-sig')`.** It is not a general-purpose interchange format; it is
an Excel file that happens to be CSV.

### The two escaping helpers

Text fields go through `_csv_safe`, which prefixes an apostrophe to anything
starting with `=`, `+`, `-`, `@`, tab, or carriage return. Excel and LibreOffice
execute such cells as formulas, so a note reading `=cmd|...` would otherwise run
on whoever opens the export.

Quantities go through `_csv_number` instead, which only swaps the decimal
separator. This is deliberate: **every shipment quantity is negative**, and
`_csv_safe` would prefix an apostrophe to all of them, turning the entire
quantity column into text and breaking every sum in the spreadsheet.

So the split is not an oversight. Quantities are `Decimal`s from the database
and cannot carry an injection payload; free text can. Route new columns through
`_csv_safe` unless they are numbers straight out of the ORM.

## Not localized

Model field `verbose_name`s are still auto-derived English, so the Django admin
shows Czech chrome over English field labels — `Movement type`, `Created at`.

This is acceptable because the admin is a back office for `ADMIN`-role users
only; depot workers never see it. Adding `verbose_name='Typ pohybu'` and so on
would be a straightforward improvement if that ever changes.
