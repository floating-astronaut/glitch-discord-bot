"""Community-server handlers — additive to bot.py, scoped strictly to the
new community guild (discord.gg/HBZFKMts) so the existing HITL/operator
behaviour on the original guild is unaffected.

Activated only when COMMUNITY_GUILD_ID is set in the env. Otherwise this
module's setup() is a no-op.

Behaviours wired here (v1):
  • on_member_join → assign @Free Kit User role + send welcome DM
                    pointing at #vibe-kit-help and the agents storefront.

Future (v2, intentionally out of scope for this turn):
  • Slash command `/grant-paid` for manual paid-role grant by Operator
  • HTTP-triggered role grant after Razorpay/Stripe payment-verified webhook
  • Per-purchase DM with kit GitHub link + private-repo collaborator instructions
"""

from __future__ import annotations

import logging
import os

import discord

log = logging.getLogger("glitch-bot.community")

# Optional. If unset, setup() is a no-op so the bot keeps running for HITL
# without trying to handle a guild that doesn't exist for this bot.
_COMMUNITY_GUILD_ID = os.environ.get("COMMUNITY_GUILD_ID")
COMMUNITY_GUILD_ID: int | None = int(_COMMUNITY_GUILD_ID) if _COMMUNITY_GUILD_ID else None

# Role + channel names match what community_bootstrap.py provisions.
FREE_KIT_ROLE_NAME = "Free Kit User"
WELCOME_CHANNEL_NAME = "vibe-kit-help"
SHOWCASE_CHANNEL_NAME = "showcase"

# The promotional message that goes out as a DM. Plain Markdown — keeps
# rendering predictable across mobile + desktop Discord clients.
WELCOME_DM = """\
Hey {name}, welcome to **Glitch Grow** 👋

You're in the free tier — that means you've got the **Vibe Coder Kit** and access to:
• `#vibe-kit-help` — install + Claude Code / Codex / OpenClaws debugging
• `#showcase` — drop screenshots of what you've built
• `#feedback` — bugs, suggestions, friction reports

When you're ready to ship something real for clients, the agents storefront is at
**<https://grow.glitchexecutor.com/#agents>** — six production AI agents you can
deploy on your own infra and resell at ₹25K–₹50K/mo (or $1.5K–$3K/mo globally).

Buying any agent unlocks `#agent-support` (paid-tier product help) and
`#paid-announcements` (deploy notes + new SKU drops). The Founder Stack
bundle adds an exclusive `#founder-stack` channel + a 1:1 architecture call.

— Tejas
"""


async def assign_free_role_and_dm(member: discord.Member) -> None:
    """Assign the Free Kit User role and DM the buyer the welcome message.

    Failures are logged but never raised — a flaky DM (closed DMs, banned
    member) shouldn't block the join.
    """
    guild = member.guild
    role = discord.utils.get(guild.roles, name=FREE_KIT_ROLE_NAME)
    if role is None:
        log.warning(
            "[community] %s role missing on guild %s — run community_bootstrap.py first",
            FREE_KIT_ROLE_NAME, guild.id,
        )
    else:
        try:
            await member.add_roles(role, reason="auto-assigned on join via community.py")
            log.info("[community] assigned %s to %s (%s)", role.name, member, member.id)
        except discord.Forbidden:
            log.warning("[community] missing perms to assign role %s to %s", role.name, member)
        except discord.HTTPException as e:
            log.warning("[community] role-assign failed for %s: %s", member, e)

    # DM welcome — best-effort
    try:
        await member.send(WELCOME_DM.format(name=member.display_name or member.name))
        log.info("[community] welcome DM sent to %s", member)
    except discord.Forbidden:
        # User has closed DMs; not an error worth alarming on.
        log.info("[community] %s has DMs closed; welcome message skipped", member)
    except discord.HTTPException as e:
        log.warning("[community] welcome DM failed for %s: %s", member, e)


def setup(bot: discord.Client) -> None:
    """Register the community-guild handlers on the given bot.

    Called from bot.py after the bot instance is created. Idempotent —
    if COMMUNITY_GUILD_ID isn't set, this is a no-op so the bot keeps
    serving the HITL/operator guild without touching anything new.
    """
    if COMMUNITY_GUILD_ID is None:
        log.info("[community] COMMUNITY_GUILD_ID not set; community handlers disabled")
        return

    @bot.event
    async def on_member_join(member: discord.Member) -> None:
        # Strictly scope to the community guild — never act on members of
        # the HITL/operator guild.
        if member.guild.id != COMMUNITY_GUILD_ID:
            return
        if member.bot:
            return
        log.info("[community] new member %s (%s) joined community guild", member, member.id)
        await assign_free_role_and_dm(member)

    log.info("[community] handlers registered for guild %s", COMMUNITY_GUILD_ID)
