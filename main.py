import asyncio
import html
import logging
import os
import re
import sys
from pathlib import Path
from typing import Final

import telegram

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

HASHTAG_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\w)#[\w\d_]+", re.UNICODE)
BASE_DIR: Final[Path] = Path(__file__).resolve().parent


def load_local_env(env_path: Path) -> None:
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

BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "")
TARGET_CHAT_ID: Final[int] = int(os.getenv("TARGET_CHAT_ID", "-1002672769627"))
TARGET_THREAD_ID: Final[int] = int(os.getenv("TARGET_THREAD_ID", "102816"))
LOG_ALL_MESSAGES: Final[bool] = get_bool_env("LOG_ALL_MESSAGES", False)
IGNORE_ADMINS: Final[bool] = get_bool_env("IGNORE_ADMINS", True)
IGNORE_BOTS: Final[bool] = get_bool_env("IGNORE_BOTS", True)
DELETE_SERVICE_MESSAGES: Final[bool] = False
DELETE_WARNING_AFTER_SECONDS: Final[int] = 10

WARNING_MESSAGE_TEMPLATE: Final[str] = (
    "{mention}, где хештег? Исправляйся!"
)

BOT_ENABLED: bool = True

SERVICE_MESSAGE_FIELDS: Final[tuple[str, ...]] = (
    "new_chat_members",
    "left_chat_member",
    "new_chat_title",
    "new_chat_photo",
    "delete_chat_photo",
    "group_chat_created",
    "supergroup_chat_created",
    "channel_chat_created",
    "message_auto_delete_timer_changed",
    "migrate_to_chat_id",
    "migrate_from_chat_id",
    "pinned_message",
    "forum_topic_created",
    "forum_topic_edited",
    "forum_topic_closed",
    "forum_topic_reopened",
    "general_forum_topic_hidden",
    "general_forum_topic_unhidden",
    "write_access_allowed",
    "users_shared",
    "chat_shared",
    "giveaway_created",
    "giveaway",
    "giveaway_winners",
    "giveaway_completed",
    "video_chat_scheduled",
    "video_chat_started",
    "video_chat_ended",
    "video_chat_participants_invited",
    "boost_added",
    "chat_background_set",
    "checklist_tasks_done",
    "checklist_tasks_added",
)


def _validate_config() -> None:
    errors: list[str] = []

    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is not set")
    if TARGET_CHAT_ID == 0:
        errors.append("TARGET_CHAT_ID is not set")
    if TARGET_THREAD_ID == 0:
        errors.append("TARGET_THREAD_ID is not set")

    if errors:
        raise RuntimeError("Configuration error: " + "; ".join(errors))


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


def _is_service_message(msg) -> bool:
    for field in SERVICE_MESSAGE_FIELDS:
        value = getattr(msg, field, None)
        if value:
            return True
    return False


def _is_user_content_message(msg) -> bool:
    if msg.text or msg.caption:
        return True

    media_fields = (
        msg.photo,
        msg.video,
        msg.video_note,
        msg.document,
        msg.audio,
        msg.voice,
        msg.animation,
        msg.sticker,
        msg.contact,
        msg.location,
        msg.venue,
        msg.poll,
    )

    return any(media_fields)


def _is_management_command(text: str) -> bool:
    if not text:
        return False

    command = text.strip().split()[0].lower()
    base = command.split("@")[0]
    return base in {"/on", "/off", "/status"}


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


async def _delete_message_safe(message) -> None:
    if not message:
        return
    try:
        await message.delete()
    except TelegramError:
        pass


async def _delete_bot_message_later(bot_message, context: ContextTypes.DEFAULT_TYPE, seconds: int) -> None:
    try:
        await asyncio.sleep(seconds)
        await context.bot.delete_message(
            chat_id=bot_message.chat_id,
            message_id=bot_message.message_id,
        )
    except TelegramError as exc:
        logger.warning(
            "Failed to delete bot message %s: %s",
            getattr(bot_message, "message_id", None),
            exc,
        )


async def _send_temporary_warning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    mention = f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "пользователь")}</a>'
    first_name = user.first_name or ""
    username = user.username or ""

    try:
        warning_text = WARNING_MESSAGE_TEMPLATE.format(
            mention=mention,
            first_name=first_name,
            username=username,
        )
    except Exception as exc:
        logger.error("Invalid WARNING_MESSAGE_TEMPLATE format: %s", exc)
        warning_text = f"{mention}, сообщение удалено."

    try:
        sent = await context.bot.send_message(
            chat_id=chat.id,
            text=warning_text,
            message_thread_id=getattr(msg, "message_thread_id", None),
            parse_mode=ParseMode.HTML,
        )
        context.application.create_task(
            _delete_bot_message_later(sent, context, DELETE_WARNING_AFTER_SECONDS)
        )
    except TelegramError as exc:
        logger.error("Failed to send warning message: %s", exc)


async def _reply_temp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    delete_after: int = 10,
) -> None:
    msg = update.effective_message
    chat = update.effective_chat

    if not msg or not chat:
        return

    try:
        sent = await context.bot.send_message(
            chat_id=chat.id,
            text=text,
            message_thread_id=getattr(msg, "message_thread_id", None),
        )
        if delete_after > 0:
            context.application.create_task(
                _delete_bot_message_later(sent, context, delete_after)
            )
    except TelegramError as exc:
        logger.error("Failed to send temporary reply: %s", exc)


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_ENABLED

    msg = update.effective_message

    if not await _is_admin(update, context):
        await _delete_message_safe(msg)
        return

    BOT_ENABLED = False
    logger.info("Moderation disabled by user_id=%s", getattr(update.effective_user, "id", None))
    await _delete_message_safe(msg)
    await _reply_temp(update, context, "Модерация выключена.", delete_after=10)


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_ENABLED

    msg = update.effective_message

    if not await _is_admin(update, context):
        await _delete_message_safe(msg)
        return

    BOT_ENABLED = True
    logger.info("Moderation enabled by user_id=%s", getattr(update.effective_user, "id", None))
    await _delete_message_safe(msg)
    await _reply_temp(update, context, "Модерация включена.", delete_after=10)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message

    if not await _is_admin(update, context):
        await _delete_message_safe(msg)
        return

    status_text = "Модерация включена." if BOT_ENABLED else "Модерация выключена."
    await _delete_message_safe(msg)
    await _reply_temp(update, context, status_text, delete_after=10)


async def moderate_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_ENABLED

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

    if _is_management_command(_message_text(update)):
        return

    if not BOT_ENABLED:
        return

    if not DELETE_SERVICE_MESSAGES and _is_service_message(msg):
        return

    if not _is_user_content_message(msg):
        return

    if IGNORE_BOTS and user and user.is_bot:
        return

    if IGNORE_ADMINS and await _is_admin(update, context):
        return

    if _has_hashtag(update):
        return

    try:
        await msg.delete()
        await _send_temporary_warning(update, context)
        logger.info(
            "Deleted message_id=%s from user_id=%s in thread_id=%s because hashtag was missing",
            msg.message_id,
            getattr(user, "id", None),
            TARGET_THREAD_ID,
        )
    except Forbidden:
        logger.error("No rights to delete messages. Make the bot an admin with delete permission.")
    except TelegramError as exc:
        logger.error("Failed to moderate message %s: %s", msg.message_id, exc)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)


def main() -> None:
    try:
        _validate_config()
    except RuntimeError as exc:
        logger.error(str(exc))
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("status", cmd_status))

    message_filter = filters.ALL
    app.add_handler(MessageHandler(message_filter, moderate_topic))

    app.add_error_handler(on_error)

    logger.info(
        "Bot started | chat_id=%s | thread_id=%s | ignore_admins=%s | ignore_bots=%s | enabled=%s",
        TARGET_CHAT_ID,
        TARGET_THREAD_ID,
        IGNORE_ADMINS,
        IGNORE_BOTS,
        BOT_ENABLED,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()