"""One-shot server bootstrap — idempotent.

Creates categories, channels, and roles for the Glitch server.
Safe to re-run: existing items are left alone.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path("/home/support/.config/glitch-discord/env"))

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]
BASE = "https://discord.com/api/v10"
H = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

# Channel type 0 = text, 4 = category
CATEGORY = 4
TEXT = 0

STRUCTURE = [
    ("📋 info", [
        ("welcome", "server rules, what the bots do"),
        ("announcements", "broadcast-only"),
    ]),
    ("🛠 ops", [
        ("command-center", "operator issues commands here"),
        ("bot-logs", "bot status, errors, deploys"),
        ("approvals", "tasks awaiting operator sign-off"),
    ]),
    ("📈 trade", [
        ("trade-general", "trade domain chatter"),
        ("trade-ouroboros", "glitch-ouroboros-snake-strategy"),
    ]),
    ("🌱 grow", [
        ("grow-general", "grow domain chatter"),
        ("grow-ads", "glitch-grow-ads-agent"),
        ("grow-social", "glitch-social-media-agent"),
        ("grow-seo", "glitch-seo"),
        ("grow-cod-confirm", "glitch-cod-confirm"),
    ]),
    ("🌐 edge", [
        ("edge-general", "edge domain chatter"),
        ("edge-site", "glitch-edge-site"),
    ]),
]

ROLES = [
    ("operator", 0xF1C40F, True),
    ("agent-trade", 0xE74C3C, True),
    ("agent-grow", 0x2ECC71, True),
    ("agent-edge", 0x3498DB, True),
    ("bot-grow-ads", 0x95A5A6, True),
    ("bot-grow-social", 0x95A5A6, True),
    ("bot-grow-seo", 0x95A5A6, True),
    ("bot-grow-cod-confirm", 0x95A5A6, True),
    ("bot-edge-site", 0x95A5A6, True),
    ("bot-trade-ouroboros", 0x95A5A6, True),
]


def api(method: str, path: str, **kw):
    r = requests.request(method, f"{BASE}{path}", headers=H, timeout=20, **kw)
    if r.status_code == 429:
        retry = r.json().get("retry_after", 1)
        print(f"  rate-limited, sleeping {retry}s")
        time.sleep(retry + 0.2)
        return api(method, path, **kw)
    if not r.ok:
        print(f"  !! {method} {path} -> {r.status_code} {r.text}")
        r.raise_for_status()
    return r.json() if r.content else {}


def main():
    print(f"Bootstrapping guild {GUILD_ID}")

    existing_channels = {c["name"]: c for c in api("GET", f"/guilds/{GUILD_ID}/channels")}
    existing_roles = {r["name"]: r for r in api("GET", f"/guilds/{GUILD_ID}/roles")}

    # Roles
    print("\n== roles ==")
    for name, color, hoist in ROLES:
        if name in existing_roles:
            print(f"  = {name}")
            continue
        api("POST", f"/guilds/{GUILD_ID}/roles", json={
            "name": name, "color": color, "hoist": hoist, "mentionable": True,
        })
        print(f"  + {name}")

    # Categories + channels
    print("\n== channels ==")
    for cat_name, channels in STRUCTURE:
        if cat_name in existing_channels and existing_channels[cat_name]["type"] == CATEGORY:
            cat_id = existing_channels[cat_name]["id"]
            print(f"  = {cat_name}")
        else:
            cat = api("POST", f"/guilds/{GUILD_ID}/channels", json={
                "name": cat_name, "type": CATEGORY,
            })
            cat_id = cat["id"]
            print(f"  + {cat_name}")

        for ch_name, topic in channels:
            if ch_name in existing_channels and existing_channels[ch_name]["type"] == TEXT:
                print(f"    = {ch_name}")
                continue
            api("POST", f"/guilds/{GUILD_ID}/channels", json={
                "name": ch_name, "type": TEXT, "parent_id": cat_id, "topic": topic,
            })
            print(f"    + {ch_name}")

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
