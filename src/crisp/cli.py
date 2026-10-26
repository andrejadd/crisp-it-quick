"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from crisp import __version__
from crisp.config import ConfigError, load_settings
from crisp.logs import log_startup_config, setup_logging


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crisp",
        description="Pull recordings off a USB voice recorder and transcribe them to markdown.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--config",
        default=os.environ.get("CRISP_CONFIG", "config.yaml"),
        help="path to config.yaml (default: $CRISP_CONFIG or ./config.yaml)",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", help="run the daemon (default)")
    sub.add_parser("check-config", help="validate configuration and exit")
    sub.add_parser("sync", help="mark archive files deleted by hand in the state database")
    return p


async def _run(settings) -> None:
    from crisp.service import run_service

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_service(settings, stop)


def _sync(settings) -> None:
    from crisp.db import Repository
    from crisp.sync import sync_archive

    repo = Repository(settings.paths.db_path)
    try:
        repo.migrate()
        result = sync_archive(repo, settings.paths.archive_dir)
    finally:
        repo.close()
    logging.getLogger("crisp").info(
        "sync finished: %d newly deleted, %d restored", result.deleted, result.restored
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(args.config, require_secrets=args.command != "sync")
    except ConfigError as e:
        print(f"crisp: {e}", file=sys.stderr)
        return 2
    setup_logging(settings.logging.level, settings.secrets())
    log_startup_config(settings)
    if args.command == "check-config":
        logging.getLogger("crisp").info("configuration OK")
        return 0
    if args.command == "sync":
        _sync(settings)
        return 0
    asyncio.run(_run(settings))
    return 0
