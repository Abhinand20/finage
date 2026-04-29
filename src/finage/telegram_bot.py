from __future__ import annotations

import html
import logging
import re

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from finage.digest import DigestService
from finage.models import DigestResult
from finage.settings import Settings

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 3900
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


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

    def format_inline(value: str) -> str:
        escaped = html.escape(value)
        escaped = CODE_RE.sub(r"<code>\1</code>", escaped)
        return BOLD_RE.sub(r"<b>\1</b>", escaped)

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


async def send_digest(settings: Settings, digest: DigestResult) -> None:
    if settings.telegram_default_chat_id is None:
        raise ValueError("TELEGRAM_DEFAULT_CHAT_ID is required for `finage digest send`")

    logger.info("Sending generated digest to default Telegram chat_id=%s", settings.telegram_default_chat_id)
    async with Bot(token=settings.telegram_bot_token) as bot:
        await send_markdown_text(bot, settings.telegram_default_chat_id, digest.digest)


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
        application.run_polling()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        await update.effective_message.reply_text(
            "Finage is running. Use /digest to generate the latest WSB momentum digest."
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, context):
            return
        await update.effective_message.reply_text(
            "Commands:\n/digest - scrape WSB, generate a Gemini digest, and return it here."
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
            logger.info("Completed /digest request for chat_id=%s", chat_id)
        except Exception:
            logger.exception("Failed to generate digest")
            await update.effective_message.reply_text("Digest generation failed. Check the Pi logs.")

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
