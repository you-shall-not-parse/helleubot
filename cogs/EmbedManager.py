import discord
from discord.ext import commands
import json
import os

# ---------------- CONFIG ----------------
GUILD_ID = 1462382487622914079  # your guild ID
COG_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(COG_DIR, os.pardir))
DATA_FILE = os.path.join(PROJECT_ROOT, "stored_embeds.json")

# Optional: use a direct image URL (Discord CDN, Imgur direct link, etc.)
# Leave empty ("") to disable.
ABOUT_US_IMAGE_URL = "https://cdn.discordapp.com/attachments/1464650328736792770/1464650483837702325/image.png?ex=69763d8f&is=6974ec0f&hm=93b335df920c66157f14d2e62da090ca1fe55769b27fc6973a3976d8bf385681"
ABOUT_US_THUMBNAIL_URL = ""

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
            title="About Us",
            description=(
                "Games over admin. Automation over moderation. Fairness through structure.\n\n"
                "**One league. One table. One map per round. One bot doing the work.**"
            ),
            color=discord.Color.red(),
        )

        if ABOUT_US_IMAGE_URL:
            embed1.set_image(url=ABOUT_US_IMAGE_URL)
        if ABOUT_US_THUMBNAIL_URL:
            embed1.set_thumbnail(url=ABOUT_US_THUMBNAIL_URL)

        embed1.add_field(
            name="Discord Structure",
            value=(
                "- Discord is **apply-to-join**\n"
                "- **2–3 clan representatives** per clan (no other clan members)\n"
                "- No league chat channels — only <#1462382488784470181>"
            ),
            inline=False,
        )
        embed1.add_field(
            name="How the League Runs",
            value=(
                "- Official updates are delivered via <#1462384116376014911> which you can feed into your clan discord\n"
                "- Match scheduling is handled directly between clan representatives\n"
                "- Fixtures, results, and standings are provided **remotely**"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Automation (Bot System)",
            value=(
                "- Fixtures posted automatically via announcement embeds\n"
                "- Scores submitted via button-based embeds\n"
                "- Opposing clan must confirm the result\n"
                "- Once confirmed: results lock and the league table updates + reposts"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Links",
            value=(
                "Rules document: *(coming soon)*\n"
                "League support/contact: <#1462388616025210952>"
            ),
            inline=False,
        )

        embed1.add_field(name="\u200b", value="\u200b", inline=False)

        embed1.add_field(
            name="League Overview",
            value="Low-admin, low-friction, highly automated league play.",
            inline=False,
        )
        embed1.add_field(
            name="Teams Participating",
            value=(
                "RMC, 7DR, RDG, 7PD, PG60, ITHL, 48th, Ofins\n"
                "Maybe: Crow/ZFG, Sov/KRTS"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Format",
            value=(
                "- Single division\n"
                "- Everyone plays everyone once\n"
                "- 7 rounds for 8 teams"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Rounds & Scheduling",
            value=(
                "- Each round has a **2-week window**\n"
                "- Can be extended to **3 weeks** on request\n"
                "- Fixture numbers are flexible: **30–50 players** (agreed in <#1462384116376014911>)"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Maps, Sides & Servers",
            value=(
                "- One map per round — all teams play the same map\n"
                "- Once used, the map leaves the pool\n"
                "- Mid-point: spin-the-wheel\n"
                "- Sides + server host decided by coin flip"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Scoring & Standings",
            value=(
                "- Scoring is based solely on **points scored per match**\n"
                "- League table ordered by **total points accumulated**"
            ),
            inline=False,
        )
        embed1.add_field(
            name="Dropouts",
            value=(
                "If a team drops out mid-season, remaining opponents receive a **bye** and the league continues without disruption."
            ),
            inline=False,
        )
        embed1.add_field(
            name="Ruleset Notes",
            value=(
                "- No roster locking\n"
                "- Admin cam logs / player_ids may be requested\n"
                "- No win condition within the first 30 minutes"
            ),
            inline=False,
        )
        blocks.append({
            "key": "about_us",
            "channel_id": 1462387027046830212,
            "embed": embed1
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

    # ---------------- OPTIONAL MANUAL COMMAND ----------------
    @commands.command(name="sync_embeds")
    async def sync_embeds_cmd(self, ctx):
        """Manually sync all embeds to their channels"""
        await self.sync_all_embeds()
        await ctx.send("All embeds synced!")


# ---------------- SETUP ----------------
async def setup(bot):
    await bot.add_cog(EmbedManager(bot))
