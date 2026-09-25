# AGENTS.md

## Project

Hashtag Inspector is a working Telegram bot used to moderate a specific topic in a forum-enabled supergroup.

The project is intentionally small. Do not introduce frameworks, services or abstractions unless the current task requires them.

## Development workflow

Work in small, verifiable steps.

For every change:

1. Read the current implementation before proposing changes.
2. Restate the behavior being changed and keep the scope limited to it.
3. Do not modify unrelated working behavior.
4. Implement the smallest complete change.
5. Explain what must be checked manually in Telegram.
6. Stop after implementation and wait for the result of the manual check when the change affects bot behavior.
7. Treat a successful real-world check as the gate for the next development step.

Do not expand the task with optional features unless explicitly requested.

## Responsibilities

The human controls:

- product requirements;
- expected Telegram behavior;
- scope and priorities;
- acceptance of each implementation stage;
- production deployment decisions.

The coding agent assists with:

- implementation;
- Telegram Bot API and library usage;
- debugging;
- code review;
- proposing alternatives when there is a technical trade-off.

When requirements are ambiguous, ask rather than inventing product behavior.

## Safety

- Never place bot tokens or other secrets in source code, commits, examples, logs or documentation.
- Read secrets from environment variables.
- Keep `.env` ignored.
- Use only fake values in `.env.example`.
- Bot tokens and other credentials are secrets and must never be committed.\n- Chat IDs and thread IDs are configuration values, not credentials; they may be kept as documented defaults when they are intentionally public and do not grant access.
- Do not change deployment configuration unless explicitly requested.

## Current technical constraints

- Python 3.11+
- `python-telegram-bot`
- long polling
- configuration through environment variables
- moderation is intentionally limited to the configured chat/topic
- administrator permissions must be checked for management and publishing actions

## Change discipline

Before considering a task complete:

- check that the requested behavior is implemented;
- check that unrelated handlers and flows were not changed accidentally;
- check configuration and secrets;
- state the exact manual test needed;
- do not claim production behavior was verified unless it was actually tested.
