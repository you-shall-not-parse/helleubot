import asyncio
import json
import os
import random
import secrets
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from data_paths import data_path
from league_config import CLAN_ROLE_IDS, ROUND_WINDOWS, STREAMER_ROLE_ID

# =============================
# CONFIG (EDIT THIS)
# =============================

GUILD_ID = 1462382487622914079

# Feature toggles
# Set these to False to disable the related controls and requirements.
ENABLE_MAP_MIDPOINT = False
ENABLE_SIDES = True

# If ENABLE_SIDES is False (or sides aren't set), we still need a location for external events.
EVENT_LOCATION_FALLBACK = "TBD Server"

# Channel where the “Organise Fixture” embed is posted.
ORGANISER_EMBED_CHANNEL_ID = 1464726144367464685

# Thread parent channel (threads created under this channel)
THREAD_PARENT_CHANNEL_ID = 1462382488784470181

# Where to create the scheduled Discord event. Usually same guild.
SCHEDULED_EVENT_GUILD_ID = GUILD_ID

# Optional: channel to associate to the scheduled event (voice/stage). Leave None to create an external event.
SCHEDULED_EVENT_CHANNEL_ID: Optional[int] = None



# Maps and midpoints (edit these lists)
MAP_POOL: list[str] = [
	"Carentan",
	"Hurtgen Forest",
	"Foy",
	"St Marie Du Mont",
	"St Mere Eglise",
	"Utah Beach",
	"Omaha Beach",
	"Purple Heart Lane",
	"Kursk",
	"Kharkov",
	"El Alamein",
	"Mortain",
]

# Midpoints are per-map: each map must have exactly 3 midpoints.
# Replace these placeholder strings with your real midpoint names for each map.
MIDPOINTS_BY_MAP: dict[str, list[str]] = {
	"Carentan": ["<Carentan mid 1>", "<Carentan mid 2>", "<Carentan mid 3>"],
	"Hurtgen Forest": ["<Hurtgen mid 1>", "<Hurtgen mid 2>", "<Hurtgen mid 3>"],
	"Foy": ["<Foy mid 1>", "<Foy mid 2>", "<Foy mid 3>"],
	"St Marie Du Mont": ["<SMDM mid 1>", "<SMDM mid 2>", "<SMDM mid 3>"],
	"St Mere Eglise": ["<SME mid 1>", "<SME mid 2>", "<SME mid 3>"],
	"Utah Beach": ["<Utah mid 1>", "<Utah mid 2>", "<Utah mid 3>"],
	"Omaha Beach": ["<Omaha mid 1>", "<Omaha mid 2>", "<Omaha mid 3>"],
	"Purple Heart Lane": ["<PHL mid 1>", "<PHL mid 2>", "<PHL mid 3>"],
	"Kursk": ["<Kursk mid 1>", "<Kursk mid 2>", "<Kursk mid 3>"],
	"Kharkov": ["<Kharkov mid 1>", "<Kharkov mid 2>", "<Kharkov mid 3>"],
	"El Alamein": ["<El Alamein mid 1>", "<El Alamein mid 2>", "<El Alamein mid 3>"],
	"Mortain": ["<Mortain mid 1>", "<Mortain mid 2>", "<Mortain mid 3>"],
}

# Max map+mid rerolls ("mix-ups") per clan
REROLL_LIMIT = 3

# Where we persist state
STATE_PATH = data_path("fixture_organiser_state.json")


# =============================
# Helpers
# =============================


def _load_state() -> dict[str, Any]:
	if not os.path.exists(STATE_PATH):
		return {"organiser_message": None, "threads": {}}
	try:
		with open(STATE_PATH, "r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, dict):
			return {"organiser_message": None, "threads": {}}
		data.setdefault("organiser_message", None)
		data.setdefault("threads", {})
		return data
	except Exception:
		return {"organiser_message": None, "threads": {}}


def _save_state(state: dict[str, Any]) -> None:
	os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
	with open(STATE_PATH, "w", encoding="utf-8") as f:
		json.dump(state, f, indent=2, ensure_ascii=False)


def _ordinal(n: int) -> str:
	if 10 <= (n % 100) <= 20:
		return f"{n}th"
	return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _format_round_window(round_no: int) -> str:
	start, end = ROUND_WINDOWS[round_no]
	start_str = f"{_ordinal(start.day)} {start.strftime('%B')}"
	end_str = f"{_ordinal(end.day)} {end.strftime('%B')} {end.year}"
	if start.year != end.year:
		start_str = f"{start_str} {start.year}"
	return f"{start_str} - {end_str}"


def _parse_datetime_utc(date_text: str, time_text: str) -> datetime:
	"""Parse form inputs into an aware UTC datetime.

	Expected:
	  - Date: DD/MM/YYYY (also accepts DD-MM-YYYY)
	  - Time: HH:MM

	Assumes UTC.
	"""

	raw_date = str(date_text or "").strip()
	raw_time = str(time_text or "").strip()
	if not raw_date or not raw_time:
		raise ValueError("Empty datetime")

	# Allow either / or - for the date separator.
	raw_date = raw_date.replace("-", "/")

	if not re.match(r"^\d{2}/\d{2}/\d{4}$", raw_date):
		raise ValueError("Invalid date format")
	if not re.match(r"^\d{2}:\d{2}$", raw_time):
		raise ValueError("Invalid time format")

	dd, mon, yyyy = map(int, raw_date.split("/"))
	hh, mi = map(int, raw_time.split(":"))
	if not (0 <= hh <= 23 and 0 <= mi <= 59):
		raise ValueError("Invalid time")

	d = date(yyyy, mon, dd)
	return datetime.combine(d, time(hh, mi, tzinfo=timezone.utc))


def _within_round(round_no: int, dt_obj: datetime) -> bool:
	start, end = ROUND_WINDOWS[round_no]
	d = dt_obj.date()
	return start <= d <= end


def _team_size_valid(n: int) -> bool:
	return 30 <= n <= 50


def _other_clan(a: str, b: str, who: str) -> str:
	return b if who == a else a


def _midpoints_for_map(map_name: str) -> list[str]:
	mids = MIDPOINTS_BY_MAP.get(map_name)
	if not isinstance(mids, list):
		return []
	if len(mids) != 3:
		return []
	cleaned = [x.strip() for x in mids if isinstance(x, str) and x.strip()]
	return cleaned if len(cleaned) == 3 else []


def _midpoints_config_issues() -> list[str]:
	issues: list[str] = []
	for m in MAP_POOL:
		mids = _midpoints_for_map(m)
		if len(mids) != 3:
			issues.append(m)
	return issues


def _roll_map_and_midpoint(*, avoid: Optional[tuple[str, str]] = None) -> tuple[str, str]:
	"""Roll a (map, midpoint) pair.

	If avoid is provided and there are alternative outcomes available, it will not repeat
	that exact (map, midpoint) pair.
	"""
	valid_maps = [m for m in MAP_POOL if len(_midpoints_for_map(m)) == 3]
	if not valid_maps:
		raise ValueError("No maps have exactly 3 configured midpoints")

	# Build all possible (map, midpoint) outcomes.
	outcomes: list[tuple[str, str]] = []
	for map_name in valid_maps:
		for midpoint in _midpoints_for_map(map_name):
			outcomes.append((map_name, midpoint))

	if not outcomes:
		raise ValueError("No maps have exactly 3 configured midpoints")

	if avoid is not None and len(outcomes) > 1:
		outcomes = [x for x in outcomes if x != avoid] or outcomes

	return secrets.choice(outcomes)


@dataclass
class FixtureState:
	thread_id: int
	clan_a: str
	clan_b: str
	round_no: int

	# Message inside the thread that holds the single "control embed" we keep editing.
	control_message_id: Optional[int] = None

	# proposals
	proposed_datetime_utc: Optional[str] = None
	proposed_datetime_by: Optional[str] = None
	datetime_history: list[dict[str, Any]] = field(default_factory=list)

	agreed_datetime_utc: Optional[str] = None

	proposed_team_size: Optional[int] = None
	proposed_team_size_by: Optional[str] = None
	team_size_history: list[dict[str, Any]] = field(default_factory=list)
	agreed_team_size: Optional[int] = None

	proposed_streamer: Optional[bool] = None
	proposed_streamer_by: Optional[str] = None
	streamer_history: list[dict[str, Any]] = field(default_factory=list)
	agreed_streamer: Optional[bool] = None

	current_map: Optional[str] = None
	current_midpoint: Optional[str] = None

	# Map & midpoint negotiation
	proposed_map: Optional[str] = None
	proposed_midpoint: Optional[str] = None
	proposed_map_by: Optional[str] = None
	map_history: list[dict[str, Any]] = field(default_factory=list)

	# Map/midpoint rerolls ("mix-ups")
	reroll_count_a: int = 0
	reroll_count_b: int = 0
	last_map_roll_by: Optional[str] = None

	# Sides rerolls (separate from map/midpoint mix-ups)
	sides_reroll_count_a: int = 0
	sides_reroll_count_b: int = 0

	# Sides negotiation
	proposed_sides_allies: Optional[str] = None
	proposed_sides_axis: Optional[str] = None
	proposed_sides_by: Optional[str] = None
	proposed_server_host: Optional[str] = None
	sides_history: list[dict[str, Any]] = field(default_factory=list)

	sides_allies: Optional[str] = None
	sides_axis: Optional[str] = None
	sides_decided_by: Optional[str] = None
	server_host: Optional[str] = None

	scheduled_event_id: Optional[int] = None

	# Snapshot of what was agreed at the time of finalisation (so later event edits don't lose the original agreement)
	agreement_snapshot_message_id: Optional[int] = None
	agreement_snapshot_text: Optional[str] = None

	@property
	def key(self) -> str:
		return str(self.thread_id)


def _state_to_dict(s: FixtureState) -> dict[str, Any]:
	return {
		"thread_id": s.thread_id,
		"clan_a": s.clan_a,
		"clan_b": s.clan_b,
		"round_no": s.round_no,
		"control_message_id": s.control_message_id,
		"proposed_datetime_utc": s.proposed_datetime_utc,
		"proposed_datetime_by": s.proposed_datetime_by,
		"datetime_history": s.datetime_history,
		"agreed_datetime_utc": s.agreed_datetime_utc,
		"proposed_team_size": s.proposed_team_size,
		"proposed_team_size_by": s.proposed_team_size_by,
		"team_size_history": s.team_size_history,
		"agreed_team_size": s.agreed_team_size,
		"proposed_streamer": s.proposed_streamer,
		"proposed_streamer_by": s.proposed_streamer_by,
		"streamer_history": s.streamer_history,
		"agreed_streamer": s.agreed_streamer,
		"current_map": s.current_map,
		"current_midpoint": s.current_midpoint,
		"proposed_map": s.proposed_map,
		"proposed_midpoint": s.proposed_midpoint,
		"proposed_map_by": s.proposed_map_by,
		"map_history": s.map_history,
		"reroll_count_a": s.reroll_count_a,
		"reroll_count_b": s.reroll_count_b,
		"last_map_roll_by": s.last_map_roll_by,
		"sides_reroll_count_a": s.sides_reroll_count_a,
		"sides_reroll_count_b": s.sides_reroll_count_b,
		"proposed_sides_allies": s.proposed_sides_allies,
		"proposed_sides_axis": s.proposed_sides_axis,
		"proposed_sides_by": s.proposed_sides_by,
		"proposed_server_host": s.proposed_server_host,
		"sides_history": s.sides_history,
		"sides_allies": s.sides_allies,
		"sides_axis": s.sides_axis,
		"sides_decided_by": s.sides_decided_by,
		"server_host": s.server_host,
		"scheduled_event_id": s.scheduled_event_id,
		"agreement_snapshot_message_id": s.agreement_snapshot_message_id,
		"agreement_snapshot_text": s.agreement_snapshot_text,
	}


def _dict_to_state(d: dict[str, Any]) -> FixtureState:
	return FixtureState(
		thread_id=int(d["thread_id"]),
		clan_a=str(d["clan_a"]),
		clan_b=str(d["clan_b"]),
		round_no=int(d["round_no"]),
		control_message_id=d.get("control_message_id"),
		proposed_datetime_utc=d.get("proposed_datetime_utc"),
		proposed_datetime_by=d.get("proposed_datetime_by"),
		datetime_history=d.get("datetime_history") or [],
		agreed_datetime_utc=d.get("agreed_datetime_utc"),
		proposed_team_size=d.get("proposed_team_size"),
		proposed_team_size_by=d.get("proposed_team_size_by"),
		team_size_history=d.get("team_size_history") or [],
		agreed_team_size=d.get("agreed_team_size"),
		proposed_streamer=d.get("proposed_streamer"),
		proposed_streamer_by=d.get("proposed_streamer_by"),
		streamer_history=d.get("streamer_history") or [],
		agreed_streamer=d.get("agreed_streamer"),
		current_map=d.get("current_map"),
		current_midpoint=d.get("current_midpoint"),
		proposed_map=d.get("proposed_map"),
		proposed_midpoint=d.get("proposed_midpoint"),
		proposed_map_by=d.get("proposed_map_by"),
		map_history=d.get("map_history") or [],
		reroll_count_a=int(d.get("reroll_count_a", 0)),
		reroll_count_b=int(d.get("reroll_count_b", 0)),
		last_map_roll_by=d.get("last_map_roll_by"),
		sides_reroll_count_a=int(d.get("sides_reroll_count_a", 0)),
		sides_reroll_count_b=int(d.get("sides_reroll_count_b", 0)),
		proposed_sides_allies=d.get("proposed_sides_allies"),
		proposed_sides_axis=d.get("proposed_sides_axis"),
		proposed_sides_by=d.get("proposed_sides_by"),
		proposed_server_host=d.get("proposed_server_host"),
		sides_history=d.get("sides_history") or [],
		sides_allies=d.get("sides_allies"),
		sides_axis=d.get("sides_axis"),
		sides_decided_by=d.get("sides_decided_by"),
		server_host=d.get("server_host"),
		scheduled_event_id=d.get("scheduled_event_id"),
		agreement_snapshot_message_id=d.get("agreement_snapshot_message_id"),
		agreement_snapshot_text=d.get("agreement_snapshot_text"),
	)


def _agreement_snapshot_text(s: FixtureState, *, event_url: Optional[str] = None) -> str:
	parts: list[str] = []
	parts.append("AGREEMENT SNAPSHOT (do not edit)")
	parts.append(f"Fixture: {s.clan_a} vs {s.clan_b}")
	parts.append(f"Round: {s.round_no} ({_format_round_window(s.round_no)})")
	if s.agreed_datetime_utc:
		try:
			dt = datetime.fromisoformat(s.agreed_datetime_utc)
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
			dt = dt.astimezone(timezone.utc)
			parts.append(f"Start (UTC): {dt.strftime('%d/%m/%Y %H:%M')} UTC")
			parts.append(f"Start (Discord): <t:{int(dt.timestamp())}:F>")
		except Exception:
			parts.append(f"Start (UTC): {s.agreed_datetime_utc}")
	if s.agreed_team_size:
		parts.append(f"Team size: {s.agreed_team_size} vs {s.agreed_team_size}")
	if s.agreed_streamer is not None:
		parts.append(f"Streamer: {'Yes' if s.agreed_streamer else 'No'}")
	if ENABLE_MAP_MIDPOINT:
		if s.current_map:
			parts.append(f"Map: {s.current_map}")
		if s.current_midpoint:
			parts.append(f"Midpoint: {s.current_midpoint}")
		parts.append(
			f"Map rerolls used: {s.clan_a} {s.reroll_count_a}/{REROLL_LIMIT} • {s.clan_b} {s.reroll_count_b}/{REROLL_LIMIT}"
		)
	if ENABLE_SIDES:
		if s.sides_allies and s.sides_axis:
			parts.append(f"Allies: {s.sides_allies}")
			parts.append(f"Axis: {s.sides_axis}")
		if s.server_host:
			parts.append(f"Server host: {s.server_host} Server")
		parts.append(
			f"Sides rerolls used: {s.clan_a} {s.sides_reroll_count_a}/{REROLL_LIMIT} • {s.clan_b} {s.sides_reroll_count_b}/{REROLL_LIMIT}"
		)
	parts.append(f"Thread: <#{s.thread_id}>")
	if event_url:
		parts.append(f"Event: {event_url}")
	parts.append(f"Snapshot saved (UTC): {datetime.now(timezone.utc).isoformat()}")

	# Keep it readable and scannable.
	return "**" + parts[0] + "**\n```\n" + "\n".join(parts[1:]) + "\n```"


def _find_user_clan(member: discord.Member) -> Optional[str]:
	hits: list[str] = []
	for clan, role_id in CLAN_ROLE_IDS.items():
		if isinstance(role_id, int) and role_id > 0 and any(r.id == role_id for r in member.roles):
			hits.append(clan)
	if len(hits) == 1:
		return hits[0]
	return None


def _clan_role(guild: discord.Guild, clan: str) -> Optional[discord.Role]:
	rid = CLAN_ROLE_IDS.get(clan)
	if not isinstance(rid, int) or rid <= 0:
		return None
	return guild.get_role(rid)


def _reroll_count_for(s: FixtureState, clan: str) -> int:
	return s.reroll_count_a if clan == s.clan_a else s.reroll_count_b


def _inc_reroll(s: FixtureState, clan: str) -> None:
	if clan == s.clan_a:
		s.reroll_count_a += 1
	else:
		s.reroll_count_b += 1


def _sides_reroll_count_for(s: FixtureState, clan: str) -> int:
	return s.sides_reroll_count_a if clan == s.clan_a else s.sides_reroll_count_b


def _inc_sides_reroll(s: FixtureState, clan: str) -> None:
	if clan == s.clan_a:
		s.sides_reroll_count_a += 1
	else:
		s.sides_reroll_count_b += 1


def _format_dt_short(dt_iso: str) -> str:
	try:
		dt = datetime.fromisoformat(dt_iso)
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		dt = dt.astimezone(timezone.utc)
		return dt.strftime("%d/%m/%Y %H:%M") + " UTC"
	except Exception:
		return dt_iso


def _history_lines(items: list[dict[str, Any]], *, kind: str, limit: int = 6) -> str:
	"""Render last N history items as a code block-friendly list."""
	if not items:
		return "(no history yet)"
	lines: list[str] = []
	for entry in items[-limit:]:
		by = str(entry.get("by", "?"))
		action = str(entry.get("action", "proposed"))
		if kind == "dt":
			val = entry.get("dt")
			val_s = _format_dt_short(str(val)) if val else "?"
			lines.append(f"- {by} {action}: {val_s}")
		elif kind == "size":
			val = entry.get("size")
			lines.append(f"- {by} {action}: {val}v{val}")
		elif kind == "streamer":
			val = entry.get("streamer")
			if isinstance(val, bool):
				val_s = "Yes" if val else "No"
			else:
				val_s = "?"
			lines.append(f"- {by} {action}: {val_s}")
		elif kind == "map":
			m = entry.get("map")
			mid = entry.get("mid")
			lines.append(f"- {by} {action}: {m} / {mid}")
		elif kind == "sides":
			allies = entry.get("allies")
			axis = entry.get("axis")
			host = entry.get("host")
			host_s = f" / Host {host} Server" if host else ""
			lines.append(f"- {by} {action}: Allies {allies} / Axis {axis}{host_s}")
		else:
			lines.append(f"- {by} {action}")
	return "\n".join(lines)


def _fixture_title(s: FixtureState) -> str:
	return f"Round {s.round_no}: {s.clan_a} vs {s.clan_b}"


def _fixture_embed(s: FixtureState) -> discord.Embed:
	embed = discord.Embed(
		title="Fixture Organisation",
		description=(
			"Use the buttons below to organise the fixture end-to-end.\n"
		),
		color=discord.Color.blurple(),
	)

	embed.add_field(name="Fixture", value=_fixture_title(s), inline=False)
	embed.add_field(name="Round Window", value=_format_round_window(s.round_no), inline=False)

	# Date/time (status + history)
	dt_hist = _history_lines(s.datetime_history, kind="dt")
	embed.add_field(name="Date/Time", value=f"```\n{dt_hist}\n```", inline=False)

	# Team size (status + history)
	size_hist = _history_lines(s.team_size_history, kind="size")
	embed.add_field(name="Team Size", value=f"```\n{size_hist}\n```", inline=False)

	# Streamer (status + history)
	streamer_hist = _history_lines(s.streamer_history, kind="streamer")
	embed.add_field(name="Streamer", value=f"```\n{streamer_hist}\n```", inline=False)

	# Map/midpoint (status + history)
	if ENABLE_MAP_MIDPOINT:
		rerolls_line = f"Map rerolls: {s.clan_a} {s.reroll_count_a}/{REROLL_LIMIT} • {s.clan_b} {s.reroll_count_b}/{REROLL_LIMIT}"
		map_hist = _history_lines(s.map_history, kind="map")
		embed.add_field(name="Map & Midpoint", value=f"```\n{rerolls_line}\n{map_hist}\n```", inline=False)

	# Sides (status + history)
	if ENABLE_SIDES:
		sides_rerolls_line = f"Sides rerolls: {s.clan_a} {s.sides_reroll_count_a}/{REROLL_LIMIT} • {s.clan_b} {s.sides_reroll_count_b}/{REROLL_LIMIT}"
		sides_hist = _history_lines(s.sides_history, kind="sides")
		embed.add_field(name="Sides", value=f"```\n{sides_rerolls_line}\n{sides_hist}\n```", inline=False)

	if s.scheduled_event_id:
		embed.add_field(name="Discord Event", value=f"Created (ID: {s.scheduled_event_id})", inline=False)

	return embed


# =============================
# UI
# =============================


class OrganiseFixtureButton(discord.ui.Button):
	def __init__(self):
		super().__init__(
			label="Organise Fixture",
			style=discord.ButtonStyle.primary,
			custom_id="fixture:organise",
		)

	async def callback(self, interaction: discord.Interaction):
		if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
			await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
			return

		clan = _find_user_clan(interaction.user)
		if not clan:
			await interaction.response.send_message(
				"You must have exactly one configured clan role to use this.",
				ephemeral=True,
			)
			return

		view = OpponentRoundView(requester_clan=clan)
		await interaction.response.send_message(
			"Select the opposing clan and round:",
			view=view,
			ephemeral=True,
		)


class OrganiserHomeView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)
		self.add_item(OrganiseFixtureButton())


class OpponentSelect(discord.ui.Select):
	def __init__(self, requester_clan: str):
		options = []
		for clan in CLAN_ROLE_IDS.keys():
			if clan != requester_clan:
				options.append(discord.SelectOption(label=clan, value=clan))
		super().__init__(
			placeholder="Choose opposing clan",
			min_values=1,
			max_values=1,
			options=options,
		)

	async def callback(self, interaction: discord.Interaction):
		view = self.view
		if isinstance(view, OpponentRoundView) and self.values:
			selected = self.values[0]
			view.opponent_clan = selected
			# Persist the selection visually when we edit the message.
			for opt in self.options:
				opt.default = (opt.value == selected)
			# Acknowledge the selection to avoid "This interaction failed".
			await interaction.response.edit_message(view=view)
			return
		await interaction.response.defer()


class RoundSelect(discord.ui.Select):
	def __init__(self):
		options = [
			discord.SelectOption(label=f"Round {n} ({_format_round_window(n)})", value=str(n))
			for n in sorted(ROUND_WINDOWS.keys())
		]
		super().__init__(
			placeholder="Choose round",
			min_values=1,
			max_values=1,
			options=options,
		)

	async def callback(self, interaction: discord.Interaction):
		view = self.view
		if isinstance(view, OpponentRoundView) and self.values:
			selected = self.values[0]
			try:
				view.round_no = int(selected)
			except Exception:
				view.round_no = None
			for opt in self.options:
				opt.default = (opt.value == selected)
			await interaction.response.edit_message(view=view)
			return
		await interaction.response.defer()


class CreateThreadButton(discord.ui.Button):
	def __init__(self):
		super().__init__(label="Create Fixture Thread", style=discord.ButtonStyle.success)

	async def callback(self, interaction: discord.Interaction):
		view: OpponentRoundView = self.view  # type: ignore[assignment]
		if not isinstance(view, OpponentRoundView):
			await interaction.response.send_message("Internal error.", ephemeral=True)
			return

		if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
			await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
			return

		if view.opponent_clan is None or view.round_no is None:
			await interaction.response.send_message("Select opponent + round first.", ephemeral=True)
			return

		requester_clan = view.requester_clan
		opponent_clan = view.opponent_clan
		round_no = view.round_no

		parent = interaction.guild.get_channel(THREAD_PARENT_CHANNEL_ID)
		if not isinstance(parent, discord.TextChannel):
			await interaction.response.send_message("Thread parent channel not found/configured.", ephemeral=True)
			return

		# This operation can take longer than 3 seconds (thread creation + invites), so defer.
		await interaction.response.defer(ephemeral=True, thinking=True)

		thread_name = f"R{round_no} {requester_clan} vs {opponent_clan}"
		if len(thread_name) > 100:
			thread_name = thread_name[:97] + "..."

		# Create a private thread so only invited members can see.
		try:
			thread = await parent.create_thread(
				name=thread_name,
				type=discord.ChannelType.private_thread,
				auto_archive_duration=10080,
			)
		except discord.Forbidden:
			await interaction.followup.send(
				"I don't have permission to create private threads here.",
				ephemeral=True,
			)
			return
		except Exception as e:
			await interaction.followup.send(f"Failed to create thread: {e}", ephemeral=True)
			return

		# Invite members of both clan roles (best-effort).
		await thread.add_user(interaction.user)
		clan_a_role = _clan_role(interaction.guild, requester_clan)
		clan_b_role = _clan_role(interaction.guild, opponent_clan)
		invited = 0
		for role in [clan_a_role, clan_b_role]:
			if role is None:
				continue
			for member in role.members:
				if member.bot:
					continue
				try:
					await thread.add_user(member)
					invited += 1
				except Exception:
					continue

		s = FixtureState(thread_id=thread.id, clan_a=requester_clan, clan_b=opponent_clan, round_no=round_no)

		state = _load_state()
		state["threads"][s.key] = _state_to_dict(s)
		_save_state(state)

		control_view = FixtureThreadView(thread_id=thread.id)
		embed = _fixture_embed(s)
		msg = await thread.send(content=f"{requester_clan} vs {opponent_clan}", embed=embed, view=control_view)
		s.control_message_id = msg.id
		state = _load_state()
		state["threads"][s.key] = _state_to_dict(s)
		_save_state(state)
		# Register the view so the buttons keep working after restarts.
		try:
			if hasattr(interaction.client, "add_view"):
				interaction.client.add_view(control_view, message_id=msg.id)  # type: ignore[attr-defined]
		except Exception:
			pass

		await interaction.followup.send(
			f"Thread created: {thread.mention} (invited {invited} members)",
			ephemeral=True,
		)


class OpponentRoundView(discord.ui.View):
	def __init__(self, requester_clan: str):
		super().__init__(timeout=300)
		self.requester_clan = requester_clan
		self.opponent_clan: Optional[str] = None
		self.round_no: Optional[int] = None

		self.opp_select = OpponentSelect(requester_clan=requester_clan)
		self.round_select = RoundSelect()
		self.add_item(self.opp_select)
		self.add_item(self.round_select)
		self.add_item(CreateThreadButton())

	# Selects handle state updates via their callbacks.


class DateTimeModal(discord.ui.Modal, title="Propose Date/Time (UTC)"):
	date_field = discord.ui.TextInput(
		label="Date",
		placeholder="DD/MM/YYYY",
		required=True,
		max_length=10,
	)
	time_field = discord.ui.TextInput(
		label="Time (UTC)",
		placeholder="HH:MM",
		required=True,
		max_length=5,
	)

	def __init__(self, thread_id: int):
		super().__init__()
		self.thread_id = thread_id

	async def on_submit(self, interaction: discord.Interaction):
		if interaction.guild is None or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Server only.", ephemeral=True)
			return

		state = _load_state()
		raw = state.get("threads", {}).get(str(self.thread_id))
		if not isinstance(raw, dict):
			await interaction.response.send_message("Fixture state not found.", ephemeral=True)
			return

		s = _dict_to_state(raw)
		if s.agreed_datetime_utc:
			await interaction.response.send_message("Date/time is already locked.", ephemeral=True)
			return
		user_clan = _find_user_clan(interaction.user)
		if user_clan not in (s.clan_a, s.clan_b):
			await interaction.response.send_message("You are not part of this fixture.", ephemeral=True)
			return

		try:
			dt_utc = _parse_datetime_utc(str(self.date_field.value), str(self.time_field.value))
		except ValueError:
			await interaction.response.send_message(
				"Invalid date/time. Use `DD/MM/YYYY` and `HH:MM` (UTC).",
				ephemeral=True,
			)
			return

		if not _within_round(s.round_no, dt_utc):
			await interaction.response.send_message(
				f"That time is outside the Round {s.round_no} window ({_format_round_window(s.round_no)}).",
				ephemeral=True,
			)
			return

		s.proposed_datetime_utc = dt_utc.replace(tzinfo=timezone.utc).isoformat()
		s.proposed_datetime_by = user_clan
		s.agreed_datetime_utc = None
		s.datetime_history.append(
			{
				"by": user_clan,
				"action": "proposed",
				"dt": s.proposed_datetime_utc,
			}
		)

		state["threads"][s.key] = _state_to_dict(s)
		_save_state(state)

		await interaction.response.send_message("Date/time proposal recorded.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))


class TeamSizeModal(discord.ui.Modal, title="Propose Team Size"):
	size = discord.ui.TextInput(
		label="Players per team",
		placeholder="30-50",
		required=True,
		max_length=3,
	)

	def __init__(self, thread_id: int):
		super().__init__()
		self.thread_id = thread_id

	async def on_submit(self, interaction: discord.Interaction):
		if interaction.guild is None or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Server only.", ephemeral=True)
			return

		state = _load_state()
		raw = state.get("threads", {}).get(str(self.thread_id))
		if not isinstance(raw, dict):
			await interaction.response.send_message("Fixture state not found.", ephemeral=True)
			return

		s = _dict_to_state(raw)
		if s.agreed_team_size is not None:
			await interaction.response.send_message("Team size is already locked.", ephemeral=True)
			return
		user_clan = _find_user_clan(interaction.user)
		if user_clan not in (s.clan_a, s.clan_b):
			await interaction.response.send_message("You are not part of this fixture.", ephemeral=True)
			return

		try:
			n = int(str(self.size.value).strip())
		except Exception:
			await interaction.response.send_message("Enter a number between 30 and 50.", ephemeral=True)
			return

		if not _team_size_valid(n):
			await interaction.response.send_message("Team size must be between 30 and 50.", ephemeral=True)
			return

		s.proposed_team_size = n
		action = "proposed"
		if s.proposed_team_size_by and s.proposed_team_size_by != user_clan:
			action = "countered"
		s.proposed_team_size_by = user_clan
		s.agreed_team_size = None
		s.team_size_history.append({"by": user_clan, "action": action, "size": n})

		state["threads"][s.key] = _state_to_dict(s)
		_save_state(state)
		await interaction.response.send_message("Team size proposal recorded.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))


class FixtureThreadView(discord.ui.View):
	def __init__(self, thread_id: int):
		super().__init__(timeout=None)
		self.thread_id = thread_id

		# Remove disabled feature buttons from the UI.
		# (The handlers still exist for safety, but users won't see/click the buttons.)
		if not ENABLE_MAP_MIDPOINT:
			try:
				self.remove_item(self.roll_map)
				self.remove_item(self.accept_map)
			except Exception:
				pass
		if not ENABLE_SIDES:
			try:
				self.remove_item(self.propose_sides)
				self.remove_item(self.accept_sides)
			except Exception:
				pass

	async def _get_state(self) -> Optional[FixtureState]:
		state = _load_state()
		raw = state.get("threads", {}).get(str(self.thread_id))
		if not isinstance(raw, dict):
			return None
		return _dict_to_state(raw)

	async def _require_member(self, interaction: discord.Interaction) -> Optional[tuple[FixtureState, str]]:
		if interaction.guild is None or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Server only.", ephemeral=True)
			return None
		s = await self._get_state()
		if s is None:
			await interaction.response.send_message("Fixture state missing.", ephemeral=True)
			return None
		clan = _find_user_clan(interaction.user)
		if clan not in (s.clan_a, s.clan_b):
			await interaction.response.send_message("You are not part of this fixture.", ephemeral=True)
			return None
		return s, clan

	@discord.ui.button(label="Propose date/time", style=discord.ButtonStyle.primary, custom_id="fixture:dt_propose")
	async def propose_datetime(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, _ = res
		if s.agreed_datetime_utc:
			await interaction.response.send_message("Date/time is already locked.", ephemeral=True)
			return
		await interaction.response.send_modal(DateTimeModal(thread_id=s.thread_id))

	@discord.ui.button(label="Accept date/time", style=discord.ButtonStyle.success, custom_id="fixture:dt_accept")
	async def accept_datetime(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if not s.proposed_datetime_utc or not s.proposed_datetime_by:
			await interaction.response.send_message("No date/time proposal to accept.", ephemeral=True)
			return
		if clan == s.proposed_datetime_by:
			await interaction.response.send_message("The other clan must accept/counter.", ephemeral=True)
			return
		s.agreed_datetime_utc = s.proposed_datetime_utc
		s.datetime_history.append(
			{"by": clan, "action": "accepted", "dt": s.agreed_datetime_utc}
		)
		s.proposed_datetime_utc = None
		s.proposed_datetime_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Date/time agreed.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Propose team size", style=discord.ButtonStyle.primary, custom_id="fixture:size_propose")
	async def propose_size(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, _ = res
		if s.agreed_team_size is not None:
			await interaction.response.send_message("Team size is already locked.", ephemeral=True)
			return
		await interaction.response.send_modal(TeamSizeModal(thread_id=s.thread_id))

	@discord.ui.button(label="Accept team size", style=discord.ButtonStyle.success, custom_id="fixture:size_accept")
	async def accept_size(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.proposed_team_size is None or s.proposed_team_size_by is None:
			await interaction.response.send_message("No team size proposal to accept.", ephemeral=True)
			return
		if clan == s.proposed_team_size_by:
			await interaction.response.send_message("The other clan must accept/counter.", ephemeral=True)
			return
		s.agreed_team_size = s.proposed_team_size
		s.team_size_history.append({"by": clan, "action": "accepted", "size": s.agreed_team_size})
		s.proposed_team_size = None
		s.proposed_team_size_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Team size agreed.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Propose streamer: Yes", style=discord.ButtonStyle.primary, custom_id="fixture:streamer_yes")
	async def propose_streamer_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.agreed_streamer is not None:
			await interaction.response.send_message("Streamer setting is already locked.", ephemeral=True)
			return
		s.proposed_streamer = True
		action = "proposed"
		if s.proposed_streamer_by and s.proposed_streamer_by != clan:
			action = "countered"
		s.proposed_streamer_by = clan
		s.agreed_streamer = None
		s.streamer_history.append({"by": clan, "action": action, "streamer": True})
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Streamer proposal recorded (Yes).", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Propose streamer: No", style=discord.ButtonStyle.primary, custom_id="fixture:streamer_no")
	async def propose_streamer_no(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.agreed_streamer is not None:
			await interaction.response.send_message("Streamer setting is already locked.", ephemeral=True)
			return
		s.proposed_streamer = False
		action = "proposed"
		if s.proposed_streamer_by and s.proposed_streamer_by != clan:
			action = "countered"
		s.proposed_streamer_by = clan
		s.agreed_streamer = None
		s.streamer_history.append({"by": clan, "action": action, "streamer": False})
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Streamer proposal recorded (No).", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Accept streamer", style=discord.ButtonStyle.success, custom_id="fixture:streamer_accept")
	async def accept_streamer(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.proposed_streamer is None or s.proposed_streamer_by is None:
			await interaction.response.send_message("No streamer proposal to accept.", ephemeral=True)
			return
		if clan == s.proposed_streamer_by:
			await interaction.response.send_message("The other clan must accept/counter.", ephemeral=True)
			return
		s.agreed_streamer = bool(s.proposed_streamer)
		s.streamer_history.append({"by": clan, "action": "accepted", "streamer": s.agreed_streamer})
		s.proposed_streamer = None
		s.proposed_streamer_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Streamer setting agreed.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Roll / Mix-up map+mid", style=discord.ButtonStyle.primary, custom_id="fixture:map_roll")
	async def roll_map(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not ENABLE_MAP_MIDPOINT:
			await interaction.response.send_message("Map/midpoint is disabled by config.", ephemeral=True)
			return
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.scheduled_event_id:
			await interaction.response.send_message("Event already created; fixture is locked.", ephemeral=True)
			return
		if s.current_map and s.current_midpoint:
			await interaction.response.send_message("Map/midpoint is already locked.", ephemeral=True)
			return
		if not MAP_POOL:
			await interaction.response.send_message("MAP_POOL is empty.", ephemeral=True)
			return
		issues = _midpoints_config_issues()
		if issues:
			await interaction.response.send_message(
				"Midpoint config is incomplete. Each map must have exactly 3 midpoints in MIDPOINTS_BY_MAP. Missing/invalid: "
				+ ", ".join(issues),
				ephemeral=True,
			)
			return
		# Each clan may request up to REROLL_LIMIT mix-ups before the map is accepted/locked.
		# The initial roll doesn't consume a reroll; subsequent mix-ups do.
		is_first_proposal = s.proposed_map is None
		if not is_first_proposal and _reroll_count_for(s, clan) >= REROLL_LIMIT:
			await interaction.response.send_message("You have used all mix-ups.", ephemeral=True)
			return
		try:
			avoid = None
			if s.proposed_map and s.proposed_midpoint:
				avoid = (s.proposed_map, s.proposed_midpoint)
			elif s.current_map and s.current_midpoint:
				avoid = (s.current_map, s.current_midpoint)
			new_map, new_mid = _roll_map_and_midpoint(avoid=avoid)
		except ValueError:
			await interaction.response.send_message(
				"No valid maps to roll: every map in MAP_POOL must have exactly 3 midpoints configured.",
				ephemeral=True,
			)
			return
		s.proposed_map = new_map
		s.proposed_midpoint = new_mid
		s.proposed_map_by = clan
		s.last_map_roll_by = clan
		s.map_history.append({"by": clan, "action": "proposed", "map": new_map, "mid": new_mid})
		if not is_first_proposal:
			_inc_reroll(s, clan)
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Map/midpoint proposal updated.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Accept map+mid", style=discord.ButtonStyle.success, custom_id="fixture:map_accept")
	async def accept_map(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not ENABLE_MAP_MIDPOINT:
			await interaction.response.send_message("Map/midpoint is disabled by config.", ephemeral=True)
			return
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.scheduled_event_id:
			await interaction.response.send_message("Event already created; fixture is locked.", ephemeral=True)
			return
		if s.current_map and s.current_midpoint:
			await interaction.response.send_message("Map/midpoint is already locked.", ephemeral=True)
			return
		if not (s.proposed_map and s.proposed_midpoint and s.proposed_map_by):
			await interaction.response.send_message("No map/midpoint proposal to accept.", ephemeral=True)
			return
		if clan == s.proposed_map_by:
			await interaction.response.send_message("The other clan must accept.", ephemeral=True)
			return
		s.current_map = s.proposed_map
		s.current_midpoint = s.proposed_midpoint
		s.map_history.append({"by": clan, "action": "accepted", "map": s.current_map, "mid": s.current_midpoint})
		s.proposed_map = None
		s.proposed_midpoint = None
		s.proposed_map_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Map/midpoint locked.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Propose sides (coin flip)", style=discord.ButtonStyle.primary, custom_id="fixture:sides_propose")
	async def propose_sides(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not ENABLE_SIDES:
			await interaction.response.send_message("Sides is disabled by config.", ephemeral=True)
			return
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.scheduled_event_id:
			await interaction.response.send_message("Event already created; fixture is locked.", ephemeral=True)
			return
		if s.sides_allies or s.sides_axis:
			await interaction.response.send_message("Sides are already locked.", ephemeral=True)
			return
		# Each clan may request up to REROLL_LIMIT rerolls before sides are accepted/locked.
		# The initial coin flip doesn't consume a reroll; subsequent flips do.
		is_first_proposal = s.proposed_sides_allies is None
		if not is_first_proposal and _sides_reroll_count_for(s, clan) >= REROLL_LIMIT:
			await interaction.response.send_message("You have used all sides rerolls.", ephemeral=True)
			return
		# Avoid repeating the exact same (allies, axis, host) proposal when alternatives exist.
		last = s.sides_history[-1] if s.sides_history else None
		avoid_allies = str(last.get("allies")) if isinstance(last, dict) and last.get("allies") else None
		avoid_axis = str(last.get("axis")) if isinstance(last, dict) and last.get("axis") else None
		avoid_host = str(last.get("host")) if isinstance(last, dict) and last.get("host") else None
		avoid = (avoid_allies, avoid_axis, avoid_host) if (avoid_allies and avoid_axis and avoid_host) else None

		possible: list[tuple[str, str, str]] = [
			(s.clan_a, s.clan_b, s.clan_a),
			(s.clan_a, s.clan_b, s.clan_b),
			(s.clan_b, s.clan_a, s.clan_a),
			(s.clan_b, s.clan_a, s.clan_b),
		]
		if avoid is not None and len(possible) > 1:
			possible = [x for x in possible if x != avoid] or possible
		allies, axis, host = secrets.choice(possible)
		s.proposed_sides_allies = allies
		s.proposed_sides_axis = axis
		s.proposed_sides_by = clan
		s.proposed_server_host = host
		s.sides_history.append({"by": clan, "action": "proposed", "allies": allies, "axis": axis, "host": host})
		if not is_first_proposal:
			_inc_sides_reroll(s, clan)
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Sides proposal updated.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Accept sides", style=discord.ButtonStyle.success, custom_id="fixture:sides_accept")
	async def accept_sides(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not ENABLE_SIDES:
			await interaction.response.send_message("Sides is disabled by config.", ephemeral=True)
			return
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.scheduled_event_id:
			await interaction.response.send_message("Event already created; fixture is locked.", ephemeral=True)
			return
		if s.sides_allies or s.sides_axis:
			await interaction.response.send_message("Sides are already locked.", ephemeral=True)
			return
		if not (s.proposed_sides_allies and s.proposed_sides_axis and s.proposed_sides_by and s.proposed_server_host):
			await interaction.response.send_message("No sides proposal to accept.", ephemeral=True)
			return
		if clan == s.proposed_sides_by:
			await interaction.response.send_message("The other clan must accept.", ephemeral=True)
			return
		s.sides_allies = s.proposed_sides_allies
		s.sides_axis = s.proposed_sides_axis
		s.sides_decided_by = clan
		s.server_host = s.proposed_server_host
		s.sides_history.append({"by": clan, "action": "accepted", "allies": s.sides_allies, "axis": s.sides_axis, "host": s.server_host})
		s.proposed_sides_allies = None
		s.proposed_sides_axis = None
		s.proposed_sides_by = None
		s.proposed_server_host = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Sides locked.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Create Discord Event", style=discord.ButtonStyle.success, custom_id="fixture:event")
	async def create_event(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, _ = res

		# Event creation + snapshot posting can take longer than 3 seconds.
		await interaction.response.defer(ephemeral=True, thinking=True)
		if s.scheduled_event_id:
			await interaction.followup.send("Event already created.", ephemeral=True)
			return
		if not s.agreed_datetime_utc:
			await interaction.followup.send("Agree the date/time first.", ephemeral=True)
			return
		if not s.agreed_team_size:
			await interaction.followup.send("Agree the team size first.", ephemeral=True)
			return
		if ENABLE_MAP_MIDPOINT and not (s.current_map and s.current_midpoint):
			await interaction.followup.send("Roll map/midpoint first.", ephemeral=True)
			return
		if ENABLE_SIDES and not (s.sides_allies and s.sides_axis):
			await interaction.followup.send("Decide sides first.", ephemeral=True)
			return
		if ENABLE_SIDES and not s.server_host:
			await interaction.followup.send("Select sides first (server host is chosen with the coin flip).", ephemeral=True)
			return
		if interaction.guild is None:
			await interaction.followup.send("Server only.", ephemeral=True)
			return

		guild = interaction.guild
		if guild.id != SCHEDULED_EVENT_GUILD_ID:
			await interaction.followup.send("This interaction is in the wrong guild for event creation.", ephemeral=True)
			return

		start_dt = datetime.fromisoformat(s.agreed_datetime_utc)
		if start_dt.tzinfo is None:
			start_dt = start_dt.replace(tzinfo=timezone.utc)
		start_dt = start_dt.astimezone(timezone.utc)
		end_dt = start_dt + timedelta(hours=2)

		desc_lines: list[str] = [
			f"Team size: {s.agreed_team_size} vs {s.agreed_team_size}",
		]
		if s.agreed_streamer is not None:
			desc_lines.append(f"Streamer: {'Yes' if s.agreed_streamer else 'No'}")
		if ENABLE_MAP_MIDPOINT and s.current_map and s.current_midpoint:
			desc_lines.append(f"Map: {s.current_map}")
			desc_lines.append(f"Midpoint: {s.current_midpoint}")
		if ENABLE_SIDES and s.sides_allies and s.sides_axis:
			desc_lines.append(f"Allies: {s.sides_allies}")
			desc_lines.append(f"Axis: {s.sides_axis}")
		desc_lines.append(f"Thread: <#{s.thread_id}>")
		desc = "\n".join(desc_lines)

		location = f"{s.server_host} Server" if s.server_host else EVENT_LOCATION_FALLBACK

		try:
			if SCHEDULED_EVENT_CHANNEL_ID:
				channel = guild.get_channel(SCHEDULED_EVENT_CHANNEL_ID)
				if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
					await interaction.followup.send("Configured event channel is invalid.", ephemeral=True)
					return
				# Voice/stage events don't have a location field, so include host in the description.
				ev = await guild.create_scheduled_event(
					name=_fixture_title(s),
					start_time=start_dt,
					end_time=end_dt,
					privacy_level=discord.PrivacyLevel.guild_only,
					entity_type=(
						discord.EntityType.stage_instance
						if isinstance(channel, discord.StageChannel)
						else discord.EntityType.voice
					),
					channel=channel,
					description=(
						f"{desc}\n**Server host:** {location}"
						if location
						else desc
					),
				)
			else:
				ev = await guild.create_scheduled_event(
					name=_fixture_title(s),
					start_time=start_dt,
					end_time=end_dt,
					privacy_level=discord.PrivacyLevel.guild_only,
					entity_type=discord.EntityType.external,
					location=location,
					description=desc,
				)
		except discord.Forbidden:
			await interaction.followup.send("Missing permissions to create scheduled events.", ephemeral=True)
			return
		except Exception as e:
			await interaction.followup.send(f"Failed to create event: {e}", ephemeral=True)
			return

		s.scheduled_event_id = ev.id

		# If this fixture is marked as streamed, ping the streamer role in the thread.
		if s.agreed_streamer is True and isinstance(STREAMER_ROLE_ID, int) and STREAMER_ROLE_ID > 0:
			try:
				thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
				if thread is None:
					ch = interaction.client.get_channel(s.thread_id)
					if isinstance(ch, discord.Thread):
						thread = ch
					else:
						thread = await interaction.client.fetch_channel(s.thread_id)  # type: ignore[assignment]
				if isinstance(thread, discord.Thread):
					await thread.send(
						content=f"<@&{STREAMER_ROLE_ID}> Streamer requested for **{_fixture_title(s)}**: {ev.url}",
						allowed_mentions=discord.AllowedMentions(roles=True),
					)
			except Exception:
				# Ping is best-effort; even if this fails, the event is created.
				pass

		# Post/persist an agreement snapshot so later event edits don't lose what was agreed.
		snapshot_msg_id: Optional[int] = None
		if not s.agreement_snapshot_message_id:
			try:
				thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
				if thread is None:
					ch = interaction.client.get_channel(s.thread_id)
					if isinstance(ch, discord.Thread):
						thread = ch
					else:
						thread = await interaction.client.fetch_channel(s.thread_id)  # type: ignore[assignment]
				if isinstance(thread, discord.Thread):
					snapshot_text = _agreement_snapshot_text(s, event_url=ev.url)
					msg = await thread.send(snapshot_text)
					snapshot_msg_id = msg.id
					s.agreement_snapshot_message_id = snapshot_msg_id
					s.agreement_snapshot_text = snapshot_text
			except Exception:
				# Snapshot is best-effort; even if this fails, the event is created.
				pass

		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))
		await interaction.followup.send(f"Event created: {ev.url}", ephemeral=True)


async def _refresh_thread(client: discord.Client, thread_id: int) -> None:
	"""Refresh (edit) the single control message embed in the thread."""

	channel = client.get_channel(thread_id)
	if not isinstance(channel, discord.Thread):
		try:
			channel = await client.fetch_channel(thread_id)  # type: ignore[assignment]
		except Exception:
			return
	if not isinstance(channel, discord.Thread):
		return

	state = _load_state()
	raw = state.get("threads", {}).get(str(thread_id))
	if not isinstance(raw, dict):
		return
	s = _dict_to_state(raw)
	embed = _fixture_embed(s)
	view = FixtureThreadView(thread_id=thread_id)

	msg: Optional[discord.Message] = None
	if s.control_message_id:
		try:
			msg = await channel.fetch_message(int(s.control_message_id))
		except Exception:
			msg = None

	try:
		if msg is None:
			new_msg = await channel.send(embed=embed, view=view)
			s.control_message_id = new_msg.id
			state["threads"][s.key] = _state_to_dict(s)
			_save_state(state)
			try:
				if hasattr(client, "add_view"):
					client.add_view(view, message_id=new_msg.id)  # type: ignore[attr-defined]
			except Exception:
				pass
			return

		await msg.edit(embed=embed, view=view)
		try:
			if hasattr(client, "add_view"):
				client.add_view(view, message_id=msg.id)  # type: ignore[attr-defined]
		except Exception:
			pass
	except Exception:
		return


# =============================
# Cog
# =============================


class EventOrganiser(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._lock = asyncio.Lock()
		bot.add_view(OrganiserHomeView())
		# Note: thread views are reposted on startup; no need for full persistent registration per-thread.

	@commands.Cog.listener()
	async def on_ready(self):
		if getattr(self.bot, "user", None) is None:
			return

		async with self._lock:
			await self._ensure_home_embed()
			await self._repost_thread_controls()

	async def _ensure_home_embed(self) -> None:
		channel = self.bot.get_channel(ORGANISER_EMBED_CHANNEL_ID)
		if not isinstance(channel, discord.TextChannel):
			return

		steps: list[str] = [
			"- Click the button below to organise the fixture end-to-end.",
			"- Propose date/time (must be within the round window)",
			"- Propose team size (30-50, equal sizes)",
			"- Propose streamer (yes/no)",
		]
		if ENABLE_MAP_MIDPOINT:
			steps.append("- Roll map & midpoint (first roll is free, then each clan can reroll up to 3 times)")
		if ENABLE_SIDES:
			steps.append("- Decide sides and host server with a random chance!")
		steps.append("- Create the Discord event when done!")

		embed = discord.Embed(
			title="Fixture Organiser",
			description=(
			    "\n".join(steps)
			),
			color=discord.Color.blurple(),
		)

		state = _load_state()
		msg_id = state.get("organiser_message")
		msg: Optional[discord.Message] = None
		if isinstance(msg_id, int):
			try:
				msg = await channel.fetch_message(msg_id)
			except Exception:
				msg = None

		view = OrganiserHomeView()
		if msg is None:
			new_msg = await channel.send(embed=embed, view=view)
			state["organiser_message"] = new_msg.id
			_save_state(state)
		else:
			await msg.edit(embed=embed, view=view)

	async def _repost_thread_controls(self) -> None:
		state = _load_state()
		threads = state.get("threads", {})
		if not isinstance(threads, dict):
			return

		# For each known thread, refresh the existing control message and register persistent views.
		for thread_id_str in list(threads.keys()):
			try:
				thread_id = int(thread_id_str)
			except Exception:
				continue

			try:
				await _refresh_thread(self.bot, thread_id)
			except Exception:
				continue


async def setup(bot: commands.Bot):
	await bot.add_cog(EventOrganiser(bot))

