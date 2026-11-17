"""
Translations for user-facing setup texts.

Translations live in standard gettext ``.po`` files under ``locales/<lang>.po``
(one per language), so they can be edited with any PO tool (Poedit,
SimpleLocalize, Weblate, ...). The files are parsed directly at runtime, which
avoids a ``msgfmt`` compile step in the PyInstaller builds.

The UC Core API expects setup texts as a dict containing *every* language, e.g.
``{"en": "Action", "de": "Aktion", "nl": "Actie"}``; the Remote picks the one
matching its UI language and falls back to ``en``. :func:`_a` builds that dict
from the English source string. Untranslated (or empty) entries are omitted so
the Remote falls back to English instead of showing an empty label.

To add a language: copy an existing ``.po`` file, translate the ``msgstr``
values and add the code to :data:`LANGUAGES`.
"""

import ast
import logging
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
# Languages offered in addition to the English source strings.
LANGUAGES = ["de", "nl"]


def _locale_dirs() -> list[Path]:
    """Candidate locale directories (source tree and PyInstaller bundle)."""
    dirs = [Path(__file__).resolve().parent / "locales"]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        dirs.append(Path(bundle) / "locales")
    return dirs


def _unquote(token: str) -> str:
    """Decode a PO string literal (C-style escapes, same as Python's)."""
    return ast.literal_eval(token)


def parse_po(text: str) -> dict[str, str]:
    """
    Parse PO file content into a ``msgid -> msgstr`` mapping.

    Supports the subset we use: comments, ``msgctxt``-free singular entries and
    multi-line string continuation. Fuzzy and empty translations are skipped,
    as is the header entry (empty msgid).
    """
    entries: dict[str, str] = {}
    msgid: str | None = None
    msgstr: str | None = None
    current: str | None = None
    fuzzy = False  # flag of the entry being parsed
    pending_fuzzy = False  # flag from comments preceding the next msgid

    def flush() -> None:
        if msgid and msgstr and not fuzzy:
            entries[msgid] = msgstr

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                pending_fuzzy = True
            continue
        if line.startswith("msgid "):
            flush()
            msgid, msgstr, current = _unquote(line[6:]), None, "msgid"
            fuzzy, pending_fuzzy = pending_fuzzy, False
            continue
        if line.startswith("msgstr "):
            msgstr, current = _unquote(line[7:]), "msgstr"
            continue
        if line.startswith('"'):
            if current == "msgid" and msgid is not None:
                msgid += _unquote(line)
            elif current == "msgstr" and msgstr is not None:
                msgstr += _unquote(line)
    flush()
    return entries


def _load(language: str) -> dict[str, str]:
    for directory in _locale_dirs():
        path = directory / f"{language}.po"
        if path.is_file():
            try:
                return parse_po(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, SyntaxError) as e:
                _LOG.error("Failed to load translations %s: %s", path, e)
                return {}
    _LOG.warning("No translation file for language '%s'", language)
    return {}


_CATALOGS: dict[str, dict[str, str]] = {}


def catalogs() -> dict[str, dict[str, str]]:
    """Return all loaded translation catalogs, loading them on first use."""
    if not _CATALOGS:
        _CATALOGS.update({lang: _load(lang) for lang in LANGUAGES})
    return _CATALOGS


def _a(message: str) -> dict[str, str]:
    """Return ``message`` in every available language for the UC Core API."""
    result = {DEFAULT_LANGUAGE: message}
    for lang, catalog in catalogs().items():
        translated = catalog.get(message)
        if translated:
            result[lang] = translated
    return result


def _am(*parts: str) -> dict[str, str]:
    """
    Like :func:`_a`, but concatenates separately translated parts.

    Lets long texts be translated paragraph by paragraph. A part without a
    translation falls back to English within an otherwise translated text.
    """
    translated = [_a(part) for part in parts]
    result = {DEFAULT_LANGUAGE: "".join(parts)}
    for lang in LANGUAGES:
        if any(lang in t for t in translated):
            result[lang] = "".join(t.get(lang, part) for t, part in zip(translated, parts))
    return result


__all__ = ["DEFAULT_LANGUAGE", "LANGUAGES", "_a", "_am", "catalogs", "parse_po"]
