import asyncio
import html
import logging
import os
import re
import sys
from pathlib import Path
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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

# Для GENERAL оставь пустым в .env:
# DISCUSSION_THREAD_ID=
# Если позже захочешь слать в отдельную тему, укажи здесь ее message_thread_id.
DISCUSSION_THREAD_ID_RAW: Final[str] = os.getenv("DISCUSSION_THREAD_ID", "").strip()
DISCUSSION_THREAD_ID: Final[int | None] = int(DISCUSSION_THREAD_ID_RAW) if DISCUSSION_THREAD_ID_RAW else None

LOG_ALL_MESSAGES: Final[bool] = get_bool_env("LOG_ALL_MESSAGES", False)
IGNORE_ADMINS: Final[bool] = get_bool_env("IGNORE_ADMINS", True)
IGNORE_BOTS: Final[bool] = get_bool_env("IGNORE_BOTS", True)
DELETE_SERVICE_MESSAGES: Final[bool] = False
DELETE_WARNING_AFTER_SECONDS: Final[int] = 10
DISCUSS_BUTTON_TTL_SECONDS: Final[int] = 24 * 60 * 60
GO_TO_GENERAL_BUTTON_TTL_SECONDS: Final[int] = 10

WARNING_MESSAGE_TEMPLATE: Final[str] = (
    "<b><i>{mention}, где хештег? Исправляйся!</i></b>"
)

BOT_ENABLED: bool = True

SOURCE_MESSAGE_MISSING_ERROR_MARKERS: Final[tuple[str, ...]] = (
    "message to copy not found",
    "message not found",
)

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


def _is_edited_message_update(update: Update) -> bool:
    return update.edited_message is not None or update.edited_channel_post is not None


def _is_source_message_missing_error(exc: TelegramError) -> bool:
    error_text = str(exc).lower()
    return any(marker in error_text for marker in SOURCE_MESSAGE_MISSING_ERROR_MARKERS)


def _user_name_link_html(user_id: int, first_name: str | None) -> str:
    display_name = html.escape(first_name or "пользователь")
    return f'<a href="tg://user?id={user_id}">{display_name}</a>'


def _build_discuss_callback_data(source_message_id: int, author_user_id: int) -> str:
    return f"discuss:{source_message_id}:{author_user_id}"


def _get_discuss_button(source_message_id: int, author_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "Обсудить в GENERAL",
                callback_data=_build_discuss_callback_data(source_message_id, author_user_id),
            )
        ]]
    )


def _get_go_to_general_button(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Перейти в GENERAL", url=url)]]
    )


def _build_general_message_url(chat_id: int, message_id: int) -> str:
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        internal_id = chat_id_str[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return f"https://t.me/c/{chat_id_str}/{message_id}"


def _is_image_document(msg) -> bool:
    if not getattr(msg, "document", None):
        return False

    document = msg.document
    mime_type = (document.mime_type or "").lower()
    file_name = (document.file_name or "").lower()

    if mime_type.startswith("image/"):
        return True

    return file_name.endswith((".jpg", ".jpeg", ".png", ".webp"))


def _is_chart_image_post(msg) -> bool:
    if not msg:
        return False

    if getattr(msg, "photo", None):
        return True

    if _is_image_document(msg):
        return True

    return False


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


async def _get_user_first_name(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        user = getattr(member, "user", None)
        return getattr(user, "first_name", None)
    except TelegramError as exc:
        logger.warning("Could not get first name for user %s: %s", user_id, exc)
        return None


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


async def _delete_discuss_button_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    button_message_id = job_data["button_message_id"]

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=button_message_id)
    except TelegramError as exc:
        logger.warning("Failed to delete discuss button message %s: %s", button_message_id, exc)


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


async def _send_temporary_go_to_general_button(
    chat_id: int,
    thread_id: int | None,
    copied_message_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    try:
        url = _build_general_message_url(chat_id, copied_message_id)
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=">>>",
            message_thread_id=thread_id,
            reply_markup=_get_go_to_general_button(url),
        )
        context.application.create_task(
            _delete_bot_message_later(sent, context, GO_TO_GENERAL_BUTTON_TTL_SECONDS)
        )
    except TelegramError as exc:
        logger.error("Failed to send 'Go to GENERAL' button: %s", exc)


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


async def handle_discuss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    if not query.data.startswith("discuss:"):
        return

    await query.answer()

    try:
        _, source_message_id_str, original_author_id_str = query.data.split(":", 2)
    except ValueError:
        logger.error("Invalid callback_data format: %s", query.data)
        return

    try:
        source_message_id = int(source_message_id_str)
        original_author_id = int(original_author_id_str)
    except ValueError:
        logger.error("Invalid numeric IDs in callback_data: %s", query.data)
        return

    source_chat_id = query.message.chat.id
    source_thread_id = getattr(query.message, "message_thread_id", None)
    button_message = query.message
    proposer = query.from_user

    copy_kwargs = {
        "chat_id": source_chat_id,
        "from_chat_id": source_chat_id,
        "message_id": source_message_id,
    }

    if DISCUSSION_THREAD_ID is not None:
        copy_kwargs["message_thread_id"] = DISCUSSION_THREAD_ID

    try:
        copied = await context.bot.copy_message(**copy_kwargs)

        logger.info(
            "Copied source message_id=%s to discussion target=%s as new message_id=%s",
            source_message_id,
            DISCUSSION_THREAD_ID if DISCUSSION_THREAD_ID is not None else "GENERAL",
            getattr(copied, "message_id", None),
        )

    except TelegramError as exc:
        logger.error(
            "Discuss copy failed | source_chat_id=%s | source_message_id=%s | discussion_target=%s | error=%s",
            source_chat_id,
            source_message_id,
            DISCUSSION_THREAD_ID if DISCUSSION_THREAD_ID is not None else "GENERAL",
            exc,
        )
        if _is_source_message_missing_error(exc):
            await _delete_message_safe(button_message)
        return

    original_author_first_name = await _get_user_first_name(source_chat_id, original_author_id, context)

    discussion_text = (
        f"<b><i>"
    f"│ 👤 {_user_name_link_html(proposer.id, proposer.first_name)} предлагает обсудить\n"
    f"│ 📊 пост от {_user_name_link_html(original_author_id, original_author_first_name)}\n"
    f"</i></b>"
    )

    try:
        send_kwargs = {
            "chat_id": source_chat_id,
            "text": discussion_text,
            "parse_mode": ParseMode.HTML,
        }
        if DISCUSSION_THREAD_ID is not None:
            send_kwargs["message_thread_id"] = DISCUSSION_THREAD_ID

        await context.bot.send_message(**send_kwargs)
    except TelegramError as exc:
        logger.error("Failed to send discussion intro message: %s", exc)

    try:
        await button_message.delete()
    except TelegramError as exc:
        logger.error(
            "Copied successfully, but failed to delete discuss button message_id=%s: %s",
            button_message.message_id,
            exc,
        )

    await _send_temporary_go_to_general_button(
        chat_id=source_chat_id,
        thread_id=source_thread_id,
        copied_message_id=copied.message_id,
        context=context,
    )


async def moderate_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global BOT_ENABLED

    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
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

    if IGNORE_BOTS and user.is_bot:
        return

    if _has_hashtag(update):
        if _is_chart_image_post(msg) and not _is_edited_message_update(update):
            try:
                button_msg = await context.bot.send_message(
                    chat_id=chat.id,
                    text="↑↑↑",
                    message_thread_id=msg.message_thread_id,
                    reply_markup=_get_discuss_button(msg.message_id, user.id),
                )

                if context.job_queue is not None:
                    context.job_queue.run_once(
                        _delete_discuss_button_job,
                        when=DISCUSS_BUTTON_TTL_SECONDS,
                        data={
                            "chat_id": chat.id,
                            "button_message_id": button_msg.message_id,
                        },
                        name=f"discuss_btn_{chat.id}_{button_msg.message_id}",
                    )
                else:
                    logger.warning("JobQueue is not available. Discuss button won't auto-delete after 24h.")

            except TelegramError as exc:
                logger.error("Failed to send discuss button: %s", exc)

        return

    if IGNORE_ADMINS and await _is_admin(update, context):
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
    app.add_handler(CallbackQueryHandler(handle_discuss, pattern=r"^discuss:"))

    message_filter = filters.ALL
    app.add_handler(MessageHandler(message_filter, moderate_topic))

    app.add_error_handler(on_error)

    logger.info(
        "Bot started | chat_id=%s | thread_id=%s | discussion_target=%s | ignore_admins=%s | ignore_bots=%s | enabled=%s",
        TARGET_CHAT_ID,
        TARGET_THREAD_ID,
        DISCUSSION_THREAD_ID if DISCUSSION_THREAD_ID is not None else "GENERAL",
        IGNORE_ADMINS,
        IGNORE_BOTS,
        BOT_ENABLED,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
