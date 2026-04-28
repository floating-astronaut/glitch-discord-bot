"""glitch-social-media-agent HITL plugin for the host control-plane bot.

Mirrors the existing sales_agent_integration.py shape — slots into the
host bot so we don't run a second Discord client.

Responsibilities:
1. Load glitch-social-media-agent's .env so its `glitch_signal.config`
   settings resolve (database URL, Fernet key, channel id).
2. On bot ready, open the asyncpg pool against the agent's database,
   start a polling task.
3. Polling task — every POLL_INTERVAL_S seconds:
     - Find CommentReply rows with status='pending_approval' AND
       discord_message_id IS NULL → no embed posted yet (sweeper can
       also post directly via REST; this is a backstop).
4. Reaction handler — listen for ✅ / ❌ on messages whose footer starts
   with `comment_reply `. Approve or veto by calling the agent's
   sweeper.approve_reply / sweeper.veto_reply functions in-process.

Approval reactions go through the host bot's existing approver-id
allowlist (DISCORD_APPROVER_USER_IDS_JSON). No second auth surface.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load the agent's .env BEFORE importing glitch_signal so settings()
# picks up SIGNAL_DB_URL, AUTH_ENCRYPTION_KEY, etc. Idempotent.
_AGENT_DOTENV = Path("/home/support/glitch-social-media-agent/.env")
load_dotenv(_AGENT_DOTENV)

import discord  # noqa: E402

# Add the agent repo's src to sys.path so we can import glitch_signal.*
import sys  # noqa: E402

_AGENT_SRC = Path("/home/support/glitch-social-media-agent/src")
if _AGENT_SRC.is_dir() and str(_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENT_SRC))

from glitch_signal.comments import sweeper  # noqa: E402
from glitch_signal.db.models import CommentReply  # noqa: E402
from glitch_signal.db.session import _session_factory  # noqa: E402
from glitch_signal.discord.auth import is_approver  # noqa: E402
from glitch_signal.discord.formatter import comment_reply_embed  # noqa: E402
from sqlmodel import select  # noqa: E402

logger = logging.getLogger("social-media-agent-plugin")

POLL_INTERVAL_S = 30
EMOJI_APPROVE = "✅"
EMOJI_REJECT = "❌"

CHANNEL_ID = (os.environ.get("SOCIAL_MEDIA_AGENT_CHANNEL_ID") or "").strip()


# ─── State ───────────────────────────────────────────────────────────────────


class _State:
    bot: discord.Client | None = None
    poll_task: asyncio.Task | None = None


_state = _State()


# ─── Setup hook ──────────────────────────────────────────────────────────────


async def setup(bot: discord.Client) -> None:
    """Called from the host bot's setup_hook / on_ready. Idempotent."""
    _state.bot = bot
    if not CHANNEL_ID:
        logger.warning("SOCIAL_MEDIA_AGENT_CHANNEL_ID not set — plugin disabled")
        return

    if _state.poll_task is None:
        _state.poll_task = asyncio.create_task(_polling_loop())
        logger.info("polling task started, interval=%ds", POLL_INTERVAL_S)


async def teardown() -> None:
    if _state.poll_task and not _state.poll_task.done():
        _state.poll_task.cancel()
        try:
            await _state.poll_task
        except asyncio.CancelledError:
            pass


# ─── Polling: post embed for any pending row that doesn't have one ───────────


async def _polling_loop() -> None:
    while True:
        try:
            await _scan_once()
        except Exception:
            logger.exception("scan failed")
        await asyncio.sleep(POLL_INTERVAL_S)


async def _scan_once() -> None:
    factory = _session_factory()
    async with factory() as session:
        result = await session.execute(
            select(CommentReply).where(
                CommentReply.status == "pending_approval",
                CommentReply.discord_message_id.is_(None),
            ).limit(20)
        )
        rows = list(result.scalars().all())

    for row in rows:
        await _post_embed_for(row)


async def _post_embed_for(row: CommentReply) -> None:
    bot = _state.bot
    assert bot is not None
    channel = bot.get_channel(int(CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(CHANNEL_ID))
        except discord.NotFound:
            logger.warning("channel %s not found", CHANNEL_ID)
            return

    embed = discord.Embed.from_dict(
        comment_reply_embed(row, state_override="pending_approval")
    )
    msg = await channel.send(embed=embed)
    for emoji in (EMOJI_APPROVE, EMOJI_REJECT):
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            pass

    factory = _session_factory()
    async with factory() as session:
        stored = await session.get(CommentReply, row.id)
        if stored:
            stored.discord_message_id = str(msg.id)
            stored.discord_channel_id = str(msg.channel.id)
            session.add(stored)
            await session.commit()


# ─── Reactions: approve / reject ─────────────────────────────────────────────


async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Hook the host bot wires into its on_raw_reaction_add. Filters to
    embeds we own (footer prefix = `comment_reply `)."""
    bot = _state.bot
    if bot is None or payload.guild_id is None:
        return
    if payload.user_id == bot.user.id:
        return  # ignore the bot's own seed reactions
    if not is_approver(payload.user_id):
        return
    if str(payload.emoji) not in (EMOJI_APPROVE, EMOJI_REJECT):
        return

    # Find the row this message points to.
    factory = _session_factory()
    async with factory() as session:
        result = await session.execute(
            select(CommentReply).where(
                CommentReply.discord_message_id == str(payload.message_id),
            ).limit(1)
        )
        row = result.scalar_one_or_none()
    if not row:
        return

    if str(payload.emoji) == EMOJI_APPROVE:
        ok, msg = await sweeper.approve_reply(row.id)
        new_state = "posted" if ok else "failed"
    else:
        ok, msg = await sweeper.veto_reply(row.id)
        new_state = "ignored" if ok else "failed"

    # Update embed in place to reflect terminal state.
    try:
        channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        async with factory() as session:
            updated = await session.get(CommentReply, row.id)
        embed = discord.Embed.from_dict(
            comment_reply_embed(updated or row, state_override=new_state)
        )
        await message.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden):
        pass
    logger.info(
        "reaction handled: row=%s state=%s ok=%s msg=%s",
        row.id, new_state, ok, msg,
    )
