from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from finage.congress import CongressCollector
from finage.digest import DigestService
from finage.settings import Settings
from finage.telegram_bot import TelegramDigestBot, send_digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finage", description="Finage WSB Telegram digest MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bot", help="Run the Telegram bot listener")

    digest_parser = subparsers.add_parser("digest", help="Generate WSB digests")
    digest_subparsers = digest_parser.add_subparsers(dest="digest_command", required=True)
    digest_subparsers.add_parser("preview", help="Generate and print a digest without sending it")
    digest_subparsers.add_parser("send", help="Generate and send one digest to TELEGRAM_DEFAULT_CHAT_ID")

    congress_parser = subparsers.add_parser("congress", help="Manage congressional trading data")
    congress_subparsers = congress_parser.add_subparsers(dest="congress_command", required=True)
    congress_subparsers.add_parser("refresh", help="Fetch and persist latest Senate and House trades")

    return parser


async def _run_digest_command(settings: Settings, digest_command: str) -> int:
    result = await DigestService(settings).generate()

    if digest_command == "preview":
        print(result.digest)
        return 0

    if digest_command == "send":
        await send_digest(settings, result)
        print(f"Sent digest to Telegram chat {settings.telegram_default_chat_id}")
        return 0

    raise ValueError(f"Unsupported digest command: {digest_command}")


async def _run_congress_command(settings: Settings, congress_command: str) -> int:
    if congress_command == "refresh":
        collector = CongressCollector(settings)
        snapshot = await collector.fetch(bootstrap=collector.get_cached() is None)
        print(
            "Congress refresh complete: "
            f"total_trades={snapshot.total_trades} "
            f"new_trades={snapshot.new_trades} "
            f"corrected_trades={snapshot.corrected_trades} "
            f"cache={collector.base_dir}"
        )
        return 0

    raise ValueError(f"Unsupported congress command: {congress_command}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()

    if args.command == "bot":
        TelegramDigestBot(settings).run()
        return 0

    if args.command == "digest":
        return asyncio.run(_run_digest_command(settings, args.digest_command))

    if args.command == "congress":
        return asyncio.run(_run_congress_command(settings, args.congress_command))

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
