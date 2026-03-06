"""Shared league configuration.

Keep common league constants here so multiple cogs stay in sync.
"""

from datetime import date

# Guild scope for commands / lookups
GUILD_ID: int = 1462382487622914079

# Active clan roles (name -> role_id)
# NOTE: BYE is not a Discord role and should not be added here.
CLAN_ROLE_IDS: dict[str, int] = {
    "RMC": 1462558256147857408,
    "7DR": 1462383332598743080,
    "7PD": 1464763568506536000,
    "PG60": 1464763651108896778,
    "ITHL": 1464763753441788117,
    "48th": 1462558355166986261,
    "ZFG": 1476529643128356925,
    "CROWS": 1464764074985390090,
}


# =============================
# Shared emoji tagging
# =============================

# If text contains one of these keywords, bots can append the emoji tag after it.
# Put custom emoji names in Discord short-name format (e.g. ':48th:').
KEYWORD_EMOJI_TAGS: dict[str, str] = {
    "RMC": ":RMC:",
    "48th": ":48th:",
    "7DR": ":7DR:",
    "7PD": ":7PD:",
    "ITHL": ":ITHL:",
    "PG60": ":flag_de:",
    "ZFG": ":ZFG:",
    "CROWS": ":CROWS:", 
}


# =============================
# Events calendar (display)
# =============================

# Channel ID where events will be posted
EVENT_DISPLAY_CHANNEL_ID: int = 1464719794912755937

# How often to update the events display (in minutes)
UPDATE_INTERVAL_MINUTES: int = 30

# Maximum number of events to display - 25 is the max allowed by Discord per embed
MAX_EVENTS_TO_DISPLAY: int = 25

# Embed color (Discord blurple)
EMBED_COLOR: int = 0x5865F2


# =============================
# Season fixtures (display)
# =============================

# Display order for schedule embeds.
CLAN_DISPLAY_ORDER: list[str] = list(CLAN_ROLE_IDS.keys())

# BYE is a display placeholder (not a Discord role).
BYE_TEAM_NAME: str = "BYE"


# Round windows (inclusive) for validation and display.
ROUND_WINDOWS: dict[int, tuple[date, date]] = {
    1: (date(2026, 3, 2), date(2026, 3, 15)),
    2: (date(2026, 3, 16), date(2026, 3, 29)),
    3: (date(2026, 3, 30), date(2026, 4, 12)),
    4: (date(2026, 4, 13), date(2026, 4, 26)),
    5: (date(2026, 4, 27), date(2026, 5, 10)),
    6: (date(2026, 5, 11), date(2026, 5, 24)),
    7: (date(2026, 5, 25), date(2026, 6, 7)),
    8: (date(2026, 6, 8), date(2026, 6, 21)),
    9: (date(2026, 6, 22), date(2026, 7, 5)),

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


# ...existing code...

# Fixtures by round. Each entry is a list of (home, away) display names.
# Use BYE_TEAM_NAME for the bye week.
FIXTURES_BY_ROUND: dict[int, list[tuple[str, str]]] = {
    # RDG have dropped out. Schedule is adjusted to 8 active clans by pairing
    # the former RDG opponent with the round's BYE team.
    1: [("RMC", "ZFG"), ("7DR", "48th"), ("7PD", "PG60"), ("ITHL", "CROWS")],
    2: [("RMC", "48th"), ("ZFG", "ITHL"), ("7DR", "PG60"), ("CROWS", "7PD")],
    3: [("RMC", "ITHL"), ("48th", "PG60"), ("ZFG", "CROWS"), ("7PD", "7DR")],
    4: [("RMC", "PG60"), ("ITHL", "CROWS"), ("ZFG", "7PD"), ("7DR", "48th")],
    5: [("RMC", "CROWS"), ("ITHL", "7PD"), ("ZFG", "7DR"), ("48th", "PG60")],
    6: [("CROWS", "7PD"), ("ITHL", "7DR"), ("48th", "ZFG"), ("PG60", "RMC")],
    7: [("RMC", "7PD"), ("CROWS", "7DR"), ("PG60", "ZFG"), ("ITHL", "48th")],
    8: [("7PD", "7DR"), ("CROWS", "48th"), ("PG60", "ITHL"), ("RMC", "ZFG")],
    9: [("RMC", "7DR"), ("7PD", "48th"), ("CROWS", "PG60"), ("ZFG", "ITHL")],
}