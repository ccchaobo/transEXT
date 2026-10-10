"""Evidence run for translation orchestration, on a real parsed document.

Two things are demonstrated and one is deliberately not:

* the plan is built from a real parsed document: which blocks would be sent, which are
  skipped because their type is not translatable, and the glossary version;
* an unconfigured run is **refused before any spend**, naming the error code, and produces no
  placeholder text;
* translation quality is **not** demonstrated, and this script cannot demonstrate it.  There
  is no credential in this environment, so the real-service acceptance item stays PENDING
  (limitations.md 2).  No mock provider is substituted, because a mock would make an
  unverified pipeline look finished.

If ``RR_TRANSLATION_API_KEY`` is set the script will go on to make a real call.  It says so
before doing it, and it never sends more units than ``--limit``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from rr_adapters.translation import (
    DEFAULT_OLLAMA_CLOUD_BASE_URL,
    config_from_env,
    describe_configuration,
    load_env_file,
    provider_from_env,
)
from rr_services import DocumentService, ParseService, Settings
from rr_services.translation import (
    TranslationBudgetExceeded,
    TranslationNotConfigured,
    TranslationService,
    error_code_for,
)
from rr_storage import JobQueue, Workspace, create_database

CORPUS = Path("benchmarks/corpus")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", default="pdf_b_two_column")
    parser.add_argument("--limit", type=int, default=3, help="units to send if a key exists")
    args = parser.parse_args()

    # A git-ignored .env is the documented way to configure this; the ambient environment wins.
    load_env_file(".env")

    # On Windows the SQLite handle keeps the database file open until the engine is disposed,
    # so cleanup is best-effort.  Left-behind temp files are preferable to a traceback that
    # hides the evidence this script exists to print.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Settings(data_dir=Path(tmp) / "data")
        database = create_database(settings.data_dir)
        database.create_all()
        workspace = Workspace(settings.workspace_root)
        queue = JobQueue(lease_seconds=120)

        source = CORPUS / "files" / f"{args.document}.pdf"
        if not source.exists():
            print(f"missing corpus file: {source}")
            return 2

        with database.session() as session:
            with source.open("rb") as stream:
                imported = DocumentService(settings, workspace, queue).import_file(
                    session, filename=source.name, stream=stream
                )
            lease = queue.claim_next(session)
            ParseService(settings, workspace, queue).run(session, lease, imported.document.id)
            queue.finish(session, lease, status="succeeded")
            document_id = imported.document.id
            print(f"parsed {source.name} -> {document_id}")

            # Build the plan with the provider that would really be used, without calling it.
            provider = provider_from_env()
            service = TranslationService(settings, workspace, queue, provider=provider)
            plan = service.plan(session, document_id)

            print()
            print("PLAN")
            print(f"  revision            : {plan.revision_id}")
            print(f"  provider            : {plan.provider}")
            print(f"  model               : {plan.model}")
            print(f"  prompt version      : {plan.prompt_version}")
            print(f"  glossary version    : {plan.glossary_version}")
            print(f"  translatable blocks : {len(plan.units)}")
            print(f"  skipped (not body)  : {plan.skipped_non_translatable}")
            print(f"  already reusable    : {len(plan.reusable_units)}")
            print(f"  would be sent       : {len(plan.pending_units)}")
            print()
            print("THE FIRST UNITS THAT WOULD BE SENT")
            for unit in plan.pending_units[:8]:
                context = f"  ctx={unit.context!r}" if unit.context else ""
                print(f"  {unit.block_type:9} {unit.block_id} {unit.text[:58]!r}{context}")
            if len(plan.pending_units) > 8:
                print(f"  ... and {len(plan.pending_units) - 8} more")

            print()
            print("CONFIGURATION")
            for key, value in describe_configuration().items():
                print(f"  {key:22} {value}")

            if provider is None:
                print()
                print("REFUSAL (no credential present, so this is what the reader will see)")
                print(f"  default base_url in code: {DEFAULT_OLLAMA_CLOUD_BASE_URL}")
                print("  that default is marked NOT VERIFIED in the source: ollama.com resolves")
                print("  to a non-public address here, so the endpoint could not be confirmed.")
                try:
                    service.execute(session, plan, max_units=args.limit)
                except TranslationNotConfigured as exc:
                    print(f"  error_code: {error_code_for(exc)}")
                    print(f"  message   : {exc}")
                print()
                print("REAL-SERVICE ACCEPTANCE: PENDING.  Nothing was translated and no")
                print("placeholder text was written.  Set RR_TRANSLATION_API_KEY to run it.")
                return 0

            # A credential exists, so this is a real, billable call.
            print()
            print(f"REAL CALL: sending at most {args.limit} unit(s) to {plan.provider}")
            try:
                run = service.execute(session, plan, max_units=args.limit)
            except TranslationBudgetExceeded as exc:
                print(f"  refused: {error_code_for(exc)}: {exc}")
                return 1
            session.flush()
            summary = run.summary()
            print()
            print("RESULT")
            for key, value in summary.items():
                print(f"  {key:22} {value}")
            print()
            for row in run.saved:
                print(f"  [{row.status}] {row.block_id} -> {row.text[:70]!r}")
            if run.unknown:
                print()
                print(f"  AMBIGUOUS: {len(run.unknown)} unit(s) may or may not have been")
                print("  processed.  They are recorded as unknown and NOT retried blindly.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
