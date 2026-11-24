"""Command-line interface for the AudioSilo Chapter Sidecar.

Commands:
    chapter_sidecar list
    chapter_sidecar show --title "Book title"
    chapter_sidecar process --title "Book title" [--author "Author"]
    chapter_sidecar process --batch N
    chapter_sidecar process --all
    chapter_sidecar retry
    chapter_sidecar report

Options: --dry-run, --limit, --force, --provider, --library-id, --verbose

WARNING: AudioSilo API endpoints are unverified candidates. The list/show
commands will work only after the API is confirmed against the running
2.6.0 instance. Dry-run mode is enabled by default.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from .audiosilo_client import AudioSiloClient
from .community import CommunityChecker
from .config import Config, load_config
from .database import SidecarDatabase
from .dryrun_writer import DryRunWriter
from .processor import Processor
from .providers.audiosilo_meta import AudioSiloMetaProvider
from .providers.audnexus import AudnexusProvider
from .reporting import format_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chapter_sidecar",
        description="AudioSilo Chapter Sidecar — find and add chapters without transcription.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("list", help="List eligible books in the library")

    show_parser = subparsers.add_parser("show", help="Show book metadata and chapter status")
    show_parser.add_argument("--title", required=True, help="Book title to search for")

    process_parser = subparsers.add_parser("process", help="Process books for chapters")
    process_group = process_parser.add_mutually_exclusive_group(required=True)
    process_group.add_argument("--title", help="Process a single book by title")
    process_group.add_argument("--batch", type=int, metavar="N", help="Process batch N")
    process_group.add_argument("--all", action="store_true", help="Process all eligible books")
    process_parser.add_argument("--author", help="Author name (with --title)")
    process_parser.add_argument("--force", action="store_true", help="Force reprocessing even if chapters exist")
    process_parser.add_argument("--limit", type=int, metavar="N", help="Max books to process")

    subparsers.add_parser("retry", help="Retry failed books")
    subparsers.add_parser("report", help="Summarise counts by status and provider")

    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode (write [title]-chapters.txt only)")
    parser.add_argument("--no-dry-run", action="store_true", help="Override dry-run and write to AudioSilo")
    parser.add_argument("--provider", choices=["audiosilo_meta", "audnexus"], help="Provider to use")
    parser.add_argument("--library-id", help="AudioSilo library ID")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    return parser


def create_components(config: Config):
    client = AudioSiloClient(config)
    community = CommunityChecker(client, config)
    store = SidecarDatabase(config.sidecar_database)
    dry_run_writer = DryRunWriter(config.dry_run_output_dir)

    providers: list = []
    providers.append(AudnexusProvider(region=config.audnexus_region))
    providers.append(AudioSiloMetaProvider(audnexus_region=config.audnexus_region))

    processor = Processor(
        client=client,
        community=community,
        providers=providers,
        store=store,
        dry_run_writer=dry_run_writer,
        config=config,
    )
    return client, community, store, dry_run_writer, processor


def cmd_list(args: argparse.Namespace, config: Config, client: AudioSiloClient) -> int:
    if not config.audiosilo_library_id:
        print("ERROR: AUDIOSILO_LIBRARY_ID is not set in .env", file=sys.stderr)
        return 1
    try:
        books = client.list_books()
    except Exception as exc:
        print(f"ERROR: Failed to list books: {exc}", file=sys.stderr)
        print(
            "Hint: The list_books endpoint and response schema are unverified "
            "candidates. Confirm them against AudioSilo 2.6.0.",
            file=sys.stderr,
        )
        return 1

    print(f"Books in library {config.audiosilo_library_id}:")
    for book in books:
        print(f"  - {book}")
    return 0


def cmd_show(args: argparse.Namespace, config: Config, client: AudioSiloClient) -> int:
    if not config.audiosilo_library_id:
        print("ERROR: AUDIOSILO_LIBRARY_ID is not set in .env", file=sys.stderr)
        return 1
    try:
        books = client.list_books()
    except Exception as exc:
        print(f"ERROR: Failed to list books: {exc}", file=sys.stderr)
        return 1

    title = args.title
    matches = [b for b in books if title.lower() in str(b.get("title", "")).lower()]

    if not matches:
        print(f"No books found matching title: {title}")
        return 0
    if len(matches) > 1:
        print(f"Multiple books match '{title}'. Please be more specific:")
        for b in matches:
            print(f"  - {b}")
        return 1

    book = matches[0]
    print(f"Book: {book}")
    return 0


def cmd_process(
    args: argparse.Namespace,
    config: Config,
    processor: Processor,
    store: SidecarDatabase,
) -> int:
    dry_run = config.dry_run
    if args.no_dry_run:
        dry_run = False
    if args.dry_run:
        dry_run = True

    if args.title:
        result = processor.process_one(
            book_path=args.title,
            title=args.title,
            author=args.author,
            dry_run=dry_run,
            force=args.force,
        )
        print(f"[{result.status}] {result.message}")
        if result.chapters:
            print(f"  Chapters: {len(result.chapters)}")
        return 0 if result.status not in ("lookup_failed", "write_failed") else 1

    if args.batch is not None:
        results = processor.process_batch(
            batch_number=args.batch,
            limit=args.limit,
            dry_run=dry_run,
        )
        for r in results:
            print(f"[{r.status}] {r.book.book_path}: {r.message}")
        failures = [r for r in results if r.status in ("lookup_failed", "write_failed")]
        return 1 if failures else 0

    if args.all:
        results = processor.process_all(dry_run=dry_run)
        for r in results:
            print(f"[{r.status}] {r.book.book_path}: {r.message}")
        failures = [r for r in results if r.status in ("lookup_failed", "write_failed")]
        return 1 if failures else 0

    print("ERROR: No processing target specified", file=sys.stderr)
    return 1


def cmd_retry(args: argparse.Namespace, config: Config, processor: Processor) -> int:
    dry_run = config.dry_run
    if args.no_dry_run:
        dry_run = False
    if args.dry_run:
        dry_run = True

    results = processor.retry(dry_run=dry_run)
    for r in results:
        print(f"[{r.status}] {r.book.book_path}: {r.message}")
    failures = [r for r in results if r.status in ("lookup_failed", "write_failed")]
    return 1 if failures else 0


def cmd_report(args: argparse.Namespace, config: Config, store: SidecarDatabase) -> int:
    if not config.audiosilo_library_id:
        print("ERROR: AUDIOSILO_LIBRARY_ID is not set in .env", file=sys.stderr)
        return 1
    print(format_report(store, config.audiosilo_library_id))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    config = load_config()

    if args.verbose:
        print(f"Config: base_url={config.audiosilo_base_url}")
        print(f"Config: library_id={config.audiosilo_library_id}")
        print(f"Config: dry_run={config.dry_run}")
        print(f"Config: audnexus_region={config.audnexus_region}")
        print()

    client, community, store, dry_run_writer, processor = create_components(config)

    if args.command == "list":
        return cmd_list(args, config, client)
    if args.command == "show":
        return cmd_show(args, config, client)
    if args.command == "process":
        return cmd_process(args, config, processor, store)
    if args.command == "retry":
        return cmd_retry(args, config, processor)
    if args.command == "report":
        return cmd_report(args, config, store)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())