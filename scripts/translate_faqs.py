#!/usr/bin/env python3
"""Translate FAQItem database content into localized fields.

Only empty translation fields are filled. Existing translations are never
overwritten unless --overwrite is passed explicitly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_ROOT = PROJECT_ROOT / "brainboost"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(DJANGO_ROOT))

from translate_po import (  # noqa: E402
    MockTranslator,
    TranslationError,
    build_translator,
    load_local_env,
    protect_placeholders,
    restore_placeholders,
    target_lang_for_locale,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brainboost.settings")

import django  # noqa: E402

django.setup()

from core.models import FAQItem  # noqa: E402


LOGGER = logging.getLogger("translate_faqs")
DEFAULT_LOCALES = ("en", "pl", "tr", "ru", "ar")


@dataclass(frozen=True)
class FAQJob:
    faq_id: int
    field_name: str
    target_field_name: str
    source_text: str
    protected_text: str
    protected_values: tuple[str, ...]


def build_jobs(locales: tuple[str, ...], overwrite: bool, landing_only: bool) -> list[FAQJob]:
    queryset = FAQItem.objects.all().order_by("id")
    if landing_only:
        queryset = queryset.filter(show_on_landing=True)

    jobs: list[FAQJob] = []
    for item in queryset:
        for locale in locales:
            for field_name in ("question", "answer"):
                source_text = getattr(item, field_name, "")
                target_field_name = f"{field_name}_{locale}"
                if not source_text:
                    continue
                if not hasattr(item, target_field_name):
                    LOGGER.warning("Skipping unsupported locale %s for FAQ #%s.", locale, item.pk)
                    continue
                if getattr(item, target_field_name) and not overwrite:
                    continue
                protected_text, protected_values = protect_placeholders(source_text)
                jobs.append(
                    FAQJob(
                        faq_id=item.pk,
                        field_name=field_name,
                        target_field_name=target_field_name,
                        source_text=source_text,
                        protected_text=protected_text,
                        protected_values=protected_values,
                    )
                )
    return jobs


def translate_jobs(jobs: list[FAQJob], locale: str, translator, batch_size: int) -> int:
    locale_jobs = [job for job in jobs if job.target_field_name.endswith(f"_{locale}")]
    if not locale_jobs:
        LOGGER.info("No FAQ translations pending for %s.", locale)
        return 0

    target_lang = target_lang_for_locale(locale, None)
    written = 0
    for offset in range(0, len(locale_jobs), batch_size):
        batch = locale_jobs[offset : offset + batch_size]
        translated_texts = translator.translate_many(
            [job.protected_text for job in batch],
            target_lang=target_lang,
        )
        if len(translated_texts) != len(batch):
            raise TranslationError(
                f"Provider returned {len(translated_texts)} translations for {len(batch)} FAQ texts."
            )

        for job, translated_text in zip(batch, translated_texts):
            restored_text = restore_placeholders(translated_text, job.protected_values)
            updated = FAQItem.objects.filter(pk=job.faq_id).update(
                **{job.target_field_name: restored_text}
            )
            if updated:
                written += 1
                LOGGER.info(
                    "FAQ #%s: filled %s from %s.",
                    job.faq_id,
                    job.target_field_name,
                    job.field_name,
                )
    LOGGER.info("Written FAQ translations for %s: %s", locale, written)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate FAQItem fields with DeepL.")
    parser.add_argument("--locales", nargs="+", default=list(DEFAULT_LOCALES))
    parser.add_argument("--provider", choices=("deepl", "mock"), default="deepl")
    parser.add_argument("--auth-key")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("DEEPL_API_URL", "https://api-free.deepl.com/v2/translate"),
    )
    parser.add_argument("--source-lang", default="DE")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--landing-only", action="store_true")
    parser.add_argument("--verbosity", choices=("INFO", "DEBUG"), default="INFO")
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.verbosity), format="%(levelname)s: %(message)s")

    locales = tuple(args.locales)
    try:
        jobs = build_jobs(locales=locales, overwrite=args.overwrite, landing_only=args.landing_only)
        LOGGER.info("Pending FAQ translations: %s", len(jobs))
        if args.dry_run:
            for locale in locales:
                count = len([job for job in jobs if job.target_field_name.endswith(f"_{locale}")])
                LOGGER.info("Pending FAQ translations for %s: %s", locale, count)
            return 0

        if not jobs:
            return 0

        translator = MockTranslator() if args.provider == "mock" else build_translator(args)
        total_written = 0
        for locale in locales:
            total_written += translate_jobs(jobs, locale, translator, args.batch_size)
    except Exception as exc:  # noqa: BLE001 - CLI should log provider/DB failures clearly.
        LOGGER.error("FAQ translation failed: %s", exc)
        return 1

    LOGGER.info("Total written FAQ translations: %s", total_written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
