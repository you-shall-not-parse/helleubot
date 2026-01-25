import asyncio
import json
import os
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from data_paths import data_path

# =============================
# CONFIG (EDIT THIS)
# =============================

GUILD_ID = 1462382487622914079

# Channel where the “Organise Fixture” embed is posted.
ORGANISER_EMBED_CHANNEL_ID = 1464726144367464685

# Thread parent channel (threads created under this channel)
THREAD_PARENT_CHANNEL_ID = 1462382488784470181

# Where to create the scheduled Discord event. Usually same guild.
SCHEDULED_EVENT_GUILD_ID = GUILD_ID

# Optional: channel to associate to the scheduled event (voice/stage). Leave None to create an external event.
SCHEDULED_EVENT_CHANNEL_ID: Optional[int] = None

# Clan roles (name -> role_id). User must have exactly one of these to start.
CLAN_ROLE_IDS: dict[str, int] = {
	"RMC": 1462558256147857408,
	"7DR": 1462383332598743080,
	"RDG": 1462558410364031097,
	"7PD": 1464763568506536000,
	"PG60": 1464763651108896778,
	"ITHL": 1464763753441788117,
	"48th": 1464763805509619958,
	"OFIN": 1464764074985390090,
}

# Round windows (inclusive) for validation.
ROUND_WINDOWS: dict[int, tuple[date, date]] = {
	1: (date(2026, 3, 2), date(2026, 3, 15)),
	2: (date(2026, 3, 16), date(2026, 3, 29)),
	3: (date(2026, 3, 30), date(2026, 4, 12)),
	4: (date(2026, 4, 13), date(2026, 4, 26)),
	5: (date(2026, 4, 27), date(2026, 5, 10)),
	6: (date(2026, 5, 11), date(2026, 5, 24)),
	7: (date(2026, 5, 25), date(2026, 6, 7)),
}

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


def _parse_datetime_utc(text: str) -> datetime:
	"""Parse user input into an aware UTC datetime.

	Supported:
	  - DD-MM-YYYY HH:MM
	  - DD-MM-YYYYTHH:MM
	  - DD-MM-YYYY HH:MMZ / ...+00:00

	If no timezone is supplied, assumes UTC.
	"""

	raw = text.strip()
	if not raw:
		raise ValueError("Empty datetime")

	raw = raw.replace("/", "-")
	raw = raw.replace("T", " ")

	# ISO with timezone
	try:
		dt_obj = datetime.fromisoformat(raw)
		if dt_obj.tzinfo is None:
			dt_obj = dt_obj.replace(tzinfo=timezone.utc)
		return dt_obj.astimezone(timezone.utc)
	except Exception:
		pass

	# Simple "DD-MM-YYYY HH:MM" assumed UTC
	m = re.match(r"^(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2})$", raw)
	if m:
		dd, mon, yyyy = map(int, m.group(1).split("-"))
		hh, mi = map(int, m.group(2).split(":"))
		d = date(yyyy, mon, dd)
		return datetime.combine(d, time(hh, mi, tzinfo=timezone.utc))

	raise ValueError("Invalid datetime format")


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


def _roll_map_and_midpoint() -> tuple[str, str]:
	valid_maps = [m for m in MAP_POOL if len(_midpoints_for_map(m)) == 3]
	if not valid_maps:
		raise ValueError("No maps have exactly 3 configured midpoints")
	map_name = random.choice(valid_maps)
	midpoint = random.choice(_midpoints_for_map(map_name))
	return map_name, midpoint


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

	current_map: Optional[str] = None
	current_midpoint: Optional[str] = None
	map_proposed_by: Optional[str] = None

	# Map/midpoint rerolls ("mix-ups")
	reroll_count_a: int = 0
	reroll_count_b: int = 0
	last_map_roll_by: Optional[str] = None

	sides_allies: Optional[str] = None
	sides_axis: Optional[str] = None
	sides_decided_by: Optional[str] = None

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
		"current_map": s.current_map,
		"current_midpoint": s.current_midpoint,
		"map_proposed_by": s.map_proposed_by,
		"reroll_count_a": s.reroll_count_a,
		"reroll_count_b": s.reroll_count_b,
		"last_map_roll_by": s.last_map_roll_by,
		"sides_allies": s.sides_allies,
		"sides_axis": s.sides_axis,
		"sides_decided_by": s.sides_decided_by,
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
		current_map=d.get("current_map"),
		current_midpoint=d.get("current_midpoint"),
		map_proposed_by=d.get("map_proposed_by"),
		reroll_count_a=int(d.get("reroll_count_a", 0)),
		reroll_count_b=int(d.get("reroll_count_b", 0)),
		last_map_roll_by=d.get("last_map_roll_by"),
		sides_allies=d.get("sides_allies"),
		sides_axis=d.get("sides_axis"),
		sides_decided_by=d.get("sides_decided_by"),
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
			parts.append(f"Start (UTC): {dt.strftime('%d-%m-%Y %H:%M')} UTC")
			parts.append(f"Start (Discord): <t:{int(dt.timestamp())}:F>")
		except Exception:
			parts.append(f"Start (UTC): {s.agreed_datetime_utc}")
	if s.agreed_team_size:
		parts.append(f"Team size: {s.agreed_team_size} vs {s.agreed_team_size}")
	if s.current_map:
		parts.append(f"Map: {s.current_map}")
	if s.current_midpoint:
		parts.append(f"Midpoint: {s.current_midpoint}")
	if s.sides_allies and s.sides_axis:
		parts.append(f"Allies: {s.sides_allies}")
		parts.append(f"Axis: {s.sides_axis}")
	parts.append(f"Rerolls used: {s.clan_a} {s.reroll_count_a}/{REROLL_LIMIT} • {s.clan_b} {s.reroll_count_b}/{REROLL_LIMIT}")
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


def _format_dt_short(dt_iso: str) -> str:
	try:
		dt = datetime.fromisoformat(dt_iso)
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		dt = dt.astimezone(timezone.utc)
		return dt.strftime("%d-%m-%Y %H:%M") + " UTC"
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
			"1. Propose date/time (must be within the round window)\n"
			"2. Agree/counter until locked\n"
			"3. Propose team size (30-50, equal sizes)\n"
			"4. Roll map and midpoint, then optional veto workflow\n"
			"5. Randomly assign sides (Allies/Axis)\n"
			"6. Create the Discord event when ready!"
		),
		color=discord.Color.blurple(),
	)

	embed.add_field(name="Fixture", value=_fixture_title(s), inline=False)
	embed.add_field(name="Round Window", value=_format_round_window(s.round_no), inline=False)

	# Date/time
	if s.agreed_datetime_utc:
		ts = int(datetime.fromisoformat(s.agreed_datetime_utc).replace(tzinfo=timezone.utc).timestamp())
		embed.add_field(name="Date/Time (Agreed)", value=f"<t:{ts}:F>", inline=False)
	elif s.proposed_datetime_utc:
		ts = int(datetime.fromisoformat(s.proposed_datetime_utc).replace(tzinfo=timezone.utc).timestamp())
		embed.add_field(
			name="Date/Time (Proposed)",
			value=f"<t:{ts}:F> (by {s.proposed_datetime_by})",
			inline=False,
		)
	else:
		embed.add_field(name="Date/Time", value="Not proposed yet", inline=False)

	# Team sizes
	if s.agreed_team_size:
		embed.add_field(name="Team Size (Agreed)", value=f"{s.agreed_team_size} vs {s.agreed_team_size}", inline=False)
	elif s.proposed_team_size:
		embed.add_field(
			name="Team Size (Proposed)",
			value=f"{s.proposed_team_size} vs {s.proposed_team_size} (by {s.proposed_team_size_by})",
			inline=False,
		)
	else:
		embed.add_field(name="Team Size", value="Not proposed yet", inline=False)

	# Map/midpoint
	if s.current_map and s.current_midpoint:
		veto_a = f"{s.veto_count_a}/{VETO_LIMIT}"
		veto_b = f"{s.veto_count_b}/{VETO_LIMIT}"
		veto_line = f"Vetoes: {s.clan_a} {veto_a} • {s.clan_b} {veto_b}"
		if s.pending_veto_by:
			veto_line += f" • Pending veto request by {s.pending_veto_by}"
		embed.add_field(
			name="Map & Midpoint",
			value=f"{s.current_map} — Midpoint: {s.current_midpoint}\n{veto_line}",
			inline=False,
		)
	else:
		embed.add_field(name="Map & Midpoint", value="Not rolled yet", inline=False)

	# Sides
	if s.sides_allies and s.sides_axis:
		embed.add_field(name="Sides", value=f"Allies: {s.sides_allies}\nAxis: {s.sides_axis}", inline=False)
	else:
		embed.add_field(name="Sides", value="Not decided yet", inline=False)

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
		await thread.send(content=f"{requester_clan} vs {opponent_clan}", embed=embed, view=control_view)

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
	when = discord.ui.TextInput(
		label="Date/Time",
		placeholder="DD-MM-YYYY HH:MM (UTC)",
		required=True,
		max_length=64,
	)

	def __init__(self, thread_id: int, mode: str):
		super().__init__()
		self.thread_id = thread_id
		self.mode = mode  # propose or counter

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
		user_clan = _find_user_clan(interaction.user)
		if user_clan not in (s.clan_a, s.clan_b):
			await interaction.response.send_message("You are not part of this fixture.", ephemeral=True)
			return

		try:
			dt_utc = _parse_datetime_utc(str(self.when.value))
		except ValueError:
			await interaction.response.send_message(
				"Invalid datetime. Use `DD-MM-YYYY HH:MM` (assumed UTC).",
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
		s.proposed_team_size_by = user_clan
		s.agreed_team_size = None

		state["threads"][s.key] = _state_to_dict(s)
		_save_state(state)
		await interaction.response.send_message("Team size proposal recorded.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))


class FixtureThreadView(discord.ui.View):
	def __init__(self, thread_id: int):
		super().__init__(timeout=None)
		self.thread_id = thread_id

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
		await interaction.response.send_modal(DateTimeModal(thread_id=s.thread_id, mode="propose"))

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
		s.proposed_datetime_utc = None
		s.proposed_datetime_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Date/time agreed.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Counter date/time", style=discord.ButtonStyle.secondary, custom_id="fixture:dt_counter")
	async def counter_datetime(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if s.proposed_datetime_by and clan == s.proposed_datetime_by:
			await interaction.response.send_message("Wait for the other clan to respond.", ephemeral=True)
			return
		await interaction.response.send_modal(DateTimeModal(thread_id=s.thread_id, mode="counter"))

	@discord.ui.button(label="Propose team size", style=discord.ButtonStyle.primary, custom_id="fixture:size_propose")
	async def propose_size(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, _ = res
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
		s.proposed_team_size = None
		s.proposed_team_size_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Team size agreed.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Roll map+mid", style=discord.ButtonStyle.primary, custom_id="fixture:map_roll")
	async def roll_map(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
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
		if s.pending_veto_by:
			await interaction.response.send_message("There is a pending veto request to resolve first.", ephemeral=True)
			return
		try:
			new_map, new_mid = _roll_map_and_midpoint()
		except ValueError:
			await interaction.response.send_message(
				"No valid maps to roll: every map in MAP_POOL must have exactly 3 midpoints configured.",
				ephemeral=True,
			)
			return
		s.current_map = new_map
		s.current_midpoint = new_mid
		s.map_proposed_by = clan
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Rolled map/midpoint.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Request veto", style=discord.ButtonStyle.danger, custom_id="fixture:map_veto_request")
	async def request_veto(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if not s.current_map:
			await interaction.response.send_message("Roll a map first.", ephemeral=True)
			return
		if s.pending_veto_by:
			await interaction.response.send_message("There is already a pending veto request.", ephemeral=True)
			return
		if _veto_count_for(s, clan) >= VETO_LIMIT:
			await interaction.response.send_message("You have used all vetoes.", ephemeral=True)
			return
		# Only allow the non-proposer to request veto (to match “one clan proposes the other agrees”).
		if s.map_proposed_by and clan == s.map_proposed_by:
			await interaction.response.send_message("The other clan must request the veto.", ephemeral=True)
			return
		s.pending_veto_by = clan
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Veto requested. Waiting for opponent to approve/reject.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Approve veto", style=discord.ButtonStyle.success, custom_id="fixture:map_veto_approve")
	async def approve_veto(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if not s.pending_veto_by:
			await interaction.response.send_message("No pending veto.", ephemeral=True)
			return
		if clan == s.pending_veto_by:
			await interaction.response.send_message("Opponent must approve/reject.", ephemeral=True)
			return
		_inc_veto(s, s.pending_veto_by)
		s.pending_veto_by = None
		# Reroll and set proposer to the clan that approved the veto (so it alternates smoothly).
		if MAP_POOL:
			issues = _midpoints_config_issues()
			if issues:
				await interaction.response.send_message(
					"Midpoint config is incomplete. Each map must have exactly 3 midpoints in MIDPOINTS_BY_MAP. Missing/invalid: "
					+ ", ".join(issues),
					ephemeral=True,
				)
				return
			try:
				new_map, new_mid = _roll_map_and_midpoint()
			except ValueError:
				await interaction.response.send_message(
					"No valid maps to roll: every map in MAP_POOL must have exactly 3 midpoints configured.",
					ephemeral=True,
				)
				return
			s.current_map = new_map
			s.current_midpoint = new_mid
		s.map_proposed_by = clan
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Veto approved and rerolled.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Reject veto", style=discord.ButtonStyle.secondary, custom_id="fixture:map_veto_reject")
	async def reject_veto(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, clan = res
		if not s.pending_veto_by:
			await interaction.response.send_message("No pending veto.", ephemeral=True)
			return
		if clan == s.pending_veto_by:
			await interaction.response.send_message("Opponent must approve/reject.", ephemeral=True)
			return
		s.pending_veto_by = None
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Veto rejected. Map stands.", ephemeral=True)
		asyncio.create_task(_refresh_thread(interaction.client, s.thread_id))

	@discord.ui.button(label="Decide sides", style=discord.ButtonStyle.primary, custom_id="fixture:sides")
	async def decide_sides(self, interaction: discord.Interaction, button: discord.ui.Button):
		res = await self._require_member(interaction)
		if not res:
			return
		s, _ = res
		if random.choice([True, False]):
			s.sides_allies, s.sides_axis = s.clan_a, s.clan_b
		else:
			s.sides_allies, s.sides_axis = s.clan_b, s.clan_a
		st = _load_state()
		st["threads"][s.key] = _state_to_dict(s)
		_save_state(st)
		await interaction.response.send_message("Sides decided.", ephemeral=True)
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
		if not (s.current_map and s.current_midpoint):
			await interaction.followup.send("Roll map/midpoint first.", ephemeral=True)
			return
		if not (s.sides_allies and s.sides_axis):
			await interaction.followup.send("Decide sides first.", ephemeral=True)
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

		desc = (
			f"**Fixture:** {s.clan_a} vs {s.clan_b}\n"
			f"**Round:** {s.round_no} ({_format_round_window(s.round_no)})\n"
			f"**Team size:** {s.agreed_team_size} vs {s.agreed_team_size}\n"
			f"**Map:** {s.current_map}\n"
			f"**Midpoint:** {s.current_midpoint}\n"
			f"**Allies:** {s.sides_allies}\n"
			f"**Axis:** {s.sides_axis}\n"
			f"**Thread:** <#{s.thread_id}>"
		)

		try:
			if SCHEDULED_EVENT_CHANNEL_ID:
				channel = guild.get_channel(SCHEDULED_EVENT_CHANNEL_ID)
				if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
					await interaction.followup.send("Configured event channel is invalid.", ephemeral=True)
					return
				ev = await guild.create_scheduled_event(
					name=_fixture_title(s),
					start_time=start_dt,
					end_time=end_dt,
					channel=channel,
					description=desc,
				)
			else:
				ev = await guild.create_scheduled_event(
					name=_fixture_title(s),
					start_time=start_dt,
					end_time=end_dt,
					location="Hell Let Loose",
					description=desc,
				)
		except discord.Forbidden:
			await interaction.followup.send("Missing permissions to create scheduled events.", ephemeral=True)
			return
		except Exception as e:
			await interaction.followup.send(f"Failed to create event: {e}", ephemeral=True)
			return

		s.scheduled_event_id = ev.id

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
	"""Repost a fresh control message with updated embed in the thread."""

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
	try:
		await channel.send(embed=embed, view=view)
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

		embed = discord.Embed(
			title="Fixture Organiser",
			description=(
			    "Use the buttons below to organise the fixture end-to-end.\n"
			    "1. Propose date/time (must be within the round window)\n"
			    "2. Agree/counter until locked\n"
			    "3. Propose team size (30-50, equal sizes)\n"
			    "4. Roll map and midpoint, then optional veto workflow\n"
			    "5. Randomly assign sides (Allies/Axis)\n"
			    "6. Create the Discord event and it'll go in the calendar!"
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

		# For each known thread, post a fresh control message so buttons work after restarts.
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

