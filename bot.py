"""Glitch Discord bot — agent operations control plane.

Commands (guild slash commands, synced on ready):
  /status                          Bot + latency + uptime
  /post <channel> <message>        Post a message to a channel
  /create-channel <name> [category] Create a text channel
  /new-agent <name>                Create a category + channel + role for an agent
  /new-task <agent> <title>        Open a task thread in the agent's channel with approval reactions
  /done                            Archive the current thread (must be run inside a thread)
  /say <message>                   Post as the bot in the current channel
  /whoami                          Show the invoking user's roles (debug)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

CONFIG_PATH = Path("/home/support/.config/glitch-discord/env")
load_dotenv(CONFIG_PATH)

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
GUILD = discord.Object(id=GUILD_ID)

AGENT_CATEGORY_NAME = "agents"

# Inbox: messages to agent channels land as JSON files here
INBOX_ROOT = Path("/home/support/.glitch-discord/inbox")
INBOX_ROOT.mkdir(parents=True, exist_ok=True)

# Only these channels are wired for inbox delivery (bot channels, not info/ops/general)
AGENT_CHANNELS = {
    "trade-ouroboros",
    "grow-ads", "grow-social", "grow-seo", "grow-cod-confirm",
    "edge-site",
    # Client-facing channels — same dispatch path, but shared with the client
    "glitch-x-ayurpet",
    "glitch-x-urban",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("glitch-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class GlitchBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.started_at = time.time()

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD)
        synced = await self.tree.sync(guild=GUILD)
        log.info("Synced %d slash commands to guild %s", len(synced), GUILD_ID)


bot = GlitchBot()


# ---------- helpers ----------


async def ensure_agent_category(guild: discord.Guild) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name.lower() == AGENT_CATEGORY_NAME:
            return cat
    return await guild.create_category(AGENT_CATEGORY_NAME, reason="glitch-bot: agent home")


def fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h{m:02d}m{s:02d}s"


# ---------- commands ----------


@bot.tree.command(description="Bot health + uptime")
async def status(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    uptime = fmt_uptime(time.time() - bot.started_at)
    embed = discord.Embed(title="Glitch bot status", color=0x00D26A)
    embed.add_field(name="Latency", value=f"{latency_ms} ms")
    embed.add_field(name="Uptime", value=uptime)
    embed.add_field(name="Guild", value=interaction.guild.name if interaction.guild else "-")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(description="Post a message to a channel")
@app_commands.describe(channel="Target channel", message="Message body")
async def post(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    await channel.send(message)
    await interaction.response.send_message(f"Posted to {channel.mention}", ephemeral=True)


@bot.tree.command(name="create-channel", description="Create a text channel")
@app_commands.describe(name="Channel name", category="Optional category")
async def create_channel(
    interaction: discord.Interaction,
    name: str,
    category: discord.CategoryChannel | None = None,
):
    ch = await interaction.guild.create_text_channel(name=name, category=category)
    await interaction.response.send_message(f"Created {ch.mention}", ephemeral=True)


@bot.tree.command(name="new-agent", description="Create a channel + role for a new agent")
@app_commands.describe(name="Agent name (e.g. ads, seo, research)")
async def new_agent(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    slug = name.lower().replace(" ", "-")
    category = await ensure_agent_category(guild)

    existing_role = discord.utils.get(guild.roles, name=f"agent-{slug}")
    role = existing_role or await guild.create_role(
        name=f"agent-{slug}", mentionable=True, reason="glitch-bot: new agent"
    )

    existing_ch = discord.utils.get(category.channels, name=slug)
    channel = existing_ch or await guild.create_text_channel(name=slug, category=category)

    embed = discord.Embed(
        title=f"Agent onboarded: {slug}",
        description=f"Channel: {channel.mention}\nRole: {role.mention}",
        color=0x5865F2,
    )
    await channel.send(embed=embed)
    await interaction.followup.send(
        f"Agent `{slug}` ready — {channel.mention}, role {role.mention}", ephemeral=True
    )


@bot.tree.command(name="new-task", description="Open a task thread in an agent's channel")
@app_commands.describe(agent="Agent channel", title="Task title")
async def new_task(
    interaction: discord.Interaction,
    agent: discord.TextChannel,
    title: str,
):
    await interaction.response.defer(ephemeral=True, thinking=True)
    starter = await agent.send(
        embed=discord.Embed(
            title=f"Task: {title}",
            description=(
                f"Opened by {interaction.user.mention}\n"
                "React ✅ to approve, ❌ to cancel."
            ),
            color=0xFEE75C,
        )
    )
    thread = await starter.create_thread(name=title[:90], auto_archive_duration=1440)
    for emoji in ("✅", "❌"):
        await starter.add_reaction(emoji)
    await interaction.followup.send(f"Thread opened: {thread.mention}", ephemeral=True)


@bot.tree.command(description="Archive the current thread")
async def done(interaction: discord.Interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.Thread):
        await interaction.response.send_message(
            "Run this inside a thread.", ephemeral=True
        )
        return
    await interaction.response.send_message("Archiving thread.", ephemeral=True)
    await ch.edit(archived=True, locked=False, reason=f"/done by {interaction.user}")


@bot.tree.command(description="Post as the bot in the current channel")
@app_commands.describe(message="Message body")
async def say(interaction: discord.Interaction, message: str):
    await interaction.channel.send(message)
    await interaction.response.send_message("Sent.", ephemeral=True)


@bot.tree.command(description="Show your roles (debug)")
async def whoami(interaction: discord.Interaction):
    roles = ", ".join(r.name for r in interaction.user.roles if r.name != "@everyone") or "—"
    await interaction.response.send_message(f"Roles: {roles}", ephemeral=True)


# ---------- events ----------


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return
    parent = message.channel.parent if isinstance(message.channel, discord.Thread) else message.channel
    if parent.name not in AGENT_CHANNELS:
        return

    inbox = INBOX_ROOT / parent.name
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": str(message.id),
        "channel": parent.name,
        "channel_id": str(parent.id),
        "thread_id": str(message.channel.id) if isinstance(message.channel, discord.Thread) else None,
        "author": {"id": str(message.author.id), "name": message.author.name},
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "timestamp": message.created_at.isoformat(),
    }
    path = inbox / f"{int(message.created_at.timestamp()*1000)}-{message.id}.json"
    path.write_text(json.dumps(payload, indent=2))
    log.info("inbox: %s <- %s: %s", parent.name, message.author.name, message.content[:80])
    try:
        await message.add_reaction("📥")
    except discord.HTTPException:
        pass


@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s) in %d guild(s)", bot.user, bot.user.id, len(bot.guilds))
    # Wire the Glitch Budz sales-agent HITL plugin if installed in this venv.
    try:
        import sales_agent_integration as sa_plugin
        await sa_plugin.setup(bot)
    except ImportError:
        log.info("sales_agent_integration not installed — skipping plugin")
    except Exception:
        log.exception("sales_agent_integration setup failed")


# ----- Action approval interactions ----------------------------------------
# Button clicks on agent-action proposals (custom_id starts with "act:")
# are written here as JSON files for the ads-agent consumer to process.
# We ack the interaction with a deferred-ephemeral response so Discord is
# happy within the 3s deadline; the consumer handles the real DB work
# + the eventual message edit.

INTERACTIONS_DIR = Path("/home/support/.glitch-discord/inbox/_interactions")
INTERACTIONS_DIR.mkdir(parents=True, exist_ok=True)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Only handle component interactions whose custom_id is one of ours.
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id", "") if interaction.data else ""
    if not custom_id.startswith("act:"):
        return

    # Defer ephemerally — gives the consumer up to 15 minutes to follow up;
    # we won't actually follow up via the interaction, the consumer will
    # edit the original message directly. The defer is purely so Discord
    # doesn't show "interaction failed" to the clicker.
    try:
        await interaction.response.defer(ephemeral=True, thinking=False)
    except discord.errors.InteractionResponded:
        pass
    except Exception as e:
        log.warning("interaction defer failed: %s", e)

    user = interaction.user
    payload = {
        "interaction_id": str(interaction.id),
        "custom_id": custom_id,
        "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
        "guild_id":   str(interaction.guild_id)   if interaction.guild_id else None,
        "message_id": str(interaction.message.id) if interaction.message else None,
        "user_id":    str(user.id) if user else None,
        "user_name":  (user.name if user else None),
        "user_display": (user.display_name if hasattr(user, "display_name") else None),
        "timestamp":  interaction.created_at.isoformat() if interaction.created_at else None,
    }
    path = INTERACTIONS_DIR / f"{int(time.time()*1000)}-{interaction.id}.json"
    path.write_text(json.dumps(payload, indent=2))
    log.info(
        "interaction: %s clicked %s on msg %s → %s",
        payload["user_name"], custom_id, payload["message_id"], path.name,
    )


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    # First, give the sales-agent plugin a chance to consume the reaction
    # (it owns the embeds whose footer starts with `draft `). Returns True
    # if it handled it; in that case we skip the host's Task: branch.
    try:
        import sales_agent_integration as sa_plugin
        if await sa_plugin.handle_reaction(payload):
            return
    except ImportError:
        pass
    except Exception:
        log.exception("sales_agent_integration.handle_reaction failed")

    if str(payload.emoji) not in ("✅", "❌"):
        return
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return
    if not (msg.author.id == bot.user.id and msg.embeds and msg.embeds[0].title and msg.embeds[0].title.startswith("Task:")):
        return
    verdict = "approved ✅" if str(payload.emoji) == "✅" else "cancelled ❌"
    await channel.send(f"Task **{msg.embeds[0].title[6:]}** {verdict} by <@{payload.user_id}>")


def main():
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
