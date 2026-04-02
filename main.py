import logging
import os
import re
import sys
from pathlib import Path
from typing import Final

from telegram import Update
from telegram.constants import ChatType
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, ContextTypes, MessageHandler, filters

"""
Telegram moderator bot for one forum topic inside one supergroup.

What it does:
- Checks messages only in one chosen Telegram topic (message_thread_id)
- Deletes messages without at least one hashtag
- Ignores all other topics in the group
- Can ignore admins and bots
- Checks text and captions

Environment variables:
- BOT_TOKEN=123456:ABC...
- TARGET_CHAT_ID=-1001234567890
- TARGET_THREAD_ID=42
- LOG_LEVEL=INFO
- LOG_ALL_MESSAGES=false
- IGNORE_ADMINS=true
- IGNORE_BOTS=true

Optional local .env support:
- Create a .env file in the same folder as this script
- Variables from .env are loaded only if they are not already set in the environment
"""

HASHTAG_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\w)#[\w\d_]+", re.UNICODE)
BASE_DIR: Final[Path] = Path(__file__).resolve().parent


def load_local_env(env_path: Path) -> None:
    """Load KEY=VALUE pairs from .env without external dependencies."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_local_env(BASE_DIR / ".env")


def get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


LOG_LEVEL: Final[str] = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger("telegram_hashtag_moderator")

 # BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "8640363370:AAHeKYHheX5HkCFvis48MhL-YHxHvQowVWk")
 # ARGET_CHAT_ID: Final[int] = int(os.getenv("TARGET_CHAT_ID", "-1003784432229"))
 # TARGET_THREAD_ID: Final[int] = int(os.getenv("TARGET_THREAD_ID", "2"))
LOG_ALL_MESSAGES: Final[bool] = get_bool_env("LOG_ALL_MESSAGES", False)
IGNORE_ADMINS: Final[bool] = get_bool_env("IGNORE_ADMINS", False)
IGNORE_BOTS: Final[bool] = get_bool_env("IGNORE_BOTS", True)
DELETE_SERVICE_MESSAGES: Final[bool] = False


def _validate_config() -> None:
    errors: list[str] = []

    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is not set")
    if TARGET_CHAT_ID == 0:
        errors.append("TARGET_CHAT_ID is not set")
    if TARGET_THREAD_ID == 0:
        errors.append("TARGET_THREAD_ID is not set")

    if errors:
        joined = "; ".join(errors)
        raise RuntimeError(f"Configuration error: {joined}")


def _message_text(update: Update) -> str:
    msg = update.effective_message
    if not msg:
        return ""
    return msg.text or msg.caption or ""


def _has_hashtag(update: Update) -> bool:
    msg = update.effective_message
    if not msg:
        return False

    entities = []
    if msg.entities:
        entities.extend(msg.entities)
    if msg.caption_entities:
        entities.extend(msg.caption_entities)

    if any(entity.type == "hashtag" for entity in entities):
        return True

    return bool(HASHTAG_RE.search(_message_text(update)))


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return False

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in {"administrator", "creator"}
    except TelegramError as exc:
        logger.warning("Could not check admin status for user %s: %s", user.id, exc)
        return False


async def moderate_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat:
        return

    if LOG_ALL_MESSAGES:
        logger.info(
            "chat_id=%s | chat_type=%s | thread_id=%s | user_id=%s | message_id=%s | text=%r",
            chat.id,
            chat.type,
            getattr(msg, "message_thread_id", None),
            getattr(user, "id", None),
            msg.message_id,
            _message_text(update),
        )

    if chat.type != ChatType.SUPERGROUP:
        return

    if chat.id != TARGET_CHAT_ID:
        return

    if getattr(msg, "message_thread_id", None) != TARGET_THREAD_ID:
        return

    if not DELETE_SERVICE_MESSAGES and not (_message_text(update).strip() or msg.caption):
        return

    if IGNORE_BOTS and user and user.is_bot:
        return

    if IGNORE_ADMINS and await _is_admin(update, context):
        return

    if _has_hashtag(update):
        return

    try:
        await msg.delete()
        logger.info(
            "Deleted message_id=%s from user_id=%s in thread_id=%s because hashtag was missing",
            msg.message_id,
            getattr(user, "id", None),
            TARGET_THREAD_ID,
        )
    except Forbidden:
        logger.error("No rights to delete messages. Make the bot an admin with delete permission.")
    except TelegramError as exc:
        logger.error("Failed to delete message %s: %s", msg.message_id, exc)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


def main() -> None:
    try:
        _validate_config()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    message_filter = filters.ALL & ~filters.COMMAND

    app.add_handler(MessageHandler(message_filter, moderate_topic))
    app.add_error_handler(on_error)

    logger.info(
        "Bot started | chat_id=%s | thread_id=%s | ignore_admins=%s | ignore_bots=%s",
        TARGET_CHAT_ID,
        TARGET_THREAD_ID,
        IGNORE_ADMINS,
        IGNORE_BOTS,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()