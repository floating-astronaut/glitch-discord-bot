"""Glitch Budz sales-agent HITL plugin for the agent control-plane bot.

This module slots into the existing `glitch-discord-bot` so we don't
spin up a second Discord application + token. It:

1. Loads the sales-agent .env so `sales_agent.config.settings` resolves.
2. On bot ready, opens the asyncpg pool against `sales_agent.*` and starts
   a polling task.
3. Polling task: every POLL_INTERVAL_S seconds, scans `email_drafts` for
   approval_state='pending' AND discord_message_id IS NULL, posts the
   draft as an embed in the configured channel, attaches the resulting
   (channel_id, message_id) back to the row.
4. Reaction handler: ✅/❌/🖊️ on a draft embed → mark_approved /
   mark_rejected / mark_edit_requested in the DB, then edits the embed
   in place to show the new state + approver.

The plugin is intentionally additive: the host bot's own /new-task and
Task: embed flow keep working untouched. Reactions on Task: embeds go
to the host's existing handler; reactions on draft embeds (detected via
the embed footer prefix `draft `) go here.

Configuration (from /home/support/.config/glitch-discord/env, loaded by
host bot):

  SALES_AGENT_CHANNEL_NAME    Channel name to post drafts in.
                              Defaults to "grow-sales".
  (also: bot's existing DISCORD_GUILD_ID + bot token + admin user list)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load sales-agent .env BEFORE importing sales_agent.config so settings
# can pick up POSTGRES_RW_URL. Idempotent if already loaded.
_SALES_AGENT_DOTENV = Path("/home/support/glitch-grow-sales-agent/.env")
load_dotenv(_SALES_AGENT_DOTENV)

import discord  # noqa: E402

import json  # noqa: E402

from sales_agent.config import settings as sa_settings  # noqa: E402
from sales_agent.db import DraftRepo, LeadRepo, pool  # noqa: E402
from sales_agent.discord.auth import actor  # noqa: E402
from sales_agent.discord.formatter import draft_embed  # noqa: E402


# Admin user-id resolution: prefer the host bot's existing approver list
# (DISCORD_APPROVER_USER_IDS_JSON, JSON array of int), fall back to
# sales_agent's own DISCORD_ADMIN_USER_IDS (comma-separated). Single
# source of truth = the host bot's config.
def _resolve_admin_ids() -> list[int]:
    raw = os.environ.get("DISCORD_APPROVER_USER_IDS_JSON", "").strip()
    if raw:
        try:
            ids = json.loads(raw)
            return [int(x) for x in ids]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return sa_settings.admin_user_id_list


_ADMIN_IDS = _resolve_admin_ids()


def is_admin(user_id: int) -> bool:
    return user_id in _ADMIN_IDS

logger = logging.getLogger("sales-agent-plugin")

POLL_INTERVAL_S = 30
REACTION_APPROVE = "✅"
REACTION_REJECT = "❌"
REACTION_EDIT = "🖊️"
RECOGNISED_REACTIONS = (REACTION_APPROVE, REACTION_REJECT, REACTION_EDIT)

DEFAULT_CHANNEL_NAME = os.environ.get("SALES_AGENT_CHANNEL_NAME", "approvals")
EMBED_FOOTER_PREFIX = "draft "  # set by sales_agent.discord.formatter


# Process-wide state populated by setup() — tied to the host bot.
class _State:
    bot: discord.Client | None = None
    channel: discord.abc.GuildChannel | None = None
    poll_task: asyncio.Task | None = None
    db_ready: bool = False


_state = _State()


# ─── Setup hooks called from the host bot ────────────────────────────────────


async def setup(bot: discord.Client) -> None:
    """Called from the host bot's on_ready (or setup_hook). Idempotent."""
    _state.bot = bot

    if not sa_settings.postgres_rw_url:
        logger.warning("postgres_rw_url not set — sales-agent plugin disabled")
        return

    if not _state.db_ready:
        await pool.connect(min_size=1, max_size=4)
        _state.db_ready = True
        logger.info("db pool connected")

    _state.channel = await _resolve_channel(bot)
    if _state.channel is None:
        logger.warning(
            "channel %r not found in guild — plugin will retry on next setup",
            DEFAULT_CHANNEL_NAME,
        )
        return

    if _state.poll_task is None or _state.poll_task.done():
        _state.poll_task = asyncio.create_task(_poll_loop(bot))
        logger.info("polling task started → channel #%s", _state.channel.name)


async def teardown() -> None:
    if _state.poll_task and not _state.poll_task.done():
        _state.poll_task.cancel()
    if _state.db_ready:
        await pool.disconnect()
        _state.db_ready = False


async def handle_reaction(payload: discord.RawReactionActionEvent) -> bool:
    """Hook called from the host bot's on_raw_reaction_add.

    Returns True if the reaction was for a draft embed (the host should
    skip its own Task: branch), False otherwise.
    """
    if not _state.db_ready or _state.channel is None:
        return False
    if _state.bot is None or _state.bot.user is None:
        return False
    if payload.user_id == _state.bot.user.id:
        return False
    if payload.channel_id != _state.channel.id:
        return False

    emoji = str(payload.emoji)
    if emoji not in RECOGNISED_REACTIONS:
        return False

    draft_repo = DraftRepo(pool.pool())
    lead_repo = LeadRepo(pool.pool())
    draft = await draft_repo.by_discord_message(payload.message_id)
    if draft is None:
        return False  # not one of ours

    if not is_admin(payload.user_id):
        logger.info("ignored reaction from non-admin %s", payload.user_id)
        return True  # consumed (don't double-handle)
    if draft.approval_state != "pending":
        return True  # already resolved

    approver = actor(payload.user_id)
    if emoji == REACTION_APPROVE:
        await draft_repo.mark_approved(draft.id, approver=approver)
        new_state = "approved"
    elif emoji == REACTION_REJECT:
        await draft_repo.mark_rejected(draft.id, approver=approver)
        new_state = "rejected"
    else:
        await draft_repo.mark_edit_requested(
            draft.id, approver=approver,
            edit_request="(operator requested edit; reply to embed)",
        )
        new_state = "edited"

    fresh = await draft_repo.get(draft.id)
    lead = await lead_repo.get(draft.lead_id)
    if fresh and lead and _state.channel:
        try:
            msg = await _state.channel.fetch_message(payload.message_id)
            await msg.edit(embed=draft_embed(fresh, lead))
        except Exception:
            logger.exception("failed to edit embed after reaction")

    logger.info("draft %s → %s by %s", draft.id, new_state, approver)
    return True


# ─── Internals ───────────────────────────────────────────────────────────────


async def _resolve_channel(bot: discord.Client) -> discord.abc.GuildChannel | None:
    guild_id = int(os.environ.get("DISCORD_GUILD_ID", 0) or 0)
    guild = bot.get_guild(guild_id) if guild_id else None
    if guild is None:
        for g in bot.guilds:
            guild = g
            break
    if guild is None:
        return None
    for ch in guild.text_channels:
        if ch.name == DEFAULT_CHANNEL_NAME:
            return ch
    return None


async def _poll_loop(bot: discord.Client) -> None:
    while not bot.is_closed():
        try:
            await _post_pending()
        except Exception:
            logger.exception("poll iteration failed")
        await asyncio.sleep(POLL_INTERVAL_S)


async def _post_pending() -> None:
    if _state.channel is None:
        # Channel may have been created after setup; re-resolve.
        if _state.bot is not None:
            _state.channel = await _resolve_channel(_state.bot)
        if _state.channel is None:
            return

    draft_repo = DraftRepo(pool.pool())
    lead_repo = LeadRepo(pool.pool())
    pending = await draft_repo.pending(limit=50)
    fresh = [d for d in pending if d.discord_message_id is None]
    if not fresh:
        return

    logger.info("posting %d new drafts", len(fresh))
    for d in fresh:
        lead = await lead_repo.get(d.lead_id)
        if lead is None:
            continue
        try:
            msg = await _state.channel.send(embed=draft_embed(d, lead))
            for emoji in RECOGNISED_REACTIONS:
                await msg.add_reaction(emoji)
            await draft_repo.attach_discord(
                d.id, channel_id=msg.channel.id, message_id=msg.id,
            )
        except Exception:
            logger.exception("failed to post draft %s", d.id)
