#!/usr/bin/env python3
"""Fill empty gettext .po translations with DeepL.

The script is intentionally conservative:
- it only fills empty msgstr values by default,
- it skips the PO header and plural entries,
- it preserves Django/Python placeholders by shielding them from the provider,
- it does not require third-party Python packages.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import parse, request
from urllib.error import HTTPError


DEFAULT_LOCALE_ROOT = Path("brainboost/brainboost/locale")
DEFAULT_DOMAIN = "django"
DEFAULT_API_URL = "https://api-free.deepl.com/v2/translate"
PAID_API_URL = "https://api.deepl.com/v2/translate"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_OVERRIDES = {
    "DEEPL_AUTH_KEY",
    "DEEPL_API_URL",
}
DEEPL_TARGETS = {
    "en": "EN-US",
    "pl": "PL",
    "tr": "TR",
    "ru": "RU",
    "ar": "AR",
}

KEYWORD_RE = re.compile(r"^(msgid|msgid_plural|msgstr)\s+(.*)$")
PLACEHOLDER_RE = re.compile(
    r"({[{%#].*?[}%]})"
    r"|(%\([^)]+\)[#0 +\-]?\d*(?:\.\d+)?[diouxXeEfFgGcrs])"
    r"|(%[#0 +\-]?\d*(?:\.\d+)?[diouxXeEfFgGcrs])"
    r"|(\{[A-Za-z_][A-Za-z0-9_]*\})"
    r"|(&[A-Za-z0-9#]+;)"
)
PROTECTED_TAG_RE = re.compile(
    r"<x\s+id=[\"'](?P<id>\d+)[\"']\s*/>|<x\s+id=[\"'](?P<id2>\d+)[\"']\s*>\s*</x>"
)
VALID_PO_LINE_RE = re.compile(
    r"^(#|\"|msgctxt\s|msgid\s|msgid_plural\s|msgstr\s|msgstr\[\d+\]\s)"
)


@dataclass(frozen=True)
class PoEntry:
    block_start: int
    block_end: int
    msgid: str
    msgstr: str
    msgstr_start: int
    msgstr_end: int
    fuzzy: bool
    obsolete: bool
    plural: bool


@dataclass(frozen=True)
class TranslationJob:
    entry: PoEntry
    original_text: str
    protected_text: str
    protected_values: tuple[str, ...]


class TranslationError(RuntimeError):
    pass


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (key in ENV_OVERRIDES or not os.environ.get(key)):
            os.environ[key] = value


def load_local_env() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    load_env_file(PROJECT_ROOT / "brainboost" / ".env")


def po_unquote(value: str) -> str:
    return ast.literal_eval(value)


def parse_po_value(lines: list[str], start: int, keyword: str) -> tuple[str, int]:
    match = KEYWORD_RE.match(lines[start])
    if not match or match.group(1) != keyword:
        raise ValueError(f"Expected {keyword} at line {start + 1}")

    values = [po_unquote(match.group(2).strip())]
    index = start + 1
    while index < len(lines) and lines[index].startswith('"'):
        values.append(po_unquote(lines[index].strip()))
        index += 1
    return "".join(values), index


def parse_entry(lines: list[str], block_start: int, block_end: int) -> PoEntry | None:
    block = lines[block_start:block_end]
    obsolete = any(line.startswith("#~") for line in block)
    fuzzy = any(line.startswith("#,") and "fuzzy" in line for line in block)
    plural = any(line.startswith("msgid_plural") or line.startswith("msgstr[") for line in block)

    msgid_line = None
    msgstr_line = None
    for offset, line in enumerate(block):
        absolute = block_start + offset
        if line.startswith("msgid ") and msgid_line is None:
            msgid_line = absolute
        elif line.startswith("msgstr ") and msgstr_line is None:
            msgstr_line = absolute

    if msgid_line is None or msgstr_line is None:
        return None

    msgid, _ = parse_po_value(lines, msgid_line, "msgid")
    msgstr, msgstr_end = parse_po_value(lines, msgstr_line, "msgstr")
    return PoEntry(
        block_start=block_start,
        block_end=block_end,
        msgid=msgid,
        msgstr=msgstr,
        msgstr_start=msgstr_line,
        msgstr_end=msgstr_end,
        fuzzy=fuzzy,
        obsolete=obsolete,
        plural=plural,
    )


def parse_po_entries(lines: list[str]) -> list[PoEntry]:
    entries: list[PoEntry] = []
    block_start = 0
    for index, line in enumerate(lines + [""]):
        if line != "":
            continue
        if block_start < index:
            entry = parse_entry(lines, block_start, index)
            if entry is not None:
                entries.append(entry)
        block_start = index + 1
    return entries


def validate_po_syntax(lines: list[str], path: Path) -> None:
    invalid_lines = [
        (index, line)
        for index, line in enumerate(lines, start=1)
        if line and not VALID_PO_LINE_RE.match(line)
    ]
    if not invalid_lines:
        return

    preview = "\n".join(
        f"{path}:{line_number}: invalid PO line: {line}"
        for line_number, line in invalid_lines[:5]
    )
    more = "" if len(invalid_lines) <= 5 else f"\n... and {len(invalid_lines) - 5} more"
    raise SystemExit(preview + more)


def should_translate(entry: PoEntry, overwrite: bool, include_fuzzy: bool) -> bool:
    if entry.msgid == "":
        return False
    if entry.obsolete or entry.plural:
        return False
    if entry.fuzzy and not include_fuzzy:
        return False
    if overwrite:
        return True
    return entry.msgstr == ""


def protect_placeholders(text: str) -> tuple[str, tuple[str, ...]]:
    values: list[str] = []

    def replace(match: re.Match[str]) -> str:
        values.append(match.group(0))
        return f'<x id="{len(values) - 1}"></x>'

    return PLACEHOLDER_RE.sub(replace, text), tuple(values)


def restore_placeholders(text: str, values: tuple[str, ...]) -> str:
    seen: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        raw_id = match.group("id") or match.group("id2")
        index = int(raw_id)
        if index >= len(values):
            raise TranslationError(f"Provider returned unknown placeholder id {index}.")
        seen.add(index)
        return values[index]

    restored = PROTECTED_TAG_RE.sub(replace, text)
    missing = set(range(len(values))) - seen
    if missing:
        raise TranslationError(
            "Provider dropped protected placeholder(s): "
            + ", ".join(str(index) for index in sorted(missing))
        )
    return restored


def po_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_msgstr(value: str) -> list[str]:
    return [f"msgstr {po_quote(value)}"]


def infer_locale_from_path(path: Path, locale_root: Path) -> str | None:
    try:
        relative = path.resolve().relative_to(locale_root.resolve())
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def po_files_from_locales(locale_root: Path, domain: str, locales: Iterable[str]) -> list[Path]:
    return [
        locale_root / locale / "LC_MESSAGES" / f"{domain}.po"
        for locale in locales
    ]


class BaseTranslator:
    def translate_many(self, texts: list[str], target_lang: str) -> list[str]:
        raise NotImplementedError


class MockTranslator(BaseTranslator):
    def translate_many(self, texts: list[str], target_lang: str) -> list[str]:
        return [f"[{target_lang}] {text}" for text in texts]


class DeepLTranslator(BaseTranslator):
    def __init__(
        self,
        auth_key: str,
        api_url: str,
        source_lang: str,
        timeout: float,
        retries: int,
    ) -> None:
        self.auth_key = auth_key
        self.api_url = api_url
        self.source_lang = source_lang
        self.timeout = timeout
        self.retries = retries

    def translate_many(self, texts: list[str], target_lang: str) -> list[str]:
        payload = {
            "source_lang": self.source_lang,
            "target_lang": target_lang,
            "tag_handling": "html",
            "ignore_tags": "x",
            "preserve_formatting": "1",
            "text": texts,
        }
        body = parse.urlencode(payload, doseq=True).encode("utf-8")
        http_request = request.Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"DeepL-Auth-Key {self.auth_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with request.urlopen(http_request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                translations = data.get("translations", [])
                if len(translations) != len(texts):
                    raise TranslationError(
                        f"DeepL returned {len(translations)} translations for {len(texts)} texts."
                    )
                return [item["text"] for item in translations]
            except Exception as exc:  # noqa: BLE001 - CLI should report provider failures clearly.
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        hint = ""
        if isinstance(last_error, HTTPError) and last_error.code == 403:
            hint = (
                " DeepL returned 403 Forbidden. Check that DEEPL_AUTH_KEY is correct "
                "and that DEEPL_API_URL matches your plan. API Free usually uses "
                f"{DEFAULT_API_URL}; Developer/API Pro may use {PAID_API_URL}."
            )
        raise TranslationError(f"DeepL request failed: {last_error}.{hint}")


def build_translator(args: argparse.Namespace) -> BaseTranslator | None:
    if args.dry_run:
        return None
    if args.provider == "mock":
        return MockTranslator()

    auth_key = args.auth_key or os.environ.get("DEEPL_AUTH_KEY", "")
    if not auth_key:
        raise SystemExit(
            "DEEPL_AUTH_KEY is not set. Export your DeepL API Free key or pass --auth-key."
        )
    if auth_key.endswith(":fx") and args.api_url == PAID_API_URL:
        raise SystemExit(
            "DEEPL_AUTH_KEY looks like an API Free key because it ends with ':fx'. "
            f"Use DEEPL_API_URL={DEFAULT_API_URL}."
        )
    if not auth_key.endswith(":fx") and args.api_url == DEFAULT_API_URL:
        raise SystemExit(
            "DEEPL_AUTH_KEY does not look like an API Free key. "
            f"Use DEEPL_API_URL={PAID_API_URL} for Developer/API Pro keys."
        )
    return DeepLTranslator(
        auth_key=auth_key,
        api_url=args.api_url,
        source_lang=args.source_lang,
        timeout=args.timeout,
        retries=args.retries,
    )


def target_lang_for_locale(locale: str, explicit_target: str | None) -> str:
    if explicit_target:
        return explicit_target
    return DEEPL_TARGETS.get(locale, locale.upper())


def process_po_file(
    path: Path,
    locale: str,
    target_lang: str,
    translator: BaseTranslator | None,
    args: argparse.Namespace,
) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    validate_po_syntax(lines, path)
    entries = parse_po_entries(lines)
    jobs: list[TranslationJob] = []

    for entry in entries:
        if not should_translate(entry, overwrite=args.overwrite, include_fuzzy=args.include_fuzzy):
            continue
        protected_text, protected_values = protect_placeholders(entry.msgid)
        jobs.append(
            TranslationJob(
                entry=entry,
                original_text=entry.msgid,
                protected_text=protected_text,
                protected_values=protected_values,
            )
        )
        if args.max_entries and len(jobs) >= args.max_entries:
            break

    if args.dry_run:
        print(f"{path}: {len(jobs)} translation(s) pending for {locale} ({target_lang}).")
        return len(jobs), 0

    if not jobs:
        print(f"{path}: nothing to translate.")
        return 0, 0

    if translator is None:
        raise AssertionError("translator must be set unless dry-run is active")

    translations: dict[int, str] = {}
    for offset in range(0, len(jobs), args.batch_size):
        batch = jobs[offset : offset + args.batch_size]
        translated = translator.translate_many(
            [job.protected_text for job in batch],
            target_lang=target_lang,
        )
        if len(translated) != len(batch):
            raise TranslationError(
                f"Provider returned {len(translated)} translations for {len(batch)} texts."
            )
        for job, translated_text in zip(batch, translated):
            translations[job.entry.msgstr_start] = restore_placeholders(
                translated_text,
                job.protected_values,
            )

    updated_lines = list(lines)
    for entry in sorted((job.entry for job in jobs), key=lambda item: item.msgstr_start, reverse=True):
        value = translations[entry.msgstr_start]
        updated_lines[entry.msgstr_start:entry.msgstr_end] = render_msgstr(value)

    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    print(f"{path}: translated {len(jobs)} entr{'y' if len(jobs) == 1 else 'ies'}.")
    return len(jobs), len(jobs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate empty gettext .po entries with DeepL API Free."
    )
    parser.add_argument("po_files", nargs="*", type=Path, help="Optional .po files to update.")
    parser.add_argument("--locale-root", type=Path, default=DEFAULT_LOCALE_ROOT)
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--locales", nargs="+", default=["en", "pl"])
    parser.add_argument("--target-locale", help="Locale to use for positional .po files.")
    parser.add_argument("--target-lang", help="Explicit provider target language, e.g. EN-US or PL.")
    parser.add_argument("--source-lang", default="DE")
    parser.add_argument("--provider", choices=("deepl", "mock"), default="deepl")
    parser.add_argument("--auth-key", help="DeepL auth key. Prefer DEEPL_AUTH_KEY instead.")
    parser.add_argument("--api-url", default=os.environ.get("DEEPL_API_URL", DEFAULT_API_URL))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-entries", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="Report pending entries without API calls.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing msgstr values.")
    parser.add_argument("--include-fuzzy", action="store_true", help="Translate fuzzy entries too.")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1.")

    po_files = args.po_files or po_files_from_locales(args.locale_root, args.domain, args.locales)
    translator = build_translator(args)
    total_pending = 0
    total_written = 0

    for path in po_files:
        if not path.exists():
            print(f"{path}: file not found.", file=sys.stderr)
            return 1
        locale = args.target_locale or infer_locale_from_path(path, args.locale_root)
        if not locale:
            print(
                f"{path}: cannot infer locale. Pass --target-locale or use --locale-root.",
                file=sys.stderr,
            )
            return 1
        target_lang = target_lang_for_locale(locale, args.target_lang)
        pending, written = process_po_file(path, locale, target_lang, translator, args)
        total_pending += pending
        total_written += written

    if args.dry_run:
        print(f"Pending translations: {total_pending}")
    else:
        print(f"Written translations: {total_written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
