# Hashtag Inspector

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.10-26A5E4?logo=telegram&logoColor=white)](https://docs.python-telegram-bot.org/)
[![Telegram Bot API](https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![asyncio](https://img.shields.io/badge/asyncio-asynchronous-4B8BBE)](https://docs.python.org/3/library/asyncio.html)
[![Bothost](https://img.shields.io/badge/Deployment-Bothost-555555)](https://bothost.ru/)

Telegram bot for moderating a selected topic in a forum-enabled supergroup.

The project was created for a real Telegram community and is currently deployed and in use. Its behavior evolved from practical requirements and testing in the group.

## What it does

- moderates one configured Telegram topic;
- requires a hashtag in user posts and captions;
- can ignore administrators, bots and service messages;
- provides admin commands to enable, disable and check moderation;
- adds an **Discuss in GENERAL** action for image posts;
- can copy a selected post into a discussion topic;
- lets an administrator prepare, preview and publish a post through a private chat with the bot;
- automatically removes temporary messages and buttons.

## Stack

- Python 3.11+
- python-telegram-bot
- Telegram Bot API
- long polling
- environment variables for configuration
- Bothost for deployment

## Configuration

Copy `.env.example` to `.env` and provide your own values.

```env
BOT_TOKEN=your_bot_token_here
TARGET_CHAT_ID=-1000000000000
TARGET_THREAD_ID=12345
DISCUSSION_THREAD_ID=
LOG_LEVEL=INFO
LOG_ALL_MESSAGES=false
IGNORE_ADMINS=true
IGNORE_BOTS=true
```

Never commit a real bot token or `.env` file.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

The bot must have the required permissions in the target Telegram supergroup.

## Admin commands

- `/on` — enable moderation
- `/off` — disable moderation
- `/status` — show moderation status
- `/post` — open the post creation flow in a private chat

## Status

The bot is deployed and working. Further changes are made when practical needs appear during use.
