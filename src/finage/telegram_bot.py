from __future__ import annotations

import html
import logging
import re

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from finage.analysis import MomentumAnalysisService, normalize_ticker_symbol
from finage.digest import DigestService
from finage.models import DigestResult
from finage.settings import Settings

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 3900
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def is_authorized(update: Update, allowed_ids: set[int]) -> bool:
    if not allowed_ids:
        return False

    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    return user_id in allowed_ids or chat_id in allowed_ids


def chunk_text(text: str, limit: int = SAFE_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


def markdown_to_telegram_html(text: str) -> str:
    """Render common Markdown output as Telegram-supported HTML."""

    def _format_plain_segment(seg: str) -> str:
        """Apply Markdown links, escaping, and bold to text outside inline code spans."""

        links: list[str] = []

        def link_sub(match: re.Match[str]) -> str:
            idx = len(links)
            label = html.escape(match.group(1))
            href = html.escape(match.group(2), quote=True)
            links.append(f'<a href="{href}">{label}</a>')
            return f"\x00LNK{idx}\x00"

        out = MD_LINK_RE.sub(link_sub, seg)
        out = html.escape(out)
        for idx, anchor in enumerate(links):
            out = out.replace(f"\x00LNK{idx}\x00", anchor)
        return BOLD_RE.sub(r"<b>\1</b>", out)

    def format_inline(value: str) -> str:
        pieces: list[str] = []
        last_end = 0
        for m in CODE_RE.finditer(value):
            pieces.append(_format_plain_segment(value[last_end : m.start()]))
            pieces.append(f"<code>{html.escape(m.group(1))}</code>")
            last_end = m.end()
        pieces.append(_format_plain_segment(value[last_end:]))
        return "".join(pieces)

    lines: list[str] = []
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            heading_text = heading.group(2).strip()
            if heading_text.startswith("**") and heading_text.endswith("**"):
                heading_text = heading_text[2:-2]
            lines.append(f"<b>{format_inline(heading_text)}</b>")
        else:
            lines.append(format_inline(line))

    return "\n".join(lines)


async def send_text(bot: Bot, chat_id: int, text: str, *, parse_mode: str | None = None) -> None:
    chunks = chunk_text(text)
    logger.info("Sending Telegram message to chat_id=%s chunks=%s parse_mode=%s", chat_id, len(chunks), parse_mode)
    for index, chunk in enumerate(chunks, start=1):
        logger.debug("Sending Telegram chunk %s/%s chars=%s", index, len(chunks), len(chunk))
        await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )


async def send_markdown_text(bot: Bot, chat_id: int, text: str) -> None:
    await send_text(
        bot,
        chat_id,
        markdown_to_telegram_html(text),
        parse_mode=ParseMode.HTML,
    )


async def send_digest(settings: Settings, digest: DigestResult, *, whale_followup: str | None = None) -> None:
    if settings.telegram_default_chat_id is None:
        raise ValueError("TELEGRAM_DEFAULT_CHAT_ID is required for `finage digest send`")

    logger.info("Sending generated digest to default Telegram chat_id=%s", settings.telegram_default_chat_id)
    async with Bot(token=settings.telegram_bot_token) as bot:
        await send_markdown_text(bot, settings.telegram_default_chat_id, digest.digest)
        if whale_followup:
            await send_markdown_text(bot, settings.telegram_default_chat_id, whale_followup)


async def send_whale_brief(settings: Settings, brief: str) -> None:
    if settings.telegram_default_chat_id is None:
        raise ValueError("TELEGRAM_DEFAULT_CHAT_ID is required for `finage whale send`")

    async with Bot(token=settings.telegram_bot_token) as bot:
        await send_markdown_text(bot, settings.telegram_default_chat_id, brief)


class TelegramDigestBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.allowed_ids = settings.telegram_allowed_id_set

    def run(self) -> None:
        logger.info("Starting Telegram bot polling with allowed_ids_count=%s", len(self.allowed_ids))
        application = ApplicationBuilder().token(self.settings.telegram_bot_token).build()
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler("digest", self.digest))
        application.add_handler(CommandHandler("live", self.live))
        application.add_handler(CommandHandler("ticker", self.ticker))
        application.add_handler(CommandHandler("why", self.why))
        application.add_handler(CommandHandler("movers", self.movers))
        application.add_handler(CommandHandler("senate", self.senate))
        application.add_handler(CommandHandler("whale", self.whale))
        application.add_handler(CommandHandler("whales", self.whales))
        application.add_handler(CommandHandler("health", self.health))
        application.run_polling()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        await update.effective_message.reply_text(
            "Finage is running. Use /digest for a full digest, /live for an ad hoc scan, "
            "/ticker TSLA for focused ticker evidence, /why TSLA for an explanation, "
            "/senate TSLA for congressional trading analysis, /whale TSLA for institutional 13F activity, "
            "/whales for top whale momentum, /movers for changes versus the latest digest, "
            "or /health for bot status."
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        await update.effective_message.reply_text(
            "Commands:\n"
            "/digest - scrape stock subreddits, generate a Gemini digest, and return it here.\n"
            "/live - run a fresh social momentum scan and return a compact market brief.\n"
            "/ticker <stock> - run a fresh scan and return focused evidence for one ticker.\n"
            "/why <stock> - explain the strongest narratives behind one ticker using Gemini.\n"
            "/senate <stock> - analyze Senate and House trading disclosures for one ticker.\n"
            "/whale <stock> - analyze top-10 institutional 13F activity for one ticker.\n"
            "/whales - summarize top whale momentum across the watchlist.\n"
            "/movers - compare a fresh scan against the latest saved digest snapshot.\n"
            "/health - check config, artifacts, data directory, and ApeWisdom reachability."
        )

    async def digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /digest request from user_id=%s chat_id=%s", user_id, chat_id)
        await update.effective_message.reply_text("Generating WSB digest...")
        try:
            result = await DigestService(self.settings).generate()
            await send_markdown_text(context.bot, update.effective_chat.id, result.digest)
            if self.settings.whale_enabled:
                whale_brief = await MomentumAnalysisService(self.settings).whales()
                await send_markdown_text(context.bot, update.effective_chat.id, whale_brief)
            logger.info("Completed /digest request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate digest")
            await update.effective_message.reply_text("Digest generation failed. Check the Pi logs.")

    async def live(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /live request from user_id=%s chat_id=%s", user_id, chat_id)
        await update.effective_message.reply_text("Scanning live social momentum...")
        try:
            brief = await MomentumAnalysisService(self.settings).live()
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /live request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate live momentum brief")
            await update.effective_message.reply_text("Live scan failed. Check the Pi logs.")

    async def ticker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /ticker TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /ticker request from user_id=%s chat_id=%s ticker=%s", user_id, chat_id, symbol)
        await update.effective_message.reply_text(f"Scanning social momentum for {symbol}...")
        try:
            brief = await MomentumAnalysisService(self.settings).ticker(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /ticker request for chat_id=%s ticker=%s", chat_id, symbol)
        except Exception:
            logger.exception("Failed to generate ticker momentum brief")
            await update.effective_message.reply_text("Ticker scan failed. Check the Pi logs.")

    async def why(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /why TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc).replace("/ticker", "/why"))
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /why request from user_id=%s chat_id=%s ticker=%s", user_id, chat_id, symbol)
        await update.effective_message.reply_text(f"Analyzing why {symbol} is moving socially...")
        try:
            brief = await MomentumAnalysisService(self.settings).why(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /why request for chat_id=%s ticker=%s", chat_id, symbol)
        except Exception:
            logger.exception("Failed to generate why brief")
            await update.effective_message.reply_text("Why analysis failed. Check the Pi logs.")

    async def senate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /senate TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc).replace("/ticker", "/senate"))
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /senate request from user_id=%s chat_id=%s ticker=%s", user_id, chat_id, symbol)
        await update.effective_message.reply_text(f"Analyzing congressional trades for {symbol}...")
        try:
            brief = await MomentumAnalysisService(self.settings).senate(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /senate request for chat_id=%s ticker=%s", chat_id, symbol)
        except Exception:
            logger.exception("Failed to generate senate brief")
            await update.effective_message.reply_text("Senate analysis failed. Check the Pi logs.")

    async def whale(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        if len(context.args) != 1:
            await update.effective_message.reply_text("Usage: /whale TSLA")
            return

        try:
            symbol = normalize_ticker_symbol(context.args[0])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc).replace("/ticker", "/whale"))
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /whale request from user_id=%s chat_id=%s ticker=%s", user_id, chat_id, symbol)
        await update.effective_message.reply_text(f"Analyzing whale 13F activity for {symbol}...")
        try:
            brief = await MomentumAnalysisService(self.settings).whale(symbol)
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /whale request for chat_id=%s ticker=%s", chat_id, symbol)
        except Exception:
            logger.exception("Failed to generate whale brief")
            await update.effective_message.reply_text("Whale analysis failed. Check the Pi logs.")

    async def whales(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /whales request from user_id=%s chat_id=%s", user_id, chat_id)
        await update.effective_message.reply_text("Analyzing top whale 13F momentum...")
        try:
            brief = await MomentumAnalysisService(self.settings).whales()
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /whales request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate whales brief")
            await update.effective_message.reply_text("Whales analysis failed. Check the Pi logs.")

    async def movers(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /movers request from user_id=%s chat_id=%s", user_id, chat_id)
        await update.effective_message.reply_text("Scanning social momentum movers...")
        try:
            brief = await MomentumAnalysisService(self.settings).movers()
            await send_markdown_text(context.bot, update.effective_chat.id, brief)
            logger.info("Completed /movers request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate movers brief")
            await update.effective_message.reply_text("Movers scan failed. Check the Pi logs.")

    async def health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return

        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        logger.info("Received /health request from user_id=%s chat_id=%s", user_id, chat_id)
        await update.effective_message.reply_text("Checking Finage health...")
        try:
            report = await MomentumAnalysisService(self.settings).health()
            await send_markdown_text(context.bot, update.effective_chat.id, report)
            logger.info("Completed /health request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate health report")
            await update.effective_message.reply_text("Health check failed. Check the Pi logs.")

    async def _guard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if is_authorized(update, self.allowed_ids):
            return True

        if update.effective_chat:
            user_id = update.effective_user.id if update.effective_user else None
            logger.warning(
                "Rejected unauthorized Telegram request from user_id=%s chat_id=%s",
                user_id,
                update.effective_chat.id,
            )
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized.")
        return False
