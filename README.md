# glitch-discord-bot

Agent operations control plane for the Glitch Discord server.

## Layout

- `bot.py` — discord.py client with guild slash commands
- `glitch-discord-bot.service` — systemd user unit
- Secrets live at `/home/support/.config/glitch-discord/env` (mode 600, not in git)

## Commands

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
