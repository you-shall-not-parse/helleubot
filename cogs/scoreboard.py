import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from data_paths import data_path


# -----------------------------
# Config (fill these in)
# -----------------------------

# Channel where the main "Submit Scores" embed is posted
SCOREBOARD_CHANNEL_ID: int = 1462387812815998997

# Channel where results are posted for confirmation by the opposing clan
VALIDATION_CHANNEL_ID: int = 1462382488784470181

# Channel where the leaderboard + match images are posted
LEADERBOARD_CHANNEL_ID: int = 1462384116376014911

# Role IDs for each clan (name -> role_id)
CLAN_ROLES: dict[str, int] = {
	"RMC": 1462558256147857408,
	"7DR": 1462383332598743080,
	"RDG": 1462558410364031097,
	"7PD": 1464763568506536000,
	"PG60": 1464763651108896778,
	"ITHL": 1464763753441788117,
	"48th": 1462558355166986261,
	"OFIN": 1464764074985390090,
}

# Image/font assets
IMAGE_TEMPLATE_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_blank.jpg")
FONT_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_font.ttf")


log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any) -> Optional[int]:
	try:
		return int(value)
	except Exception:
		return None


def _parse_score(text: str) -> tuple[int, int]:
	# Accept formats like: 3-2, 3:2, 3 2
	cleaned = text.strip().lower().replace(":", "-").replace(" ", "-")
	parts = [p for p in cleaned.split("-") if p]
	if len(parts) != 2:
		raise ValueError("Score must look like 3-2")
	a = _safe_int(parts[0])
	b = _safe_int(parts[1])
	if a is None or b is None:
		raise ValueError("Score must be two numbers")
	if a < 0 or b < 0:
		raise ValueError("Score must be non-negative")
	if a == b:
		raise ValueError("Score cannot be a draw")
	# User examples imply a 5-map series (e.g. 3-2, 4-1, 5-0)
	if a + b != 5:
		raise ValueError("Score must add up to 5 (e.g. 3-2, 4-1, 5-0)")
	return a, b


def _role_name_from_id(role_id: int) -> str:
	for name, rid in CLAN_ROLES.items():
		if rid == role_id:
			return name
	return f"Role {role_id}"


def _build_leaderboard_embed(stats: dict[str, Any]) -> discord.Embed:
	# stats is {role_id(str): {name,w,l,played,for,against}}
	rows: list[tuple[str, dict[str, Any]]] = []
	for rid_str, s in stats.items():
		name = str(s.get("name") or _role_name_from_id(int(rid_str)))
		rows.append((name, s))

	def sort_key(item: tuple[str, dict[str, Any]]):
		s = item[1]
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		maps_for = int(s.get("maps_for", 0))
		maps_against = int(s.get("maps_against", 0))
		diff = maps_for - maps_against
		played = int(s.get("played", w + l))
		# Primary: wins, then diff, then maps_for, then fewer losses, then fewer played
		return (w, diff, maps_for, -l, -played)

	rows.sort(key=sort_key, reverse=True)

	header = f"{'#':<3}{'Clan':<22}{'W':>3}{'L':>3}{'MP':>4}{'Score':>7}"
	lines = [header]
	for idx, (name, s) in enumerate(rows, start=1):
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		played = int(s.get("played", w + l))
		maps_for = int(s.get("maps_for", 0))
		# "Score" requested as accumulative score; we use total maps won.
		score = maps_for
		display_name = (name[:19] + "…") if len(name) > 20 else name
		lines.append(f"{idx:<3}{display_name:<22}{w:>3}{l:>3}{played:>4}{score:>7}")

	embed = discord.Embed(
		title="League Leaderboard",
		description="```\n" + "\n".join(lines) + "\n```",
		colour=discord.Colour.blurple(),
	)
	embed.set_footer(text="Score = total maps won")
	return embed


def _build_scoreboard_embed() -> discord.Embed:
	embed = discord.Embed(
		title="Submit Match Scores",
		description="Click the button below to submit a match result for validation by the opposing clan.",
		colour=discord.Colour.green(),
	)
	return embed


@dataclass
class PendingMatch:
	match_id: str
	submitter_id: int
	submitter_clan_role_id: int
	opponent_clan_role_id: int
	submitter_score: int
	opponent_score: int
	created_at: str
	validation_message_id: Optional[int] = None
	status: str = "pending"  # pending | confirmed | disputed
	confirmed_by_id: Optional[int] = None
	confirmed_at: Optional[str] = None

	def to_dict(self) -> dict[str, Any]:
		return {
			"match_id": self.match_id,
			"submitter_id": self.submitter_id,
			"submitter_clan_role_id": self.submitter_clan_role_id,
			"opponent_clan_role_id": self.opponent_clan_role_id,
			"submitter_score": self.submitter_score,
			"opponent_score": self.opponent_score,
			"created_at": self.created_at,
			"validation_message_id": self.validation_message_id,
			"status": self.status,
			"confirmed_by_id": self.confirmed_by_id,
			"confirmed_at": self.confirmed_at,
		}

	@staticmethod
	def from_dict(d: dict[str, Any]) -> "PendingMatch":
		return PendingMatch(
			match_id=str(d["match_id"]),
			submitter_id=int(d["submitter_id"]),
			submitter_clan_role_id=int(d["submitter_clan_role_id"]),
			opponent_clan_role_id=int(d["opponent_clan_role_id"]),
			submitter_score=int(d["submitter_score"]),
			opponent_score=int(d["opponent_score"]),
			created_at=str(d.get("created_at") or _utcnow_iso()),
			validation_message_id=_safe_int(d.get("validation_message_id")),
			status=str(d.get("status") or "pending"),
			confirmed_by_id=_safe_int(d.get("confirmed_by_id")),
			confirmed_at=d.get("confirmed_at"),
		)


class ScoreboardStore:
	def __init__(self) -> None:
		self._path = data_path("scoreboard.json")
		self._lock = asyncio.Lock()
		self.data: dict[str, Any] = {}

	async def load(self) -> None:
		async with self._lock:
			if os.path.exists(self._path):
				try:
					with open(self._path, "r", encoding="utf-8") as f:
						self.data = json.load(f)
				except Exception:
					log.exception("Failed reading %s, starting fresh", self._path)
					self.data = {}

			self.data.setdefault("scoreboard_message_id", None)
			self.data.setdefault("leaderboard_message_id", None)
			self.data.setdefault("clan_stats", {})
			self.data.setdefault("pending_matches", {})  # match_id -> match dict
			self.data.setdefault("pending_by_validation_message", {})  # message_id(str) -> match_id
			await self._ensure_clans_locked()

	async def save(self) -> None:
		async with self._lock:
			tmp = self._path + ".tmp"
			with open(tmp, "w", encoding="utf-8") as f:
				json.dump(self.data, f, indent=2)
			os.replace(tmp, self._path)

	async def _ensure_clans_locked(self) -> None:
		stats: dict[str, Any] = self.data.setdefault("clan_stats", {})
		for clan_name, role_id in CLAN_ROLES.items():
			key = str(role_id)
			stats.setdefault(
				key,
				{
					"name": clan_name,
					"w": 0,
					"l": 0,
					"played": 0,
					"maps_for": 0,
					"maps_against": 0,
				},
			)

	async def ensure_clans(self) -> None:
		async with self._lock:
			await self._ensure_clans_locked()
		await self.save()

	async def add_pending_match(self, match: PendingMatch) -> None:
		async with self._lock:
			self.data["pending_matches"][match.match_id] = match.to_dict()
		await self.save()

	async def link_validation_message(self, match_id: str, validation_message_id: int) -> None:
		async with self._lock:
			m = self.data["pending_matches"].get(match_id)
			if not m:
				return
			m["validation_message_id"] = int(validation_message_id)
			self.data["pending_by_validation_message"][str(validation_message_id)] = match_id
		await self.save()

	async def get_match(self, match_id: str) -> Optional[PendingMatch]:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			return PendingMatch.from_dict(d)

	async def get_match_by_validation_message(self, message_id: int) -> Optional[PendingMatch]:
		async with self._lock:
			match_id = self.data.get("pending_by_validation_message", {}).get(str(message_id))
			if not match_id:
				return None
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			return PendingMatch.from_dict(d)

	async def mark_disputed(self, match_id: str) -> None:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return
			d["status"] = "disputed"
		await self.save()

	async def confirm_match(self, match_id: str, confirmed_by_id: int) -> Optional[PendingMatch]:
		async with self._lock:
			d = self.data.get("pending_matches", {}).get(match_id)
			if not d:
				return None
			d["status"] = "confirmed"
			d["confirmed_by_id"] = int(confirmed_by_id)
			d["confirmed_at"] = _utcnow_iso()
			match = PendingMatch.from_dict(d)

			# Apply to leaderboard
			stats: dict[str, Any] = self.data.setdefault("clan_stats", {})

			a_key = str(match.submitter_clan_role_id)
			b_key = str(match.opponent_clan_role_id)
			if a_key not in stats:
				stats[a_key] = {
					"name": _role_name_from_id(match.submitter_clan_role_id),
					"w": 0,
					"l": 0,
					"played": 0,
					"maps_for": 0,
					"maps_against": 0,
				}
			if b_key not in stats:
				stats[b_key] = {
					"name": _role_name_from_id(match.opponent_clan_role_id),
					"w": 0,
					"l": 0,
					"played": 0,
					"maps_for": 0,
					"maps_against": 0,
				}

			a = stats[a_key]
			b = stats[b_key]

			a["played"] = int(a.get("played", 0)) + 1
			b["played"] = int(b.get("played", 0)) + 1

			a["maps_for"] = int(a.get("maps_for", 0)) + match.submitter_score
			a["maps_against"] = int(a.get("maps_against", 0)) + match.opponent_score
			b["maps_for"] = int(b.get("maps_for", 0)) + match.opponent_score
			b["maps_against"] = int(b.get("maps_against", 0)) + match.submitter_score

			if match.submitter_score > match.opponent_score:
				a["w"] = int(a.get("w", 0)) + 1
				b["l"] = int(b.get("l", 0)) + 1
			else:
				b["w"] = int(b.get("w", 0)) + 1
				a["l"] = int(a.get("l", 0)) + 1

			self.data["clan_stats"] = stats

		await self.save()
		return match


def _member_clan_role_id(member: discord.Member) -> Optional[int]:
	clan_role_ids = set(CLAN_ROLES.values())
	hits = [r.id for r in member.roles if r.id in clan_role_ids]
	if len(hits) != 1:
		return None
	return hits[0]


class OpponentSelect(discord.ui.Select):
	def __init__(self, submitter_clan_role_id: int):
		options = []
		for clan_name, role_id in CLAN_ROLES.items():
			if role_id == submitter_clan_role_id:
				continue
			options.append(discord.SelectOption(label=clan_name, value=str(role_id)))
		super().__init__(
			placeholder="Select the opposing clan…",
			min_values=1,
			max_values=1,
			options=options[:25],
			custom_id="scoreboard:opponent_select",
		)

	async def callback(self, interaction: discord.Interaction):
		view: "SubmitFlowView" = self.view  # type: ignore[assignment]
		view.opponent_clan_role_id = int(self.values[0])
		for child in view.children:
			if isinstance(child, discord.ui.Button) and child.custom_id == "scoreboard:open_score_modal":
				child.disabled = False
		await interaction.response.edit_message(view=view)


class ScoreModal(discord.ui.Modal, title="Submit match score"):
	score = discord.ui.TextInput(
		label="Score (adds to 5)",
		placeholder="Example: 3-2, 4-1, 5-0",
		min_length=3,
		max_length=10,
		required=True,
	)

	def __init__(self, parent_view: "SubmitFlowView"):
		super().__init__(timeout=300)
		self.parent_view = parent_view

	async def on_submit(self, interaction: discord.Interaction):
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
			return
		if interaction.user.id != self.parent_view.submitter_id:
			await interaction.response.send_message("This submit flow isn’t yours.", ephemeral=True)
			return
		if self.parent_view.opponent_clan_role_id is None:
			await interaction.response.send_message("Pick an opposing clan first.", ephemeral=True)
			return

		try:
			a, b = _parse_score(str(self.score.value))
		except ValueError as e:
			await interaction.response.send_message(str(e), ephemeral=True)
			return

		await interaction.response.defer(ephemeral=True, thinking=True)

		cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
		if cog is None:
			await interaction.followup.send("Scoreboard cog is not loaded.", ephemeral=True)
			return

		match_id = uuid.uuid4().hex[:12]
		match = PendingMatch(
			match_id=match_id,
			submitter_id=interaction.user.id,
			submitter_clan_role_id=self.parent_view.submitter_clan_role_id,
			opponent_clan_role_id=self.parent_view.opponent_clan_role_id,
			submitter_score=a,
			opponent_score=b,
			created_at=_utcnow_iso(),
		)

		await cog.store.add_pending_match(match)
		validation_message = await cog.post_validation_message(interaction.guild, match)
		if validation_message:
			await cog.store.link_validation_message(match.match_id, validation_message.id)

		await interaction.followup.send("Submitted! A validation message has been posted.", ephemeral=True)


class SubmitFlowView(discord.ui.View):
	def __init__(self, submitter_id: int, submitter_clan_role_id: int):
		super().__init__(timeout=300)
		self.submitter_id = submitter_id
		self.submitter_clan_role_id = submitter_clan_role_id
		self.opponent_clan_role_id: Optional[int] = None

		self.add_item(OpponentSelect(submitter_clan_role_id))

	@discord.ui.button(
		label="Enter Score",
		style=discord.ButtonStyle.primary,
		disabled=True,
		custom_id="scoreboard:open_score_modal",
	)
	async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
		if interaction.user.id != self.submitter_id:
			await interaction.response.send_message("This submit flow isn’t yours.", ephemeral=True)
			return
		await interaction.response.send_modal(ScoreModal(self))


class ScoreboardMainView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)  # persistent

	@discord.ui.button(
		label="Submit Scores",
		style=discord.ButtonStyle.success,
		custom_id="scoreboard:submit_scores",
	)
	async def submit_scores(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		clan_role_id = _member_clan_role_id(interaction.user)
		if clan_role_id is None:
			await interaction.response.send_message(
				"You must have exactly one clan role to submit scores.",
				ephemeral=True,
			)
			return
		if len(CLAN_ROLES) < 2:
			await interaction.response.send_message(
				"No clans configured. Fill in CLAN_ROLES at the top of the cog.",
				ephemeral=True,
			)
			return

		embed = discord.Embed(
			title="Submit a result",
			description="Select the opposing clan, then click **Enter Score**.",
			colour=discord.Colour.blurple(),
		)
		await interaction.response.send_message(
			embed=embed,
			view=SubmitFlowView(interaction.user.id, clan_role_id),
			ephemeral=True,
		)


class ValidationView(discord.ui.View):
	def __init__(self, match_id: str):
		super().__init__(timeout=None)  # persistent
		self.match_id = match_id

		confirm_button = discord.ui.Button(
			label="Confirm Result",
			style=discord.ButtonStyle.success,
			custom_id=f"scoreboard:confirm:{match_id}",
		)

		async def confirm_callback(interaction: discord.Interaction):
			cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
			if cog is None:
				await interaction.response.send_message("Scoreboard cog is not loaded.", ephemeral=True)
				return
			await cog.handle_confirm(interaction, self.match_id)

		confirm_button.callback = confirm_callback
		self.add_item(confirm_button)

		dispute_button = discord.ui.Button(
			label="Dispute",
			style=discord.ButtonStyle.danger,
			custom_id=f"scoreboard:dispute:{match_id}",
		)

		async def dispute_callback(interaction: discord.Interaction):
			cog: "ScoreboardCog" = interaction.client.get_cog("ScoreboardCog")  # type: ignore[assignment]
			if cog is None:
				await interaction.response.send_message("Scoreboard cog is not loaded.", ephemeral=True)
				return
			await cog.handle_dispute(interaction, self.match_id)

		dispute_button.callback = dispute_callback
		self.add_item(dispute_button)


class ScoreboardCog(commands.Cog):
	"""Score submission + validation + leaderboard."""

	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self.store = ScoreboardStore()

	async def cog_load(self) -> None:
		await self.store.load()
		await self.store.ensure_clans()
		# Register persistent base view
		self.bot.add_view(ScoreboardMainView())
		# Re-register persistent validation views for pending matches
		pending = self.store.data.get("pending_matches", {})
		for match_id, d in pending.items():
			try:
				match = PendingMatch.from_dict(d)
			except Exception:
				continue
			if match.status == "pending":
				self.bot.add_view(ValidationView(match_id))

	@commands.Cog.listener()
	async def on_ready(self):
		# Post/repair the main scoreboard message and leaderboard message.
		await self.ensure_scoreboard_message()
		await self.ensure_leaderboard_message()

	async def ensure_scoreboard_message(self) -> None:
		if SCOREBOARD_CHANNEL_ID == 0:
			return
		channel = self.bot.get_channel(SCOREBOARD_CHANNEL_ID)
		if channel is None:
			try:
				channel = await self.bot.fetch_channel(SCOREBOARD_CHANNEL_ID)
			except Exception:
				log.exception("Failed to fetch scoreboard channel")
				return
		if not isinstance(channel, discord.TextChannel):
			return

		message_id = self.store.data.get("scoreboard_message_id")
		embed = _build_scoreboard_embed()
		view = ScoreboardMainView()

		if message_id:
			try:
				msg = await channel.fetch_message(int(message_id))
				await msg.edit(embed=embed, view=view)
				return
			except Exception:
				log.warning("Could not edit existing scoreboard message; re-sending")

		msg = await channel.send(embed=embed, view=view)
		self.store.data["scoreboard_message_id"] = msg.id
		await self.store.save()

	async def ensure_leaderboard_message(self) -> None:
		if LEADERBOARD_CHANNEL_ID == 0:
			return
		channel = self.bot.get_channel(LEADERBOARD_CHANNEL_ID)
		if channel is None:
			try:
				channel = await self.bot.fetch_channel(LEADERBOARD_CHANNEL_ID)
			except Exception:
				log.exception("Failed to fetch leaderboard channel")
				return
		if not isinstance(channel, discord.TextChannel):
			return

		message_id = self.store.data.get("leaderboard_message_id")
		embed = _build_leaderboard_embed(self.store.data.get("clan_stats", {}))

		if message_id:
			try:
				msg = await channel.fetch_message(int(message_id))
				await msg.edit(embed=embed)
				return
			except Exception:
				log.warning("Could not edit existing leaderboard message; re-sending")

		msg = await channel.send(embed=embed)
		self.store.data["leaderboard_message_id"] = msg.id
		await self.store.save()

	async def post_validation_message(self, guild: discord.Guild, match: PendingMatch) -> Optional[discord.Message]:
		if VALIDATION_CHANNEL_ID == 0:
			return None
		channel = guild.get_channel(VALIDATION_CHANNEL_ID)
		if channel is None:
			try:
				channel = await guild.fetch_channel(VALIDATION_CHANNEL_ID)
			except Exception:
				log.exception("Failed to fetch validation channel")
				return None
		if not isinstance(channel, discord.TextChannel):
			return None

		a_name = _role_name_from_id(match.submitter_clan_role_id)
		b_name = _role_name_from_id(match.opponent_clan_role_id)
		embed = discord.Embed(
			title="Match Result Submitted",
			description=(
				f"**{a_name}** vs **{b_name}**\n"
				f"Proposed score: **{match.submitter_score}-{match.opponent_score}**\n\n"
				f"Opposing clan should confirm below."
			),
			colour=discord.Colour.orange(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.add_field(name="Submitted by", value=f"<@{match.submitter_id}>", inline=False)
		embed.set_footer(text=f"Match ID: {match.match_id}")

		opponent_role_mention = f"<@&{match.opponent_clan_role_id}>"
		msg = await channel.send(
			content=f"Validation required from {opponent_role_mention}",
			embed=embed,
			view=ValidationView(match.match_id),
			allowed_mentions=discord.AllowedMentions(roles=True),
		)
		return msg

	async def handle_confirm(self, interaction: discord.Interaction, match_id: str) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True, thinking=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("This match can’t be found.", ephemeral=True)
			return
		if match.status != "pending":
			await interaction.followup.send(f"This match is already {match.status}.", ephemeral=True)
			return

		# Only the opposing clan role can confirm.
		opponent_role = interaction.guild.get_role(match.opponent_clan_role_id)
		if opponent_role is None or opponent_role not in interaction.user.roles:
			await interaction.followup.send(
				"Only a member of the opposing clan can confirm this result.",
				ephemeral=True,
			)
			return

		confirmed = await self.store.confirm_match(match_id, interaction.user.id)
		if confirmed is None:
			await interaction.followup.send("Failed to confirm match.", ephemeral=True)
			return

		# Update validation message
		try:
			if interaction.message:
				new_embed = interaction.message.embeds[0] if interaction.message.embeds else None
				if new_embed:
					new_embed = new_embed.copy()
					new_embed.colour = discord.Colour.green()
					new_embed.add_field(name="Confirmed by", value=f"<@{interaction.user.id}>", inline=False)
				await interaction.message.edit(embed=new_embed, view=None)
		except Exception:
			log.exception("Failed updating validation message")

		await self.ensure_leaderboard_message()
		await self.post_match_image_and_summary(interaction.guild, confirmed)

		await interaction.followup.send("Confirmed and leaderboard updated.", ephemeral=True)

	async def handle_dispute(self, interaction: discord.Interaction, match_id: str) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Use this in a server.", ephemeral=True)
			return
		await interaction.response.defer(ephemeral=True, thinking=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("This match can’t be found.", ephemeral=True)
			return
		if match.status != "pending":
			await interaction.followup.send(f"This match is already {match.status}.", ephemeral=True)
			return
		opponent_role = interaction.guild.get_role(match.opponent_clan_role_id)
		if opponent_role is None or opponent_role not in interaction.user.roles:
			await interaction.followup.send(
				"Only a member of the opposing clan can dispute this result.",
				ephemeral=True,
			)
			return
		await self.store.mark_disputed(match_id)
		try:
			if interaction.message:
				new_embed = interaction.message.embeds[0] if interaction.message.embeds else None
				if new_embed:
					new_embed = new_embed.copy()
					new_embed.colour = discord.Colour.red()
					new_embed.add_field(name="Disputed by", value=f"<@{interaction.user.id}>", inline=False)
				await interaction.message.edit(embed=new_embed, view=None)
		except Exception:
			log.exception("Failed updating disputed message")
		await interaction.followup.send("Marked as disputed.", ephemeral=True)

	async def post_match_image_and_summary(self, guild: discord.Guild, match: PendingMatch) -> None:
		channel = guild.get_channel(LEADERBOARD_CHANNEL_ID)
		if channel is None:
			try:
				channel = await guild.fetch_channel(LEADERBOARD_CHANNEL_ID)
			except Exception:
				return
		if not isinstance(channel, discord.TextChannel):
			return

		a_name = _role_name_from_id(match.submitter_clan_role_id)
		b_name = _role_name_from_id(match.opponent_clan_role_id)

		image_path = await self._render_match_image(match, a_name, b_name)
		file = discord.File(image_path, filename=os.path.basename(image_path))

		embed = discord.Embed(
			title="Result Confirmed",
			description=f"**{a_name}** {match.submitter_score} - {match.opponent_score} **{b_name}**",
			colour=discord.Colour.green(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.set_image(url=f"attachment://{os.path.basename(image_path)}")
		await channel.send(embed=embed, file=file)

	async def _render_match_image(self, match: PendingMatch, clan_a: str, clan_b: str) -> str:
		from PIL import Image, ImageDraw, ImageFont  # pillow

		if os.path.exists(IMAGE_TEMPLATE_PATH):
			base = Image.open(IMAGE_TEMPLATE_PATH).convert("RGBA")
		else:
			base = Image.new("RGBA", (1280, 720), (20, 20, 20, 255))

		draw = ImageDraw.Draw(base)
		try:
			font_big = ImageFont.truetype(FONT_PATH, 80)
			font_med = ImageFont.truetype(FONT_PATH, 56)
		except Exception:
			font_big = ImageFont.load_default()
			font_med = ImageFont.load_default()

		w, h = base.size
		score_text = f"{match.submitter_score} - {match.opponent_score}"

		# Center score
		score_bbox = draw.textbbox((0, 0), score_text, font=font_big)
		score_w = score_bbox[2] - score_bbox[0]
		score_h = score_bbox[3] - score_bbox[1]
		score_x = (w - score_w) // 2
		score_y = (h - score_h) // 2
		draw.text((score_x, score_y), score_text, font=font_big, fill=(255, 255, 255, 255))

		# Clan names left/right
		left_bbox = draw.textbbox((0, 0), clan_a, font=font_med)
		left_w = left_bbox[2] - left_bbox[0]
		left_x = max(40, score_x - left_w - 60)
		left_y = score_y + (score_h // 2) - ((left_bbox[3] - left_bbox[1]) // 2)
		draw.text((left_x, left_y), clan_a, font=font_med, fill=(220, 220, 220, 255))

		right_bbox = draw.textbbox((0, 0), clan_b, font=font_med)
		right_x = min(w - 40 - (right_bbox[2] - right_bbox[0]), score_x + score_w + 60)
		right_y = score_y + (score_h // 2) - ((right_bbox[3] - right_bbox[1]) // 2)
		draw.text((right_x, right_y), clan_b, font=font_med, fill=(220, 220, 220, 255))

		out_path = data_path(f"match_{match.match_id}.png")
		base.save(out_path, format="PNG")
		return out_path

	@app_commands.command(name="scoreboard_repost", description="Repost/repair the scoreboard and leaderboard messages")
	@app_commands.checks.has_permissions(administrator=True)
	async def scoreboard_repost(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)
		await self.store.ensure_clans()
		await self.ensure_scoreboard_message()
		await self.ensure_leaderboard_message()
		await interaction.followup.send("Scoreboard + leaderboard repaired.", ephemeral=True)


async def setup(bot: commands.Bot):
	await bot.add_cog(ScoreboardCog(bot))
