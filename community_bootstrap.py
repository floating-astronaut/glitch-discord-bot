"""One-shot community-server bootstrap — idempotent.

Provisions the new community Discord server (the one at discord.gg/HBZFKMts)
with the channel/role architecture for free-kit users and paid agent
buyers. Mirrors the pattern of the existing bootstrap.py but targets a
DIFFERENT guild — leaves the original HITL/operator server untouched.

Run after Tejas adds the bot to the community server AND adds
COMMUNITY_GUILD_ID to /home/support/.config/glitch-discord/env:

    python community_bootstrap.py

Safe to re-run: existing channels/roles are left alone; only missing
items are created. Re-run after editing STRUCTURE / ROLES below to add
new channels.

Env required:
  DISCORD_BOT_TOKEN     — same token already wired (one bot, two guilds)
  COMMUNITY_GUILD_ID    — new server's guild ID (right-click server in
                          Discord with Developer Mode on → Copy Server ID)
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
try:
    GUILD_ID = os.environ["COMMUNITY_GUILD_ID"]
except KeyError:
    print("ERROR: COMMUNITY_GUILD_ID not set in /home/support/.config/glitch-discord/env", file=sys.stderr)
    print("       Get it by right-clicking the new server in Discord (Developer Mode → Copy Server ID).", file=sys.stderr)
    sys.exit(1)

BASE = "https://discord.com/api/v10"
H = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}

CATEGORY = 4
TEXT = 0

# Discord permission integer constants (least-significant flags as documented
# at https://discord.com/developers/docs/topics/permissions)
PERM_VIEW_CHANNEL = 1 << 10           # 1024
PERM_SEND_MESSAGES = 1 << 11          # 2048
PERM_READ_MESSAGE_HISTORY = 1 << 16   # 65536
PERM_ADD_REACTIONS = 1 << 6           # 64
PERM_EMBED_LINKS = 1 << 14            # 16384
PERM_ATTACH_FILES = 1 << 15           # 32768
PERM_USE_EXTERNAL_EMOJIS = 1 << 18    # 262144

# Default user permission set inside a paid channel — enough to read,
# react, send messages with embeds. No moderation rights.
PAID_USER_ALLOW = (
    PERM_VIEW_CHANNEL
    | PERM_SEND_MESSAGES
    | PERM_READ_MESSAGE_HISTORY
    | PERM_ADD_REACTIONS
    | PERM_EMBED_LINKS
    | PERM_ATTACH_FILES
    | PERM_USE_EXTERNAL_EMOJIS
)

# Channel structure. Each entry: (category_emoji_name, [(channel, topic, visibility)])
# `visibility` keys:
#   "public"        — everyone sees the channel
#   "paid"          — only Agent Buyer + Founder Stack Buyer + Operator see
#   "founder-only"  — only Founder Stack Buyer + Operator see
STRUCTURE = [
    ("📋 info", [
        ("welcome",            "Server intro, rules, what to expect.", "public"),
        ("announcements",      "Glitch Grow news, kit drops, ad campaigns.", "public"),
    ]),
    ("🌱 free-kit", [
        ("vibe-kit-help",      "Free-kit install + Claude Code / Codex / OpenClaws debugging.", "public"),
        ("showcase",           "Show what you built with the kit. Screenshots welcome.", "public"),
        ("feedback",           "Bugs, feature requests, friction reports.", "public"),
    ]),
    ("🔒 paid", [
        ("agent-support",      "Paid-tier product support — agent deployment, configuration, debugging.", "paid"),
        ("paid-announcements", "Product updates, deploy notes, new SKU drops. Read-only.", "paid"),
    ]),
    ("⭐ founder-stack", [
        ("founder-stack",      "Bundle buyers only — exclusive notes, 1:1 call scheduling, founder roundtables.", "founder-only"),
    ]),
]

# Roles. Order matters — first listed sits at the BOTTOM of the role list,
# last listed at the TOP. Higher roles override lower ones in permission
# resolution.
ROLES = [
    # (name, hex_color, hoist_in_member_list, mentionable)
    ("Free Kit User",        0x95A5A6, True,  False),
    ("Agent Buyer",          0x00C2FF, True,  False),
    ("Founder Stack Buyer",  0x00FF9D, True,  False),
    ("Operator",             0xF1C40F, True,  True),
]


def api(method: str, path: str, **kw):
    r = requests.request(method, f"{BASE}{path}", headers=H, timeout=20, **kw)
    if r.status_code == 429:
        retry = r.json().get("retry_after", 1)
        print(f"  rate-limited, sleeping {retry}s")
        time.sleep(retry + 0.2)
        return api(method, path, **kw)
    if not r.ok:
        print(f"  !! {method} {path} -> {r.status_code} {r.text[:300]}")
        r.raise_for_status()
    return r.json() if r.content else {}


def overwrites_for(visibility: str, role_ids: dict) -> list:
    """Build the channel-level permission overwrites for a given visibility level.

    Discord permission overwrite spec:
      { id: <role_or_user_id>, type: 0 (role) | 1 (member),
        allow: "<bitfield as string>", deny: "<bitfield as string>" }
    """
    everyone_id = role_ids["@everyone"]

    if visibility == "public":
        # Default — let @everyone see + chat. No overrides needed,
        # @everyone's guild-level perms apply.
        return []

    if visibility == "paid":
        return [
            # Hide from @everyone (Free Kit users + unauthenticated members)
            {"id": str(everyone_id), "type": 0,
             "allow": "0", "deny": str(PERM_VIEW_CHANNEL)},
            # Free Kit User explicitly denied (just to make intent crystal-clear
            # in the audit log, though @everyone deny already covers them)
            {"id": str(role_ids["Free Kit User"]), "type": 0,
             "allow": "0", "deny": str(PERM_VIEW_CHANNEL)},
            # Agent Buyers see + chat
            {"id": str(role_ids["Agent Buyer"]), "type": 0,
             "allow": str(PAID_USER_ALLOW), "deny": "0"},
            # Founder Stack Buyers see + chat
            {"id": str(role_ids["Founder Stack Buyer"]), "type": 0,
             "allow": str(PAID_USER_ALLOW), "deny": "0"},
        ]

    if visibility == "founder-only":
        return [
            {"id": str(everyone_id), "type": 0,
             "allow": "0", "deny": str(PERM_VIEW_CHANNEL)},
            {"id": str(role_ids["Free Kit User"]), "type": 0,
             "allow": "0", "deny": str(PERM_VIEW_CHANNEL)},
            {"id": str(role_ids["Agent Buyer"]), "type": 0,
             "allow": "0", "deny": str(PERM_VIEW_CHANNEL)},
            {"id": str(role_ids["Founder Stack Buyer"]), "type": 0,
             "allow": str(PAID_USER_ALLOW), "deny": "0"},
        ]

    raise ValueError(f"Unknown visibility: {visibility}")


def main():
    print(f"Bootstrapping community guild {GUILD_ID}")

    existing_channels = {c["name"]: c for c in api("GET", f"/guilds/{GUILD_ID}/channels")}
    existing_roles_list = api("GET", f"/guilds/{GUILD_ID}/roles")
    existing_roles = {r["name"]: r for r in existing_roles_list}

    # @everyone role's ID equals the guild ID per Discord's design.
    everyone_role = next((r for r in existing_roles_list if r["name"] == "@everyone"), None)
    if not everyone_role:
        print("ERROR: couldn't locate @everyone role", file=sys.stderr)
        sys.exit(1)

    # ── 1. Roles ────────────────────────────────────────────────────────
    print("\n== roles ==")
    role_ids = {"@everyone": int(everyone_role["id"])}
    for name, color, hoist, mentionable in ROLES:
        if name in existing_roles:
            print(f"  = {name}")
            role_ids[name] = int(existing_roles[name]["id"])
            continue
        created = api("POST", f"/guilds/{GUILD_ID}/roles", json={
            "name": name,
            "color": color,
            "hoist": hoist,
            "mentionable": mentionable,
        })
        role_ids[name] = int(created["id"])
        print(f"  + {name}")

    # ── 2. Categories + channels ────────────────────────────────────────
    print("\n== channels ==")
    for cat_name, channels in STRUCTURE:
        if cat_name in existing_channels and existing_channels[cat_name]["type"] == CATEGORY:
            cat_id = existing_channels[cat_name]["id"]
            print(f"  = {cat_name}")
        else:
            cat = api("POST", f"/guilds/{GUILD_ID}/channels", json={
                "name": cat_name,
                "type": CATEGORY,
            })
            cat_id = cat["id"]
            print(f"  + {cat_name}")

        for ch_name, topic, visibility in channels:
            if ch_name in existing_channels and existing_channels[ch_name]["type"] == TEXT:
                # Channel already exists — but we still want to make sure
                # the permission overwrites match the current visibility
                # spec. Idempotent re-application is safe.
                ch_id = existing_channels[ch_name]["id"]
                ovs = overwrites_for(visibility, role_ids)
                if ovs:
                    api("PATCH", f"/channels/{ch_id}", json={"permission_overwrites": ovs})
                    print(f"    = {ch_name}  (perms refreshed: {visibility})")
                else:
                    print(f"    = {ch_name}  ({visibility})")
                continue

            created = api("POST", f"/guilds/{GUILD_ID}/channels", json={
                "name": ch_name,
                "type": TEXT,
                "parent_id": cat_id,
                "topic": topic,
                "permission_overwrites": overwrites_for(visibility, role_ids),
            })
            print(f"    + {ch_name}  ({visibility})")

    print("\nDone.")
    print("\nNext manual steps in Discord (5 min):")
    print("  1. Server Settings → Engagement → Welcome Screen — enable + write a")
    print("     short greeting + add #welcome and #vibe-kit-help to the suggested")
    print("     channels list.")
    print("  2. Server Settings → Roles — drag 'Operator' to the very top and")
    print("     give yourself that role. Then drag the 3 buyer/free roles into")
    print("     the order they appear in member-list (Founder Stack at top).")
    print("  3. Server Settings → Safety Setup — set verification level to")
    print("     'Medium' or 'High' to deter spambots.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
