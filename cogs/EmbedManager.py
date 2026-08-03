import discord
from discord.ext import commands, tasks
import json
import os
from typing import Optional

from data_paths import data_path
from league_config import (
    ABOUT_EMBED_CHANNEL_ID,
    ADMIN_CONTACT_CHANNEL_ID,
    FULL_RULES_CHANNEL_ID,
    GUILD_ID,
    LEADERBOARD_CHANNEL_IDS,
    LEAGUE_INFO_CHANNEL_ID,
    ORGANISER_EMBED_CHANNEL_ID,
    RULES_EMBED_CHANNEL_ID,
    SCOREBOARD_CHANNEL_ID,
    SEASON_SCHEDULE_CHANNEL_ID,
)

# ---------------- CONFIG ----------------
COG_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(COG_DIR, os.pardir))
DATA_FILE = data_path("stored_embeds.json")

# Auto-refresh embeds periodically so dynamic sections (like clan reps) stay updated.
AUTO_SYNC_INTERVAL_MINUTES: int = 30

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
                f"- No league chat channels, only <#{LEAGUE_INFO_CHANNEL_ID}>"
            ),
            inline=False,
        )
        embed1.add_field(
            name=":question: How does the league work?",
            value=(
                "- Single division, everyone plays each team once\n"
                f"- Match scheduling is handled directly between clan representatives using <#{ORGANISER_EMBED_CHANNEL_ID}>\n"
                f"- Division 1 and Division 2 standings are posted in <#{LEADERBOARD_CHANNEL_IDS['Division 1']}> "
                f"and <#{LEADERBOARD_CHANNEL_IDS['Division 2']}>\n"
                f"- Scores are submitted via the button-based embed in <#{SCOREBOARD_CHANNEL_ID}>\n"
                f"- Check out <#{FULL_RULES_CHANNEL_ID}> for full rules"
            ),
            inline=False,
        )
        embed1.add_field(
            name=" :pencil: How and when do we join?",
            value=(
                "- European teams :flag_eu: only are permitted to take part\n"
                f"- Check out the current season schedule and fixtures in <#{SEASON_SCHEDULE_CHANNEL_ID}>\n"
                f"- Contact an admin in <#{ADMIN_CONTACT_CHANNEL_ID}> to express interest\n"
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
            "channel_id": ABOUT_EMBED_CHANNEL_ID,
            "embed": embed1
        })
        # ---------------- EMBED 2: DISCORD SERVER RULES ----------------
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
            "channel_id": RULES_EMBED_CHANNEL_ID,
            "embed": embed3
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
