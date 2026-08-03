"""Shared league configuration.

Keep common league constants here so multiple cogs stay in sync.
"""

from datetime import date

import discord

# Guild scope for commands / lookups
GUILD_ID: int = 1533957292947673288

# Role to ping when a fixture is marked as streamed.
STREAMER_ROLE_ID: int = 1478166069662191627

# Active clan roles (name -> role_id)
# NOTE: BYE is not a Discord role and should not be added here.
CLAN_ROLE_IDS: dict[str, int] = {
    "OFIN": 1520125783983653105,
    "HG": 1517652132541628456,
    "KRTS": 1518583363953229894,
    "7CIE": 1520121068600164582,
    "RMC": 1462558256147857408,
    "7DR": 1462383332598743080,
    "7PD": 1464763568506536000,
    "PG60": 1464763651108896778,
    "48th": 1462558355166986261,
    "ZFG": 1476529643128356925,
}


# =============================
# Discord channels
# =============================

# Replace each 0 with a channel ID from the new server. The same channel may
# be used for multiple settings. Leave SCHEDULED_EVENT_CHANNEL_ID as None to
# create external scheduled events instead of attaching them to voice/stage.

# Score submission embed and confirmed results.
SCOREBOARD_CHANNEL_ID: int = 1533968864369573948

# Clan/team listing.
TEAMS_CHANNEL_ID: int = 1533973957823303711

# Pending scores awaiting confirmation by the opposing clan.
VALIDATION_CHANNEL_ID: int = 1533968999019315421

# Division tables and rendered leaderboard images.
LEADERBOARD_CHANNEL_IDS: dict[str, int] = {
    "Division 1": 1533969122193309836,
    "Division 2": 1533969302947102950,
}

# Upcoming Discord events calendar embed.
EVENT_DISPLAY_CHANNEL_ID: int = 1533968625055301784

# Parent text channel for threads automatically created for Discord events.
EVENT_THREADS_PARENT_CHANNEL_ID: int = 1533968999019315421

# Home channel for the fixture organiser embed.
ORGANISER_EMBED_CHANNEL_ID: int = 1533970006428221630

# Parent forum channels for fixture negotiation posts.
FIXTURE_FORUM_CHANNEL_IDS: dict[str, int] = {
    "Division 1": 1533969661329145919,
    "Division 2": 1533969797262606426,
}

# Optional voice/stage channel for scheduled events; None creates external events.
SCHEDULED_EVENT_CHANNEL_ID: int | None = 1533957293639602271

# Streamer request messages and the streamer calendar/board.
STREAMER_REQUESTS_CHANNEL_ID: int = 1533971238748426361
STREAMER_CALENDAR_CHANNEL_ID: int = 1533971281521803285

# =============================
# Shared emoji tagging
# =============================

# If text contains one of these keywords, bots can append the emoji tag after it.
# Put custom emoji names in Discord short-name format (e.g. ':48th:').
KEYWORD_EMOJI_TAGS: dict[str, str] = {
    "OFIN": ":Only_Finns:",
    "HG": ":HG:",
    "KRTS": ":KRTS:",
    "7DR": ":7DR:",
    "7PD": ":7PD:",
    "48th": ":48th:",
    "PG60": ":flag_de:",
    "RMC": ":RMC:",
    "7CIE": ":7CIE:",
    "ZFG": ":ZFG:",
}


# =============================
# Events calendar (display)
# =============================

# How often to update the events display (in minutes)
UPDATE_INTERVAL_MINUTES: int = 30

# Maximum number of events to display - 25 is the max allowed by Discord per embed
MAX_EVENTS_TO_DISPLAY: int = 25

# Embed color (Discord blurple)
EMBED_COLOR: int = 0x5865F2


# =============================
# Season fixtures (display)
# =============================

# Divisions for the active season.
DIVISION_CLANS: dict[str, list[str]] = {
    "Division 1": ["48th", "7PD", "ZFG", "PG60", "7CIE"],
    "Division 2": ["OFIN", "HG", "KRTS", "7DR", "RMC"],
}

# Display order for schedule-like surfaces.
CLAN_DISPLAY_ORDER: list[str] = [
    *DIVISION_CLANS["Division 1"],
    *DIVISION_CLANS["Division 2"],
]

# BYE is a display placeholder (not a Discord role).
BYE_TEAM_NAME: str = "BYE"


# Round windows (inclusive) for validation and display.
ROUND_WINDOWS: dict[int, tuple[date, date]] = {
    1: (date(2026, 7, 20), date(2026, 8, 2)),
    2: (date(2026, 8, 3), date(2026, 8, 16)),
    3: (date(2026, 8, 17), date(2026, 8, 30)),
    4: (date(2026, 8, 31), date(2026, 9, 13)),
    5: (date(2026, 9, 14), date(2026, 9, 27)),
}


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def format_round_window(round_no: int) -> str:
    """Format a round window like: '2nd March - 15th March 2026'."""

    if round_no not in ROUND_WINDOWS:
        return ""
    start, end = ROUND_WINDOWS[round_no]
    start_str = f"{_ordinal(start.day)} {start.strftime('%B')}"
    end_str = f"{_ordinal(end.day)} {end.strftime('%B')} {end.year}"
    if start.year != end.year:
        start_str = f"{start_str} {start.year}"
    return f"{start_str} - {end_str}"


DIVISION_FIXTURES_BY_ROUND: dict[str, dict[int, list[tuple[str, str]]]] = {
    "Division 1": {
        1: [("48th", "7PD"), ("ZFG", "PG60")],
        2: [("ZFG", "48th"), ("7PD", "7CIE")],
        3: [("PG60", "48th"), ("ZFG", "7CIE")],
        4: [("7CIE", "48th"), ("7PD", "PG60")],
        5: [("7PD", "ZFG"), ("PG60", "7CIE")],
    },
    "Division 2": {
        1: [("OFIN", "HG"), ("KRTS", "7DR")],
        2: [("KRTS", "OFIN"), ("HG", "RMC")],
        3: [("7DR", "OFIN"), ("KRTS", "RMC")],
        4: [("OFIN", "RMC"), ("HG", "7DR")],
        5: [("HG", "KRTS"), ("7DR", "RMC")],
    },
}


FIXTURES_BY_ROUND: dict[int, list[tuple[str, str]]] = {
    round_no: [
        fixture
        for division in DIVISION_FIXTURES_BY_ROUND.values()
        for fixture in division.get(round_no, [])
    ]
    for round_no in sorted(ROUND_WINDOWS.keys())
}


TEAMS_EMBED_MARKER: str = "league-config-teams"


def build_teams_embed() -> discord.Embed:
    """Build the configured league overview shown in the teams channel."""

    embed = discord.Embed(
        title="League Teams",
        description=f"{len(CLAN_ROLE_IDS)} clans competing across {len(DIVISION_CLANS)} divisions.",
        color=EMBED_COLOR,
    )

    for division, clans in DIVISION_CLANS.items():
        teams = [
            f"{KEYWORD_EMOJI_TAGS.get(clan, '•')} <@&{CLAN_ROLE_IDS[clan]}> (`{clan}`)"
            for clan in clans
            if clan in CLAN_ROLE_IDS
        ]
        embed.add_field(name=division, value="\n".join(teams) or "No clans configured", inline=True)

    rounds = [
        f"**Round {round_no}:** {format_round_window(round_no)}"
        for round_no in sorted(ROUND_WINDOWS)
    ]
    embed.add_field(name="Season Schedule", value="\n".join(rounds), inline=False)

    channel_links = [
        f"Scores: <#{SCOREBOARD_CHANNEL_ID}>",
        f"Validation: <#{VALIDATION_CHANNEL_ID}>",
        *(f"{division} table: <#{channel_id}>" for division, channel_id in LEADERBOARD_CHANNEL_IDS.items()),
        f"Fixtures: <#{ORGANISER_EMBED_CHANNEL_ID}>",
        f"Events: <#{EVENT_DISPLAY_CHANNEL_ID}>",
        f"Streamer requests: <#{STREAMER_REQUESTS_CHANNEL_ID}>",
        f"Streamer calendar: <#{STREAMER_CALENDAR_CHANNEL_ID}>",
    ]
    embed.add_field(name="League Channels", value="\n".join(channel_links), inline=False)
    embed.add_field(name="Streamer Role", value=f"<@&{STREAMER_ROLE_ID}>", inline=False)
    embed.set_footer(text=TEAMS_EMBED_MARKER)
    return embed


async def publish_teams_embed(bot: discord.Client) -> None:
    """Post the teams overview, or update the existing bot-owned copy."""

    channel = bot.get_channel(TEAMS_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(TEAMS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise TypeError(f"TEAMS_CHANNEL_ID {TEAMS_CHANNEL_ID} is not a text channel")

    embed = build_teams_embed()
    async for message in channel.history(limit=50):
        if message.author.id != bot.user.id or not message.embeds:
            continue
        if message.embeds[0].footer.text == TEAMS_EMBED_MARKER:
            await message.edit(embed=embed)
            return

    await channel.send(embed=embed)
