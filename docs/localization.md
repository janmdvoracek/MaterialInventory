# Localization

The app is used by Czech depot workers and its interface is entirely in Czech.
This is achieved two ways at once, and the split matters.

| Layer | Mechanism |
|---|---|
| **App copy** — labels, buttons, messages, choice labels | **Hardcoded Czech strings** in the source |
| **Framework strings** — admin chrome, `contrib.auth`, validation errors | `LANGUAGE_CODE = 'cs'`, using the `.mo` catalogs Django ships |
| **Framework strings Django never translated** | `LOCALE_PATHS` → `locale/cs/LC_MESSAGES/django.po`, holding Django msgids only |

The three are complementary, not alternatives. `LANGUAGE_CODE` is what turns
`This field is required.` into `Toto pole je vyžadováno.` without anyone writing
it; hardcoding covers everything the app says in its own voice; the local
catalog patches the dozen strings Django added faster than its Czech
translators kept up with. See [the admin](#the-admin) below.

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

`{{ row.hours }}` renders **`12,50`**, not `12.50`.

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

**Anything rendered outside a template has to localize itself.** Templates do
it for free; `str(value)` and f-strings do not. Two places in the app:

- The mass-balance error message (`_balance_error`) puts its totals through
  `django.utils.formats.localize`, or it would report `9.5` in an app that
  shows `9,5` everywhere else.
- The summary CSV exports call `number_format(value, decimal_pos=N)` per cell.
  That comma is also why the files are **`;`-delimited**: Excel under `cs`
  splits a `.csv` on the locale's list separator, which is the semicolon, and a
  comma decimal and a comma delimiter cannot share a file. The same files carry
  a UTF-8 BOM, without which Excel opens „Štěrk" as mojibake. See
  [architecture.md](architecture.md).

## Dates still round-trip

Czech `DATE_INPUT_FORMATS` is `%d.%m.%Y`-first and does not list ISO. Django
appends `%Y-%m-%d` to every locale's list anyway, so the `<input type="date">`
widgets on the filter forms parse normally. Bound forms re-render the raw
submitted string, so filters survive pagination.

The one trap: an **unbound** date field with a Python `date` as `initial`
renders as `14.08.2026`, which `<input type="date">` rejects as invalid and
displays blank — the pre-fill is lost with no error anywhere.

One form does exactly this. `WorkOrderForm.performed_on` on the Transform form
is pre-filled with today, and on `job_edit` with the job's own date, so its
widget carries an explicit format:

```python
performed_on = forms.DateField(
    initial=timezone.localdate,
    widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
)
```

Any new pre-filled date field needs the same, and two tests in
`PerformedOnTests` assert the rendered `value=` to catch it going missing.

## The clock in native pickers is the device's, not ours

Everything the app renders is 24-hour: templates use an explicit `H:i` and Czech
`TIME_FORMAT` is `G:i`.

The app no longer renders an `<input type="datetime-local">` anywhere — it
existed only for the back-dating checkbox on Příjem and Výdej, and went with
them. It is recorded here because the caveat returns with the widget: browsers
draw that control themselves and format it from the **browser or OS language**,
so a Czech-configured phone shows 24h while an English-configured one shows
AM/PM, and `lang="cs"` does not override it. The alternative — a text field
taking `20.08.2026 07:30`, which Czech `DATETIME_INPUT_FORMATS` already parses —
guarantees 24-hour but costs the tap-to-pick calendar on phones. That trade was
settled in favour of the native control, the same way as `localize=False` on
quantities above.

## Keep explicit date formats in templates

Timestamps use `|date:"Y-m-d H:i"`, which is locale-independent. A bare
`{{ usage.created_at }}` would render `14. srpna 2026` instead.

## Every `ModelChoiceField` needs an `empty_label`

Django 6 defaults it to `- Select an option -`, which is **not a translatable
string**. It renders in English even under `cs` and cannot be fixed by the
locale — only by setting it explicitly.

The wording depends on what a blank choice *means*:

| Form type | Blank means | Wording |
|---|---|---|
| Transform formset rows | it *is* the label | `Materiál`, `Stroj`, `Pracovník` |
| Filter (hours, machine usage) | "don't filter by this" | `Všechny stroje`, `Všichni pracovníci`, `Všichni uživatelé` |

The transform rows use the bare noun rather than a `Vyberte ...` prompt because
each row renders its selects side by side with no room for a visible label, so
the empty option is doing the labelling. The `hours` input on those rows carries
a placeholder for the same reason.

`EmptyLabelTests` in `workorders/tests.py` fails if a new field forgets. `ModelMultipleChoiceField` — `collaborators`, for
example — has no blank option and needs nothing.

## Time zones

`USE_TZ` stays on, so **the database stores UTC**. `TIME_ZONE` affects only
rendering. No filter depends on it: the date filters compare
`WorkOrder.performed_on`, a `DateField` with no time and no zone, so a job
recorded at 00:30 local cannot land on the previous UTC day the way a
`created_at__date` lookup could.

Templates localize automatically. **Python code does not.** Anything formatting
a datetime outside a template must call `timezone.localtime()` explicitly.
Nothing does today: the summary CSV exports format decimals and put one
`timezone.localdate()` in a filename, and a date carries no zone to get wrong.

## The admin

The Django admin is Czech as well, and it takes three mechanisms to get there.
A new model or ModelAdmin needs all three, or it will show English in one spot.

**1. App labels.** Each `AppConfig` carries a `verbose_name` — `Materiály`,
`Stroje`, `Zakázky`, `Uživatelé`. Without it the admin index groups models under the
Python package name. (Django's own `Autentizace a autorizace`
section is gone as well — `auth.Group` is unregistered in `accounts/admin.py`,
since this app never consults permissions.)

**2. Model and field names.** Every `Meta` sets `verbose_name` and
`verbose_name_plural`, and every field sets a lowercase `verbose_name` (Django
capitalizes it where it needs to). Note that this is a **migration**:
`makemigrations` emits an `AlterField` for a label-only change, and CI's
`makemigrations --check` step fails if it is not committed.

**3. `LOCALE_PATHS`.** Some strings cannot be reached by either of the above,
because they are Django's own and the `cs` catalogs Django ships have no entry
for them — the msgid falls straight back to English no matter what
`LANGUAGE_CODE` says. `locale/cs/LC_MESSAGES/django.po` supplies them:

| String | Where it shows |
|---|---|
| `- Select an option -` | the blank option in every admin `<select>` |
| `Filter by %(field_name)s` | the `date_hierarchy` bar on `WorkOrderAdmin`, the only admin that has one |
| `Are you sure you want to delete the %(object_name)s …` | delete confirmation |
| `After you’ve created a user, …` | the user add form |
| `Search %(name)s`, `Pagination %(name)s` | screen-reader-only headings |

This is the one place the project uses the i18n framework. It stays narrow on
purpose: **Django msgids only.** App copy is still hardcoded, and putting an
app string in here would restart exactly the half-migrated split the rule above
exists to prevent.

> **The runtime reads the compiled `.mo`, not the `.po`.** Both are committed.
> Editing the `.po` without recompiling changes nothing, silently:
>
> ```bash
> python manage.py compilemessages -l cs --ignore=.venv
> ```
>
> Keep the `--ignore`: `compilemessages` walks the tree from the project root
> and will otherwise recompile every catalog inside the virtualenv too.

`AdminCzechTests` in `accounts/tests.py` covers all three layers, and fails if
the `.mo` is missing or stale — so a forgotten `compilemessages` is caught by
CI rather than by a manager seeing English.

### What LOCALE_PATHS cannot fix

`filter_horizontal` builds its labels in JavaScript from the `djangojs` catalog,
and the admin's `jsi18n` view loads only `django.contrib.admin`'s own locale
directories — it ignores `LOCALE_PATHS` entirely. Three of those strings
("Choose %s by selecting them and then select the "Choose" arrow button.") have
no Czech translation, so the widget renders half-English.

`WorkOrderAdmin` therefore uses the plain multi-select for `collaborators`,
whose help text *is* translated. Don't reintroduce `filter_horizontal` without
re-checking that.
