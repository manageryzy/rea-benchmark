# Report languages

The generated benchmark reports support English (`en`), Simplified Chinese
(`zh-CN`), and Japanese (`ja`). Use the language selector in the header. The
selection is kept in `?lang=...`, carried into internal links, and saved locally
when browser storage is available. An explicit URL selection takes precedence
over the saved preference and browser language. Opening the report through
`file://` needs no web server or translation service.

## Translation files

Edit these independent UTF-8 JSON catalogues:

- `benchmarks/scripts/report_assets/locales/en.json`
- `benchmarks/scripts/report_assets/locales/zh-CN.json`
- `benchmarks/scripts/report_assets/locales/ja.json`

Keys are source-message templates, like gettext message IDs. English display
copy is also in its catalogue; a few existing Chinese source IDs have English
values. Values must be plain text, never HTML. Keep numbered placeholders such
as `{0}` and `{1}` intact; their order can change in translation. Numbers,
identifiers, model names, hashes, source code and other evidence are not
translated. `locale.name` supplies each language's own name in the selector.

The report-only localization module compiles these catalogues into independent
`assets/locales/*.js` files. This supports offline file URLs without `fetch`,
while keeping raw benchmark JSON out of the public site. The generated bundles
are build output; edit the source JSON files instead. Both private and public
report templates load the same localization assets. UI and accessibility text,
SVG labels, filter messages, radar tooltips and print labels use the same
catalogues. Search and filters keep their canonical data values across language
changes. `code`, `pre`, `translate="no"` and `data-no-i18n` contents stay verbatim.

Report templates retain readable fallback text when scripting is disabled.
Dynamic UI messages should use a complete catalogue template where possible.
The localization layer normalizes variable numbers and hashes and composes
known labels in generated evidence descriptions. New prose without an entry is
reported instead of silently treated as translated.

## Missing translations

A missing message or locale, or a mismatched placeholder set, falls back to the
English text. The browser shows a warning panel and logs the locale, message ID
and reason once per warning. Unknown source messages retain their original text
and are flagged. A missing English file cannot provide a translation and is
explicitly reported. Translation text is inserted as text, not HTML.

Normal report generation warns about missing source messages and identifies a
page containing each one. Catalogue validation warns about missing translations
and invalid placeholders. Duplicate catalogue keys are rejected. A strict
focused check is available:

```sh
/usr/bin/python3 benchmarks/scripts/check_i18n_report.py --site benchmarks/site
node benchmarks/scripts/check_i18n_browser.cjs --site benchmarks/site
```

The browser control uses the locally installed Playwright/Chromium; set
`PLAYWRIGHT_MODULE` or `CHROMIUM_EXECUTABLE` for another installation. These
checks do not run benchmark cases, validation suites or model APIs. Use the
project Python (`/usr/bin/python3` by default), matching normal report generation.

After editing translations, regenerate the site and `docs/` from the saved
public summary:

```sh
make pages
```

This does not refresh model results or call models. The saved report's data date
remains visible. Translation files are declared dependencies of the report and
public-report cache actions only, so editing them invalidates rendered reports,
not inference or grading results. Coordinate with an already running report
finalizer rather than starting two builders for the same output directory.
