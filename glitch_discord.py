"""Agent-side helper for posting to Discord from Telegram-native agents.

Usage:
    from glitch_discord import post, post_embed
    post("grow-ads", "ROAS update: 3.2x")
    post_embed("grow-ads", title="Daily report", description="...", color=0x2ECC71)

CLI:
    glitch-post <channel> "message"
    echo "message" | glitch-post grow-ads
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_PATH = Path("/home/support/.config/glitch-discord/env")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")
BASE = "https://discord.com/api/v10"

_channel_cache: dict[str, str] = {}


def _auth():
    if not TOKEN or not GUILD_ID:
        raise RuntimeError("DISCORD_BOT_TOKEN / DISCORD_GUILD_ID missing (env file not loaded?)")
    return {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}


def _resolve(channel: str) -> str:
    if channel.isdigit():
        return channel
    if channel in _channel_cache:
        return _channel_cache[channel]
    r = requests.get(f"{BASE}/guilds/{GUILD_ID}/channels", headers=_auth(), timeout=10)
    r.raise_for_status()
    for c in r.json():
        _channel_cache[c["name"]] = c["id"]
    if channel not in _channel_cache:
        raise ValueError(f"channel not found: {channel}")
    return _channel_cache[channel]


def post(channel: str, content: str) -> dict:
    """Post a plain text message to a channel (by name or id)."""
    cid = _resolve(channel)
    r = requests.post(
        f"{BASE}/channels/{cid}/messages",
        headers=_auth(),
        data=json.dumps({"content": content[:2000]}),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def post_embed(channel: str, title: str, description: str = "", color: int = 0x5865F2, **fields) -> dict:
    cid = _resolve(channel)
    embed = {"title": title[:256], "description": description[:4096], "color": color}
    if fields:
        embed["fields"] = [{"name": k, "value": str(v)[:1024], "inline": True} for k, v in fields.items()]
    r = requests.post(
        f"{BASE}/channels/{cid}/messages",
        headers=_auth(),
        data=json.dumps({"embeds": [embed]}),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: glitch-post <channel> [message]\n       echo msg | glitch-post <channel>")
    channel = sys.argv[1]
    content = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.read().strip()
    if not content:
        sys.exit("no message content")
    res = post(channel, content)
    print(res["id"])


if __name__ == "__main__":
    main()
