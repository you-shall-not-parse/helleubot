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

# Role allowed to use admin scoreboard edit commands
ADMIN_ROLE_ID: int = 1109147750932676649

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
IMAGE_TEMPLATE_PATH: str = os.path.join(os.path.dirname(__file__), "scoreboard_blank1.jpg")
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


def _score_options() -> list[tuple[int, int]]:
	# From the submitter clan's perspective
	return [(5, 0), (4, 1), (3, 2), (2, 3), (1, 4), (0, 5)]


def _is_admin_member(member: discord.Member) -> bool:
	if member.guild_permissions.administrator:
		return True
	return any(r.id == ADMIN_ROLE_ID for r in member.roles)


def _admin_app_command_check(interaction: discord.Interaction) -> bool:
	# Safe check wrapper for app_commands decorators.
	cog = interaction.client.get_cog("ScoreboardCog")
	if cog is None:
		return False
	return cog._admin_check(interaction)  # type: ignore[no-any-return]


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
		score = maps_for
		# Primary: score (maps won), then diff, then wins, then fewer losses, then fewer played
		return (score, diff, w, -l, -played)

	rows.sort(key=sort_key, reverse=True)

	header = f"{'#':<3}{'Clan':<22}{'W':>3}{'L':>3}{'MP':>4}{'Score':>7}"
	lines = [header]
	for idx, (name, s) in enumerate(rows, start=1):
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		played = int(s.get("played", w + l))
		maps_for = int(s.get("maps_for", 0))
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


def _sorted_leaderboard_rows(stats: dict[str, Any]) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for rid_str, s in stats.items():
		name = str(s.get("name") or _role_name_from_id(int(rid_str)))
		w = int(s.get("w", 0))
		l = int(s.get("l", 0))
		maps_for = int(s.get("maps_for", 0))
		maps_against = int(s.get("maps_against", 0))
		diff = maps_for - maps_against
		rows.append(
			{
				"name": name,
				"score": maps_for,
				"w": w,
				"l": l,
				"diff": diff,
			},
		)

	rows.sort(key=lambda r: (r["score"], r["diff"], r["w"], -r["l"], r["name"].lower()), reverse=True)
	return rows


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
			self.data.setdefault("last_result", None)
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
			self.data["last_result"] = {
				"match_id": match.match_id,
				"a_name": _role_name_from_id(match.submitter_clan_role_id),
				"b_name": _role_name_from_id(match.opponent_clan_role_id),
				"a_score": match.submitter_score,
				"b_score": match.opponent_score,
				"at": _utcnow_iso(),
			}

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
		view._refresh_score_options()
		await interaction.response.edit_message(view=view)


class ScoreSelect(discord.ui.Select):
	def __init__(self, submitter_clan_role_id: int):
		self.submitter_clan_role_id = submitter_clan_role_id
		super().__init__(
			placeholder="Select the match score…",
			min_values=1,
			max_values=1,
			options=[discord.SelectOption(label="Pick an opponent first", value="0-5")],
			disabled=True,
			custom_id="scoreboard:score_select",
		)

	def set_matchup(self, opponent_clan_role_id: Optional[int]) -> None:
		if opponent_clan_role_id is None:
			self.disabled = True
			self.options = [discord.SelectOption(label="Pick an opponent first", value="0-5")]
			return
		a_name = _role_name_from_id(self.submitter_clan_role_id)
		b_name = _role_name_from_id(opponent_clan_role_id)
		self.disabled = False
		self.options = [
			discord.SelectOption(
				label=f"{a_name} - {b_name} ({a}-{b})",
				value=f"{a}-{b}",
			)
			for a, b in _score_options()
		]

	async def callback(self, interaction: discord.Interaction):
		view: "SubmitFlowView" = self.view  # type: ignore[assignment]
		view.selected_score = str(self.values[0])
		view._refresh_submit_button_state()
		await interaction.response.edit_message(view=view)


class SubmitFlowView(discord.ui.View):
	def __init__(self, submitter_id: int, submitter_clan_role_id: int):
		super().__init__(timeout=300)
		self.submitter_id = submitter_id
		self.submitter_clan_role_id = submitter_clan_role_id
		self.opponent_clan_role_id: Optional[int] = None
		self.selected_score: Optional[str] = None

		self.add_item(OpponentSelect(submitter_clan_role_id))
		self.score_select = ScoreSelect(submitter_clan_role_id)
		self.add_item(self.score_select)

	def _refresh_score_options(self) -> None:
		self.score_select.set_matchup(self.opponent_clan_role_id)
		self.selected_score = None
		self._refresh_submit_button_state()

	def _refresh_submit_button_state(self) -> None:
		for child in self.children:
			if isinstance(child, discord.ui.Button) and child.custom_id == "scoreboard:submit_result":
				child.disabled = not (self.opponent_clan_role_id is not None and self.selected_score is not None)

	@discord.ui.button(label="Submit Result", style=discord.ButtonStyle.success, disabled=True, custom_id="scoreboard:submit_result")
	async def submit_result(self, interaction: discord.Interaction, button: discord.ui.Button):
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
			return
		if interaction.user.id != self.submitter_id:
			await interaction.response.send_message("This submit flow isn’t yours.", ephemeral=True)
			return
		if self.opponent_clan_role_id is None or self.selected_score is None:
			await interaction.response.send_message("Pick an opposing clan and a score first.", ephemeral=True)
			return

		try:
			a, b = _parse_score(self.selected_score)
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
			submitter_clan_role_id=self.submitter_clan_role_id,
			opponent_clan_role_id=self.opponent_clan_role_id,
			submitter_score=a,
			opponent_score=b,
			created_at=_utcnow_iso(),
		)

		await cog.store.add_pending_match(match)
		validation_message = await cog.post_validation_message(interaction.guild, match)
		if validation_message:
			await cog.store.link_validation_message(match.match_id, validation_message.id)

		await interaction.followup.send("Submitted! A validation message has been posted.", ephemeral=True)


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
			description="Select the opposing clan and score, then click **Submit Result**.",
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
		image_path = await self._render_scoreboard_portrait_image()
		file = discord.File(image_path, filename=os.path.basename(image_path))

		content = "Scoreboard (top = latest result, bottom = leaderboard)"
		if message_id:
			try:
				msg = await channel.fetch_message(int(message_id))
				await msg.edit(content=content, embed=None, attachments=[], files=[file])
				return
			except Exception:
				log.warning("Could not edit existing leaderboard message; re-sending")

		msg = await channel.send(content=content, file=file)
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
		# Leaderboard message is now the combined scoreboard image.

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

	async def _render_scoreboard_portrait_image(self) -> str:
		from PIL import Image, ImageDraw, ImageFont  # pillow

		# Portrait base
		width, height = 1080, 1920
		base = Image.new("RGBA", (width, height), (16, 18, 24, 255))
		draw = ImageDraw.Draw(base)

		try:
			font_title = ImageFont.truetype(FONT_PATH, 64)
			font_big = ImageFont.truetype(FONT_PATH, 84)
			font_med = ImageFont.truetype(FONT_PATH, 44)
			font_small = ImageFont.truetype(FONT_PATH, 36)
		except Exception:
			font_title = ImageFont.load_default()
			font_big = ImageFont.load_default()
			font_med = ImageFont.load_default()
			font_small = ImageFont.load_default()

		pad = 60
		# Header
		draw.text((pad, pad), "SCOREBOARD", font=font_title, fill=(235, 239, 245, 255))
		draw.line((pad, pad + 88, width - pad, pad + 88), fill=(80, 90, 110, 255), width=3)

		# Latest result section
		result_top = pad + 120
		result_h = 420
		draw.rounded_rectangle(
			(pad, result_top, width - pad, result_top + result_h),
			radius=24,
			fill=(26, 30, 40, 255),
			outline=(60, 70, 92, 255),
			width=3,
		)

		last = self.store.data.get("last_result")
		if isinstance(last, dict):
			a_name = str(last.get("a_name") or "")
			b_name = str(last.get("b_name") or "")
			a_score = int(last.get("a_score", 0))
			b_score = int(last.get("b_score", 0))
			result_text = f"{a_name}  {a_score} - {b_score}  {b_name}".strip()
			caption = "Latest result"
		else:
			result_text = "No results yet"
			caption = "Latest result"

		draw.text((pad + 36, result_top + 28), caption, font=font_small, fill=(180, 190, 210, 255))
		bbox = draw.textbbox((0, 0), result_text, font=font_big)
		text_w = bbox[2] - bbox[0]
		text_h = bbox[3] - bbox[1]
		x = (width - text_w) // 2
		y = result_top + (result_h // 2) - (text_h // 2) + 20
		draw.text((x, y), result_text, font=font_big, fill=(245, 246, 250, 255))

		# Leaderboard section
		table_top = result_top + result_h + 50
		draw.text((pad, table_top), "LEADERBOARD", font=font_title, fill=(235, 239, 245, 255))
		draw.line((pad, table_top + 88, width - pad, table_top + 88), fill=(80, 90, 110, 255), width=3)

		rows = _sorted_leaderboard_rows(self.store.data.get("clan_stats", {}))
		# Column layout
		header_y = table_top + 120
		col_clan = pad
		col_score = width - pad - 360
		col_w = width - pad - 240
		col_l = width - pad - 120
		row_h = 74

		draw.text((col_clan, header_y), "Clan", font=font_med, fill=(190, 200, 220, 255))
		draw.text((col_score, header_y), "Score", font=font_med, fill=(190, 200, 220, 255))
		draw.text((col_w, header_y), "Win", font=font_med, fill=(190, 200, 220, 255))
		draw.text((col_l, header_y), "Loss", font=font_med, fill=(190, 200, 220, 255))
		draw.line((pad, header_y + 56, width - pad, header_y + 56), fill=(60, 70, 92, 255), width=2)

		start_y = header_y + 80
		max_rows = 16
		for i, r in enumerate(rows[:max_rows], start=1):
			y = start_y + (i - 1) * row_h
			bg = (22, 26, 36, 255) if i % 2 == 1 else (18, 22, 32, 255)
			draw.rounded_rectangle(
				(pad, y - 8, width - pad, y + row_h - 8),
				radius=16,
				fill=bg,
				outline=(38, 44, 60, 255),
				width=2,
			)
			name = str(r["name"])
			name = (name[:18] + "…") if len(name) > 19 else name
			draw.text((col_clan + 10, y), f"{i}. {name}", font=font_med, fill=(235, 239, 245, 255))
			draw.text((col_score + 20, y), str(r["score"]), font=font_med, fill=(235, 239, 245, 255))
			draw.text((col_w + 20, y), str(r["w"]), font=font_med, fill=(235, 239, 245, 255))
			draw.text((col_l + 20, y), str(r["l"]), font=font_med, fill=(235, 239, 245, 255))

		out_path = data_path("scoreboard.png")
		base.save(out_path, format="PNG")
		return out_path


	def _admin_check(self, interaction: discord.Interaction) -> bool:
		if not isinstance(interaction.user, discord.Member):
			return False
		return _is_admin_member(interaction.user)


	@app_commands.command(name="scoreboard_admin_edit_clan", description="Admin: edit a clan's leaderboard values")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_edit_clan(
		self,
		interaction: discord.Interaction,
		clan_role: discord.Role,
		score: int,
		wins: int,
		losses: int,
	):
		await interaction.response.defer(ephemeral=True)
		if clan_role.id not in set(CLAN_ROLES.values()):
			await interaction.followup.send("That role is not a configured clan role.", ephemeral=True)
			return
		if score < 0 or wins < 0 or losses < 0:
			await interaction.followup.send("Score/Wins/Losses must be non-negative.", ephemeral=True)
			return

		key = str(clan_role.id)
		stats = self.store.data.setdefault("clan_stats", {})
		s = stats.setdefault(
			key,
			{"name": clan_role.name, "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0},
		)
		s["name"] = s.get("name") or clan_role.name
		s["maps_for"] = int(score)
		s["w"] = int(wins)
		s["l"] = int(losses)
		s["played"] = int(wins) + int(losses)
		self.store.data["clan_stats"] = stats
		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send(f"Updated {clan_role.name}: score={score}, W={wins}, L={losses}", ephemeral=True)


	@app_commands.command(name="scoreboard_admin_edit_match", description="Admin: edit a confirmed match and adjust leaderboard")
	@app_commands.check(_admin_app_command_check)
	async def scoreboard_admin_edit_match(
		self,
		interaction: discord.Interaction,
		match_id: str,
		new_score: str,
	):
		await interaction.response.defer(ephemeral=True)
		match = await self.store.get_match(match_id)
		if match is None:
			await interaction.followup.send("Match not found.", ephemeral=True)
			return
		if match.status != "confirmed":
			await interaction.followup.send("Only confirmed matches can be edited with leaderboard adjustment.", ephemeral=True)
			return

		try:
			new_a, new_b = _parse_score(new_score)
		except ValueError as e:
			await interaction.followup.send(str(e), ephemeral=True)
			return

		# Compute delta vs old and apply to clan_stats
		old_a, old_b = match.submitter_score, match.opponent_score
		a_key = str(match.submitter_clan_role_id)
		b_key = str(match.opponent_clan_role_id)
		stats: dict[str, Any] = self.store.data.setdefault("clan_stats", {})
		a = stats.setdefault(a_key, {"name": _role_name_from_id(match.submitter_clan_role_id), "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0})
		b = stats.setdefault(b_key, {"name": _role_name_from_id(match.opponent_clan_role_id), "w": 0, "l": 0, "played": 0, "maps_for": 0, "maps_against": 0})

		# Undo old maps
		a["maps_for"] = int(a.get("maps_for", 0)) - int(old_a)
		a["maps_against"] = int(a.get("maps_against", 0)) - int(old_b)
		b["maps_for"] = int(b.get("maps_for", 0)) - int(old_b)
		b["maps_against"] = int(b.get("maps_against", 0)) - int(old_a)

		# Undo old W/L
		if old_a > old_b:
			a["w"] = int(a.get("w", 0)) - 1
			b["l"] = int(b.get("l", 0)) - 1
		else:
			b["w"] = int(b.get("w", 0)) - 1
			a["l"] = int(a.get("l", 0)) - 1

		# Apply new maps
		a["maps_for"] = int(a.get("maps_for", 0)) + int(new_a)
		a["maps_against"] = int(a.get("maps_against", 0)) + int(new_b)
		b["maps_for"] = int(b.get("maps_for", 0)) + int(new_b)
		b["maps_against"] = int(b.get("maps_against", 0)) + int(new_a)

		# Apply new W/L
		if new_a > new_b:
			a["w"] = int(a.get("w", 0)) + 1
			b["l"] = int(b.get("l", 0)) + 1
		else:
			b["w"] = int(b.get("w", 0)) + 1
			a["l"] = int(a.get("l", 0)) + 1

		# Normalize played
		a["played"] = int(a.get("w", 0)) + int(a.get("l", 0))
		b["played"] = int(b.get("w", 0)) + int(b.get("l", 0))

		# Persist match update
		match.submitter_score = int(new_a)
		match.opponent_score = int(new_b)
		self.store.data["pending_matches"][match.match_id] = match.to_dict()

		# Update last_result to this edited match
		self.store.data["last_result"] = {
			"match_id": match.match_id,
			"a_name": _role_name_from_id(match.submitter_clan_role_id),
			"b_name": _role_name_from_id(match.opponent_clan_role_id),
			"a_score": match.submitter_score,
			"b_score": match.opponent_score,
			"at": _utcnow_iso(),
		}

		await self.store.save()
		await self.ensure_leaderboard_message()
		await interaction.followup.send(f"Updated match {match_id} to {new_a}-{new_b} and adjusted leaderboard.", ephemeral=True)

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
