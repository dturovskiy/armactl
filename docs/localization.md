# Localization

`armactl` uses English source strings in code and translates them through locale
JSON files in `src/armactl/locales/`.

## File format

Each locale file has this shape:

```json
{
  "__meta__": {
    "language": "Українська",
    "code": "uk"
  },
  "translations": {
    "Exit": "Вийти"
  }
}
```

Notes:

- `en.json` is intentionally almost empty. English is the source language.
- Missing keys fall back to the original English string.
- Locale filenames should match the language code, for example `uk.json`,
  `pl.json`, `de.json`.

## How to add a new locale

1. Copy `src/armactl/locales/uk.json` to a new file such as
   `src/armactl/locales/pl.json`.
2. Update `__meta__.language` and `__meta__.code`.
3. Translate values in `translations`.
4. Leave keys unchanged. Keys are the exact English source strings used in code.
5. Run `./scripts/run-host-tests` on the Linux host.

## How to add new translatable text in code

Use `_()` for static strings:

```python
from armactl.i18n import _

label = _("Exit")
```

Use `tr()` for formatted strings:

```python
from armactl.i18n import tr

message = tr("Exported {count} mods to {path}.", count=count, path=path)
```

Rules:

- Do not build user-facing strings by concatenation when a formatted template is
  clearer.
- Prefer named placeholders such as `{count}` and `{path}`.
- Keep the English source string readable; it becomes the translation key.
- Any text shown to users in TUI, notifications, validation errors, and backend
  messages surfaced in TUI should go through `_()` or `tr()`.

## Validation

The test suite includes locale coverage checks for the currently localized
modules. If a new key is added in code and missing in `uk.json`, tests should
fail and show the missing keys.
