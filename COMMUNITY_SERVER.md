# Community Discord — setup + operator runbook

The new community Discord server (the one at https://discord.gg/HBZFKMts)
serves both **free Vibe Kit users** and **paid agent buyers** with
role-gated channels. Same bot as the HITL/operator server (one Discord
application, two guilds) — but the community-server behaviour is strictly
scoped via `COMMUNITY_GUILD_ID` env var so it never interferes with
agent HITL.

## Architecture

| Channel | Visibility | Purpose |
|---|---|---|
| 📋 **info** | | |
| `#welcome` | Public | Server intro, rules |
| `#announcements` | Public, read-only | Glitch Grow news, kit drops |
| 🌱 **free-kit** | | |
| `#vibe-kit-help` | Public | Free-kit install + Claude Code / Codex / OpenClaws debugging |
| `#showcase` | Public | Buyers post what they built |
| `#feedback` | Public | Bugs, feature requests, friction reports |
| 🔒 **paid** | Paid roles only | Agent + Founder Stack buyers see |
| `#agent-support` | Paid | Paid-tier product support |
| `#paid-announcements` | Paid | Product updates, deploy notes, new SKU drops |
| ⭐ **founder-stack** | Founder Stack only | Bundle exclusive |
| `#founder-stack` | Founder Stack | 1:1 calls + roundtables |

| Role | Color | Granted when |
|---|---|---|
| `@Operator` | gold | manually (you) |
| `@Founder Stack Buyer` | brand-green | bundle purchase |
| `@Agent Buyer` | brand-cyan | individual SKU purchase |
| `@Free Kit User` | gray | on join (auto by bot) |

## First-time setup (~10 min, do once)

### 1. Enable the privileged Server Members intent

The bot needs to receive `on_member_join` events. Discord requires this
to be explicitly enabled per-application:

1. Open https://discord.com/developers/applications
2. Pick the existing Glitch Discord bot application
3. Bot → Privileged Gateway Intents
4. Toggle **Server Members Intent** → **ON**
5. Save changes

(If `Message Content Intent` is also off, leave that as-is — the HITL
bot already needs it on; nothing to change.)

### 2. Get the community-server guild ID

1. Discord → User Settings → Advanced → toggle **Developer Mode** ON
2. Right-click the new community server in your server list → **Copy Server ID**

### 3. Add the env var

Edit `/home/support/.config/glitch-discord/env` and add a new line:

```
COMMUNITY_GUILD_ID=<the ID you copied>
```

(File is mode 600. Don't echo the bot token in any pasted command.)

### 4. Provision channels + roles

```bash
cd /home/support/glitch-discord-bot
.venv/bin/python community_bootstrap.py
```

Idempotent. Re-runnable any time you want to add channels. Output
prints `+` for created, `=` for already-existed.

### 5. Give the bot Manage Roles + raise it above buyer roles

The bot was invited with `permissions=379969` which deliberately
**excluded** `Manage Roles` (admin-grade). To enable auto-role-on-join:

1. Discord → New community server → Server Settings → Roles
2. Find the bot's auto-created role (named after the bot)
3. Edit → toggle **Manage Roles** ON → Save
4. Drag the bot's role **above** `@Founder Stack Buyer` in the role list
   (Discord rule: a role can only assign roles below itself in the hierarchy)
5. Drag `@Operator` to the very TOP of the list, then drop yourself in it

### 6. Restart the bot service so it picks up COMMUNITY_GUILD_ID

```bash
sudo systemctl restart glitch-discord-bot
journalctl -u glitch-discord-bot -n 50 --no-pager
```

Look for the line `[community] handlers registered for guild <ID>` —
that confirms the community.py module loaded.

### 7. Test the join flow

1. Use a second Discord account (or ask a friend) to click your invite
   link (`https://discord.gg/HBZFKMts`)
2. Within ~2 seconds of join, the bot should:
   - Assign the `@Free Kit User` role (visible in their member sidebar)
   - DM them the welcome message
3. Verify they CAN see #welcome / #vibe-kit-help / #showcase / #feedback
4. Verify they CAN'T see #agent-support / #paid-announcements / #founder-stack

If any step fails, `journalctl -u glitch-discord-bot -f` shows what went
wrong (most common: missing Manage Roles perm → `[community] missing
perms to assign role`).

## Granting paid roles after a purchase (manual for now)

For the first ~10 buyers, manual role grants are fine. After someone
buys an agent or the Founder Stack, you grant them the role in Discord:

1. Look up the Razorpay/Stripe payment in your dashboard, get the
   buyer's email
2. Find them in the Discord member list (Server Settings → Members,
   sort by recently joined)
3. Right-click their name → **Roles** → tick `Agent Buyer` (or
   `Founder Stack Buyer` for bundle purchases)
4. They immediately see the paid channels

The bot's existing `Operator` permissions let you do this through the
Discord UI without any bot command. Phase 2 (after first 10 sales)
will add a webhook from `/api/razorpay/verify-payment` and the Stripe
webhook to a new `/api/discord/grant-role` endpoint that auto-grants
based on the buyer's stated Discord handle (collected during checkout).

## Adding new channels later

Edit `STRUCTURE` in `community_bootstrap.py`, then re-run:

```bash
cd /home/support/glitch-discord-bot
.venv/bin/python community_bootstrap.py
```

Existing channels are left alone; new ones are created with the right
permission overwrites baked in.

## Troubleshooting

**Bot doesn't appear online in the new server**
Make sure `glitch-discord-bot.service` is running:
`sudo systemctl status glitch-discord-bot` — if not, `sudo systemctl
start glitch-discord-bot`.

**`on_member_join` never fires**
Privileged Members intent isn't enabled in the Developer Portal.
See step 1 above.

**Role assignment fails with "missing perms"**
Bot's role doesn't have Manage Roles, or sits below the role you're
trying to assign in the hierarchy. See step 5 above.

**Welcome DM never arrives**
The new member has DMs from server members closed (Discord's privacy
default). Bot logs `[community] <member> has DMs closed`. They'll see
the welcome via the welcome screen instead.

**Bot replies in HITL channels**
Shouldn't happen — community.py guards every handler with
`if member.guild.id != COMMUNITY_GUILD_ID: return`. If it's misbehaving
on the HITL guild, check `journalctl` for the exact misroute.
