import discord
from discord.ext import commands, tasks
import json
import os
import re
from typing import Optional

from data_paths import data_path
from league_config import (
    BYE_TEAM_NAME,
    CLAN_DISPLAY_ORDER,
    CLAN_ROLE_IDS,
    FIXTURES_BY_ROUND,
    KEYWORD_EMOJI_TAGS,
    format_round_window,
)

# ---------------- CONFIG ----------------
GUILD_ID = 1462382487622914079  # your guild ID
COG_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(COG_DIR, os.pardir))
DATA_FILE = data_path("stored_embeds.json")

# Auto-refresh embeds periodically so dynamic sections (like clan reps) stay updated.
AUTO_SYNC_INTERVAL_MINUTES: int = 30

# Role that marks someone as an organiser/rep. Members must have BOTH this role and a clan role.
# Prefer setting the ID; if 0/None, we'll fall back to a name lookup.
EVENT_ORGANISERS_ROLE_ID: int = 1462383205280649308
EVENT_ORGANISERS_ROLE_NAME: str = "Clan Representative"



SCHEDULE_TEAMS_FIELD_NAME: str = "👥Teams Participating"
SCHEDULE_REPS_FIELD_NAME: str = "👤 Clan reps"

# ---------------- HELPER FUNCTIONS ----------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


class EmbedManager(commands.Cog):
    """Cog to manage static embeds that auto-post/update"""

    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()
        self._auto_sync_task.start()

    def cog_unload(self):
        if self._auto_sync_task.is_running():
            self._auto_sync_task.cancel()

    @tasks.loop(minutes=AUTO_SYNC_INTERVAL_MINUTES)
    async def _auto_sync_task(self):
        await self.sync_all_embeds()

    @_auto_sync_task.before_loop
    async def _before_auto_sync_task(self):
        await self.bot.wait_until_ready()

    def _get_role_by_name(self, guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
        target = str(role_name or "").strip().lower()
        if not target:
            return None
        for r in getattr(guild, "roles", []):
            if str(getattr(r, "name", "")).strip().lower() == target:
                return r
        return None

    def _resolve_event_organisers_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        if EVENT_ORGANISERS_ROLE_ID:
            return guild.get_role(EVENT_ORGANISERS_ROLE_ID)
        return self._get_role_by_name(guild, EVENT_ORGANISERS_ROLE_NAME)

    async def _get_guild_members(self, guild: discord.Guild) -> list[discord.Member]:
        members = list(getattr(guild, "members", []) or [])
        if members:
            return members

        # Try chunking (works only if members intent is enabled).
        try:
            await guild.chunk(cache=True)
            members = list(getattr(guild, "members", []) or [])
            if members:
                return members
        except Exception:
            pass

        # Fall back to fetch_members (also requires members intent).
        try:
            fetched: list[discord.Member] = [m async for m in guild.fetch_members(limit=None)]
            return fetched
        except Exception:
            return []

    def _extract_schedule_clans(self, embed: discord.Embed) -> list[str]:
        """Best-effort extract of clan shortnames from the schedule embed."""

        raw = ""
        for f in list(getattr(embed, "fields", []) or []):
            if str(getattr(f, "name", "")).strip() == SCHEDULE_TEAMS_FIELD_NAME:
                raw = str(getattr(f, "value", "") or "")
                break

        if not raw:
            return list(CLAN_ROLE_IDS.keys())

        # Split on commas/newlines and normalise. Values may already have emojis appended
        # (e.g. 'RMC <:RMC:123>'), so we take the first whitespace token.
        tokens = [t.strip() for t in re.split(r"[\n,]", raw) if t and t.strip()]
        clans: list[str] = []
        for t in tokens:
            head = t.split()[0].strip() if t.split() else ""
            if head in CLAN_ROLE_IDS and head not in clans:
                clans.append(head)
        return clans or list(CLAN_ROLE_IDS.keys())

    async def _build_clan_reps_value(self, guild: discord.Guild, embed: discord.Embed) -> str:
        organiser_role = self._resolve_event_organisers_role(guild)
        if organiser_role is None:
            return "(Missing Event Organisers role config)"

        members = await self._get_guild_members(guild)

        clans = self._extract_schedule_clans(embed)
        lines: list[str] = []

        for clan in clans:
            clan_role_id = CLAN_ROLE_IDS.get(clan)
            clan_role = guild.get_role(clan_role_id) if clan_role_id else None

            if clan_role is None:
                lines.append(f"**{clan}:** —")
                continue

            reps = [
                m
                for m in members
                if isinstance(m, discord.Member)
                and not getattr(m, "bot", False)
                and clan_role in getattr(m, "roles", [])
                and organiser_role in getattr(m, "roles", [])
            ]
            reps.sort(key=lambda m: str(getattr(m, "display_name", "")).lower())

            if reps:
                mentions = " ".join(m.mention for m in reps)
                lines.append(f"**{clan}:** {mentions}")
            else:
                lines.append(f"**{clan}:** —")

        value = "\n".join(lines).strip() or "—"
        # Embed field limit is 1024 characters.
        if len(value) > 1024:
            value = value[:1021] + "..."
        return value

    async def _upsert_schedule_clan_reps(self, guild: discord.Guild, embed: discord.Embed) -> None:
        reps_value = await self._build_clan_reps_value(guild, embed)

        original_fields = list(getattr(embed, "fields", []) or [])
        kept: list[tuple[str, str, bool]] = []
        for f in original_fields:
            name = str(getattr(f, "name", "") or "")
            if name == SCHEDULE_REPS_FIELD_NAME:
                continue
            kept.append((name, f.value, f.inline))

        embed.clear_fields()
        for name, value, inline in kept:
            embed.add_field(name=name, value=value, inline=inline)
        embed.add_field(name=SCHEDULE_REPS_FIELD_NAME, value=reps_value, inline=False)

    def _resolve_custom_emoji(self, guild: discord.Guild, emoji_tag: str) -> str:
        """Resolve a tag like ':name:' to '<:name:id>' if possible."""

        emoji_name = emoji_tag.strip(":")
        if not emoji_name:
            return emoji_tag

        for emoji in getattr(guild, "emojis", []):
            if emoji.name == emoji_name:
                return str(emoji)

        # Not found; return the original tag (may display as text)
        return emoji_tag

    def _append_team_emojis(self, guild: discord.Guild, text: str) -> str:
        """Append configured emojis after matching keywords in text."""

        if not text or not KEYWORD_EMOJI_TAGS:
            return text

        formatted = text

        # Longer keys first to avoid partial matches.
        for keyword in sorted(KEYWORD_EMOJI_TAGS.keys(), key=len, reverse=True):
            emoji_tag = KEYWORD_EMOJI_TAGS.get(keyword)
            if not emoji_tag:
                continue

            emoji_str = self._resolve_custom_emoji(guild, emoji_tag)

            # Match keyword as a standalone token (not inside another word).
            pattern = re.compile(rf"(?<!\\w){re.escape(keyword)}(?!\\w)")

            def _repl(match: re.Match) -> str:
                # Avoid double-appending if already followed by an emoji-like token.
                end = match.end()
                tail = formatted[end:end + 32]
                if emoji_str in tail or emoji_tag in tail:
                    return match.group(0)
                return f"{match.group(0)} {emoji_str}"

            formatted = pattern.sub(_repl, formatted)

        return formatted

    def _apply_schedule_emoji_tags(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Mutate embed fields so schedule fixtures include team emojis."""

        # discord.py field proxies are read-only; rebuild fields safely.
        original_fields = list(getattr(embed, "fields", []))
        rebuilt = []
        for f in original_fields:
            value = f.value
            if isinstance(value, str):
                value = self._append_team_emojis(guild, value)
            rebuilt.append((f.name, value, f.inline))

        embed.clear_fields()
        for name, value, inline in rebuilt:
            embed.add_field(name=name, value=value, inline=inline)

    # ---------------- CHEAT SHEET ----------------
    """
    ================= EMBED CHEAT SHEET =================
    COLORS:
        discord.Color.blue()
        Custom Hex: discord.Color.from_str("#1abc9c")

    LINE BREAKS:
        "\n"       - new line
        "\n\n"     - blank line
        "\u200b"   - zero width space

    SPACER FIELD:
        embed.add_field(name="\u200b", value="\u200b", inline=False)

    =====================================================
    """

    # ---------------- EMBED DEFINITIONS ----------------
    def get_embed_blocks(self):
        """
        Returns a list of embed blocks.
        Each block is a dict: {"key": ..., "channel_id": ..., "embed": discord.Embed}
        """

        blocks = []

        # ---------------- EMBED 1: ABOUT US ----------------
        embed1 = discord.Embed(
            title=":boom: League FAQ :boom:",
            description=(
                "*Games over admin. Automation over moderation. Fair play over all else.*\n\n"
            ),
            color=discord.Color.blurple(),
        )

        embed1.add_field(
            name=":question: How does this discord work?",
            value=(
                "- Discord is apply-to-join\n"
                "- 2–3 clan representatives per clan (no other clan members)\n"
                "- No league chat channels, only <#1462382488784470181>"
            ),
            inline=False,
        )
        embed1.add_field(
            name=":question: How does the league work?",
            value=(
                "- Single division, everyone plays each team once\n"
                "- Match scheduling is handled directly between clan representatives using <#1464726144367464685>\n"
                "- Fixtures, results, media and standings are provided **remotely** into your clan discord via <#1462384116376014911> \n"
                "- Scores submitted via button-based embed message, opposing clan confirms the result and result is reposed in <#1462387812815998997>\n"
                "- Check out <#1464642927438463269> for full rules"
            ),
            inline=False,
        )
        embed1.add_field(
            name=" :pencil: How and when do we join?",
            value=(
                "- European teams :flag_eu: only are permitted to take part\n"
                "- Check out the current season schedule and fixtures in <#1462388344205082685>\n"
                "- Contact an admin in <#1462388616025210952> to express interest\n"
            ),
            inline=False,
        )

        # Image URLs for EMBED 1 (paste Discord CDN links here)
        embed1_image_url = "https://cdn.discordapp.com/attachments/1464650328736792770/1464650483837702325/image.png?ex=69763d8f&is=6974ec0f&hm=93b335df920c66157f14d2e62da090ca1fe55769b27fc6973a3976d8bf385681"
        embed1_thumbnail_url = ""
        if embed1_image_url:
            embed1.set_image(url=embed1_image_url)
        if embed1_thumbnail_url:
            embed1.set_thumbnail(url=embed1_thumbnail_url)

        blocks.append({
            "key": "about_us",
            "channel_id": 1462387027046830212,
            "embed": embed1
        })
        # ---------------- EMBED 2: EVERYTHING AFTER SPLIT ----------------
        embed2 = discord.Embed(
            title="Rules",
            color=discord.Color.blurple(),
        )

        embed2.add_field(
            name="🔢 Rounds & Scheduling",
            value=(
                "- Each round has a **2-week window**\n"
                "- Fixtures are automated and in <#1462388344205082685>\n"
                "- Fixtures are organised by using <#1464726144367464685>\n"
                "- Once you have organised your fixture it magically appears in <#1464719794912755937>"
            ),
            inline=False,
        )
        embed2.add_field(
            name="🗺️ Maps, Sides & Servers",
            value=(
                "- One map per round — all teams play the same map then the map leaves the pool\n"
                "- Maps are chosen by spin the wheel before the round commences and all maps except Stalingrad, Driel, Remagen, Smolensk are in the mix!\n"
                "- Mid-point: Spin the Wheel\n"
                "- Sides and Server host decided by using <#1464726144367464685>\n"
                "- Fixture player numbers between **30–50 players** as agreed with opponent using <#1464726144367464685>\n"
                "- Streaming is permitted but must not be live and must be agreed before the match with opponent"
            ),
            inline=False,
        )
        embed2.add_field(
            name="🏆 Scoring & Standings",
            value=(
                "- Scores are subbitted by using <#1462387812815998997>"
                "- Scoring is based solely on **objectives scored per match**\n"
                "- League table ordered by **total objectives accumulated** and is automated in <#1462384116376014911> \n"
                "- The team with the better W/L record will be awarded the higher seed, if this is tied then a final match shall be played to determine the first-place seed"
            ),
            inline=False,
        )
        embed2.add_field(
            name="📜 Anti-Cheat and Duration Protection",
            value=(
                "- No roster locks required\n"
                "- Admin cam logs / player_ids may be requested by the opposing team\n"
                "- **No win condition within the first 30 minutes**\n"
                "- Deliberate clipping inside an asset that prevents you being shot but allows you to shoot out is prohibited, this does not include terrain clipping as a result of natural gameplay\n"
                "- The use of rooftops is permitted provided the above rule is not violated\n"
                "- The use of cronus/XIM or other controller emulator is prohibited\n"
                "- Hired guns are prohibited however coalition teams may be formed and fielded\n"
                "- Players transferred between clans competing in the current season are banned from playing for their new clan until the following season\n"
                "- Footage and consequence is subject to review by league admins if submitted as part of a protest\n"
            ),
            inline=False,
        )
        embed2.add_field(
            name=":boom: Artillery, SPA & Panther",
            value=(
                "- The use of HLL log utilities is prohibited, trust is key in this league\n"
                "- Multiple artillery squads can be fielded but only one static gun is permitted.\n"
                "- Artillery Observer (SL) must operate the gun and cannot move freely on the map\n"
                "- Designated Artillery Player (DAP) & Reloader:\n"
                "   - First player to get a kill becomes the DAP.\n"
                "   - Each team may assign one reloader in the same artillery squad who assists the DAP.\n"
                "   - Both must stay on one assigned gun; you cannot switch guns.\n"
                "   - Both cannot leave the gun or attack enemies.\n"
                "   - If either are accidentally killed, both must return to the gun without returning fire.\n"
                "- SPA is prohibited unless otherwise explicitly agreed by both teams and driving (only) within the HQ is permitted to move it\n"
                "- Commander artillery is prohibited\n" 
                "- Panther tank is prohibited unless otherwise explicitly agreed by both teams, driving (only) within the HQ is permitted to move it\n"
                "- Failure to comply may result in forfeiture of the match and/or points deducted\n"
            ),
            inline=False,
        )
        embed2.add_field(
            name=" :tools: Indestructable Nodes",
            value=(
                "- Nodes must be built in an HQ sector that is away from artillery and vehicle spawns\n"
                "- Nodes cannot be destroyed or dismantled by the opposing team\n"
                "- Failure to comply may result in forfeiture of the match and/or points deducted\n"
            ),
            inline=False,
        )

        # Image URLs for EMBED 2 (paste Discord CDN links here)
        embed2_image_url = ""
        embed2_thumbnail_url = ""
        if embed2_image_url:
            embed2.set_image(url=embed2_image_url)
        if embed2_thumbnail_url:
            embed2.set_thumbnail(url=embed2_thumbnail_url)

        blocks.append({
            "key": "rules",
            "channel_id": 1464642927438463269,
            "embed": embed2
        })

        # ---------------- EMBED 3: DISCORD SERVER RULES ----------------
        embed3 = discord.Embed(
            title="Discord Server Rules & Conduct",
            description=(
                "1. Keep it simple - organise events or stay up-to-date.\n"
                "2. No inappropriate profile pictures.\n"
                "3. No @mentioning spam.\n"
                "4. No NSFW or Illegal content.\n"
                "5. No personal attacks.\n"
                "6. No harassment.\n"
                "7. No sexism.\n"
                "8. No racism.\n"
                "9. No hate speech."
            ),
            color=discord.Color.blurple(),
        )

        # Image URLs for EMBED 3 (paste Discord CDN links here)
        embed3_image_url = ""
        embed3_thumbnail_url = ""
        if embed3_image_url:
            embed3.set_image(url=embed3_image_url)
        if embed3_thumbnail_url:
            embed3.set_thumbnail(url=embed3_thumbnail_url)

        blocks.append({
            "key": "discord_rules_conduct",
            "channel_id": 1462382688777404601,
            "embed": embed3
        })

        # ---------------- EMBED 4: Schedule and Fixes ----------------
        embed4 = discord.Embed(
            title=":calendar: Fixtures & Schedule",
            description="Round windows for the current season.",
            color=discord.Color.blurple(),
        )

        embed4.add_field(
            name="👥Teams Participating",
            value=(
                ", ".join([*CLAN_DISPLAY_ORDER, BYE_TEAM_NAME]) + "\n"
            ),
            inline=False,
        )

        for round_no in sorted(FIXTURES_BY_ROUND.keys()):
            window = format_round_window(round_no)
            fixtures = FIXTURES_BY_ROUND.get(round_no, [])
            lines: list[str] = []
            if window:
                lines.append(window)
            for a, b in fixtures:
                lines.append(f"{a} vs {b}")

            embed4.add_field(
                name=f"Round {round_no}",
                value="\n".join(lines).strip() or "—",
                inline=False,
            )

        # Image URLs for EMBED 4 (paste Discord CDN links here)
        embed4_image_url = "https://cdn.discordapp.com/attachments/1464650328736792770/1464650483837702325/image.png?ex=69763d8f&is=6974ec0f&hm=93b335df920c66157f14d2e62da090ca1fe55769b27fc6973a3976d8bf385681"
        embed4_thumbnail_url = ""
        if embed4_image_url:
            embed4.set_image(url=embed4_image_url)
        if embed4_thumbnail_url:
            embed4.set_thumbnail(url=embed4_thumbnail_url)

        blocks.append({
            "key": "schedule",
            "channel_id": 1462388344205082685,
            "embed": embed4
        })

        return blocks

    # ---------------- SYNC LOGIC ----------------
    async def sync_embed_block(self, block):
        channel = self.bot.get_channel(block["channel_id"])
        if channel is None:
            print(f"[EmbedManager] Channel {block['channel_id']} not found.")
            return

        key = block["key"]
        embed_to_post = block["embed"]

        # Apply team emoji tagging to the schedule embed (Embed 4)
        if key == "schedule" and hasattr(channel, "guild") and channel.guild is not None:
            self._apply_schedule_emoji_tags(channel.guild, embed_to_post)
            await self._upsert_schedule_clan_reps(channel.guild, embed_to_post)
        stored_id = self.data.get(key)

        msg = None
        if stored_id:
            try:
                msg = await channel.fetch_message(stored_id)
            except discord.NotFound:
                print(f"[EmbedManager] Previous embed '{key}' missing, will post new.")

        if msg and msg.embeds and msg.embeds[0].to_dict() != embed_to_post.to_dict():
            print(f"[EmbedManager] Updating embed '{key}' in channel {channel.id}")
            await msg.edit(embed=embed_to_post)
        elif msg:
            print(f"[EmbedManager] Embed '{key}' unchanged in channel {channel.id}")
        else:
            new_msg = await channel.send(embed=embed_to_post)
            self.data[key] = new_msg.id
            print(f"[EmbedManager] Posted new embed '{key}' to channel {channel.id}")

        save_data(self.data)

    async def sync_all_embeds(self):
        blocks = self.get_embed_blocks()
        for block in blocks:
            await self.sync_embed_block(block)

    # ---------------- AUTO-SYNC ON READY ----------------
    @commands.Cog.listener()
    async def on_ready(self):
        print("[EmbedManager] Bot ready — syncing embeds...")
        await self.sync_all_embeds()


# ---------------- SETUP ----------------
async def setup(bot):
    await bot.add_cog(EmbedManager(bot))
