"""Explicit product copy for reviewed journey wiring; never translate user data.

The four non-English catalogs are draft translations pending native-language
and consequence-copy review. Arabic remains the community seed, unchanged.
"""
from __future__ import annotations

import hashlib
import re
from string import Formatter

from markupsafe import Markup, escape

from .i18n_journey_common import ROWS as COMMON_ROWS
from .i18n_journey_employee import ROWS as EMPLOYEE_ROWS
from .i18n_journey_review import ROWS as REVIEW_ROWS
from .i18n_journey_workflow import ROWS as WORKFLOW_ROWS

ROWS = [*COMMON_ROWS, *EMPLOYEE_ROWS, *REVIEW_ROWS, *WORKFLOW_ROWS]

TRANSLATION_LANGS = ("fr", "de", "ja", "zh")
# Rows are source, French, German, Japanese, Chinese. No Arabic machine completion.


def key(source):
    return "journey." + hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def messages():
    result = {}
    for row in ROWS:
        source, *translations = row
        if len(translations) != len(TRANSLATION_LANGS):
            raise ValueError("Journey translations require all four draft languages")
        expected = {name for _, name, _, _ in Formatter().parse(source) if name}
        for text in translations:
            if (not text.strip() or {name for _, name, _, _ in Formatter().parse(text) if name} != expected
                    or set(re.findall(r"\{\{[a-z_]+\}\}", text)) != set(re.findall(r"\{\{[a-z_]+\}\}", source))):
                raise ValueError("Journey catalog placeholders differ")
        result[key(source)] = {"en": source, **dict(zip(TRANSLATION_LANGS, translations, strict=True))}
    return result


def entry(source, lang):
    from .i18n import MESSAGES
    row = MESSAGES.get(key(source), {"en": source})
    actual = lang if row.get(lang) else "en"
    return row[actual], actual


def text(source, lang="en", **values):
    value, _ = entry(source, lang)
    return value.format_map(values) if values else value


def region(source, lang="en", **values):
    """A product-copy region, with caller-supplied content escaped and isolated."""
    value, actual = entry(source, lang)
    safe = escape(value)
    if values:
        safe = safe.format_map({name: Markup('<bdi lang="und" dir="auto">{}</bdi>').format(escape(str(value))) for name, value in values.items()})
    return Markup('<span lang="{}" dir="{}" data-product-copy>{}</span>').format(actual, "rtl" if actual == "ar" else "ltr", safe)


def accepted_locales(request):
    """Honor browser quality values; malformed and explicitly rejected tags lose."""
    choices = []
    for position, part in enumerate(request.headers.get("Accept-Language", "").split(",")):
        tag, *options = part.strip().split(";")
        if not re.fullmatch(r"[A-Za-z]{2}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?", tag):
            continue
        try:
            quality = float(next((option.strip()[2:] for option in options if option.strip().startswith("q=")), "1"))
        except ValueError:
            continue
        if 0 < quality <= 1:
            choices.append((-quality, position, tag))
    return [tag for _, _, tag in sorted(choices)]


def locale_for(request, lang):
    """Retain a valid regional formatting preference without inventing translation."""
    from .auth import caller_principal
    candidates = ([] if caller_principal(request) else [request.query_params.get("lang", "")]) + accepted_locales(request)
    for candidate in candidates:
        if re.fullmatch(r"[A-Za-z]{2}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?", candidate or "") and candidate.lower().split("-")[0] == lang:
            return candidate
    return lang


def context(request, lang):
    from .i18n import MESSAGES
    catalog = {}
    for source, *_ in ROWS:
        value, actual = entry(source, lang)
        catalog[source] = {"text": value, "lang": actual}
    # Existing community-reviewed chrome remains available to explicitly wired
    # controls, but missing Arabic journey copy always retains English regions.
    for row in MESSAGES.values():
        if row.get("en") not in catalog:
            actual = lang if row.get(lang) else "en"
            catalog[row["en"]] = {"text": row.get(actual, row["en"]), "lang": actual}
    return {"ui": lambda source, **values: region(source, lang, **values),
            "ut": lambda source, **values: text(source, lang, **values),
            "ul": lambda source: entry(source, lang)[1],
            "journey_i18n": {"language": lang, "locale": locale_for(request, lang), "catalog": catalog,
                              "review_status": "source" if lang == "en" else "community_seed" if lang == "ar" else "draft_partial"}}
