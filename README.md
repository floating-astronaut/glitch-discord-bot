# glitch-discord-bot

Agent operations control plane for the Glitch Discord server.

## What this does

Thin guild-side adapter so agent processes (`glitch-grow-ai-ads-agent`,
others) can stay transport-agnostic. For every channel in `AGENT_CHANNELS`:

1. **Inbound messages** are written as JSON to
   `/home/support/.glitch-discord/inbox/<channel>/<msg_id>.json`. The
   agent polls those JSONs, dispatches the contained `/cmd`, and posts
   the reply back via Discord REST.
2. **Button-click interactions** (custom_id starting with `act:`) are
   written to `/home/support/.glitch-discord/inbox/_interactions/`. The
   agent's shared resolver consumes them for HITL approve/reject of
   action proposals — same DB row whether the click came from Discord
   or Telegram (first-click-wins).

## Layout

- `bot.py` — discord.py client: guild slash commands + on_message inbox
  + on_interaction approval-button handler
- `glitch-discord-bot.service` — systemd unit
- Secrets live at `/home/support/.config/glitch-discord/env` (mode 600, not in git)

## Slash commands (guild)

| Command | Purpose |
| --- | --- |
| `/status` | Bot + latency + uptime |
| `/post` | Post a message to a channel |
| `/create-channel` | Create a text channel (optional category) |
| `/new-agent <name>` | Create category/channel/role for an agent |
| `/new-task <agent> <title>` | Open task thread with ✅/❌ approval reactions |
| `/done` | Archive the current thread |
| `/say <message>` | Post as the bot in the current channel |
| `/whoami` | Show invoking user's roles |

Reactions ✅ / ❌ on a `Task:` embed trigger approval/cancel notices.

The agent's own `/help`, `/insights`, `/meta_audit`, `/port_meta_to_tiktok`
etc. are NOT registered here as slash commands — they're plain
text-prefixed commands the agent's inbox consumer parses out of normal
channel messages.

## Run (foreground)

```bash
cd /home/support/glitch-discord-bot
.venv/bin/python bot.py
```

## Install as systemd user service

```bash
mkdir -p ~/.config/systemd/user
ln -sf /home/support/glitch-discord-bot/glitch-discord-bot.service \
       ~/.config/systemd/user/glitch-discord-bot.service
systemctl --user daemon-reload
systemctl --user enable --now glitch-discord-bot
journalctl --user -u glitch-discord-bot -f
```

If the box restarts without a login session, enable linger:

```bash
sudo loginctl enable-linger support
```
