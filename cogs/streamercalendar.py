import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord.ext import commands

from data_paths import data_path
from league_config import STREAMER_ROLE_ID

# =============================
# CONFIG (EDIT THIS)
# =============================

# Channel where streamer requests are posted + managed.
# Set to the text channel ID where you want the streamer request board to live.
STREAMER_REQUEST_CHANNEL_ID = 0

# Where we persist streamer request board state
STREAMER_STATE_PATH = data_path("streamer_requests_state.json")


def _load_streamer_state() -> dict[str, Any]:
	if not os.path.exists(STREAMER_STATE_PATH):
		return {"board_message_id": None, "current_request_id": None, "requests": {}}
	try:
		with open(STREAMER_STATE_PATH, "r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, dict):
			return {"board_message_id": None, "current_request_id": None, "requests": {}}
		data.setdefault("board_message_id", None)
		data.setdefault("current_request_id", None)
		data.setdefault("requests", {})
		if not isinstance(data.get("requests"), dict):
			data["requests"] = {}
		return data
	except Exception:
		return {"board_message_id": None, "current_request_id": None, "requests": {}}


def _save_streamer_state(state: dict[str, Any]) -> None:
	os.makedirs(os.path.dirname(STREAMER_STATE_PATH), exist_ok=True)
	with open(STREAMER_STATE_PATH, "w", encoding="utf-8") as f:
		json.dump(state, f, indent=2, ensure_ascii=False)


def _discord_message_url(*, guild_id: int, channel_id: int, message_id: int) -> str:
	return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _format_dt_short(dt_iso: Optional[str]) -> str:
	if not dt_iso:
		return "(time TBD)"
	try:
		dt = datetime.fromisoformat(dt_iso)
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		dt = dt.astimezone(timezone.utc)
		return dt.strftime("%d/%m/%Y %H:%M") + " UTC"
	except Exception:
		return str(dt_iso)


def _request_fixture_line(*, clan_a: str, clan_b: str, dt_iso: Optional[str]) -> str:
	return f"{clan_a} vs {clan_b} — {_format_dt_short(dt_iso)}"


def _request_embed_from_entry(entry: dict[str, Any]) -> discord.Embed:
	clan_a = str(entry.get("clan_a", "?"))
	clan_b = str(entry.get("clan_b", "?"))
	dt_iso = entry.get("datetime_utc") if isinstance(entry.get("datetime_utc"), str) else None
	thread_id = entry.get("thread_id")
	ev_url = entry.get("event_url") if isinstance(entry.get("event_url"), str) else None
	accepted_by = entry.get("accepted_by") if isinstance(entry.get("accepted_by"), list) else []
	rejected_by = entry.get("rejected_by") if isinstance(entry.get("rejected_by"), list) else []
	accepted_by = [x for x in accepted_by if isinstance(x, int)]
	rejected_by = [x for x in rejected_by if isinstance(x, int)]

	embed = discord.Embed(
		title="Streamer Request",
		description=_request_fixture_line(clan_a=clan_a, clan_b=clan_b, dt_iso=dt_iso),
		color=discord.Color.blurple(),
	)
	if isinstance(thread_id, int):
		embed.add_field(name="Thread", value=f"<#{thread_id}>", inline=True)
	if ev_url:
		embed.add_field(name="Event", value=ev_url, inline=True)
	acc = "\n".join([f"<@{uid}>" for uid in accepted_by]) or "(none)"
	rej = "\n".join([f"<@{uid}>" for uid in rejected_by]) or "(none)"
	embed.add_field(name="Accepted", value=acc, inline=True)
	embed.add_field(name="Rejected", value=rej, inline=True)
	embed.set_footer(text="Accept/reject/remove using the Streamer Requests Board buttons.")
	return embed


def _board_embed(
	*,
	guild_id: int,
	channel_id: int,
	state: dict[str, Any],
	max_lines: int = 12,
) -> discord.Embed:
	requests = state.get("requests", {})
	current_id = state.get("current_request_id")

	items: list[tuple[datetime, str, dict[str, Any]]] = []
	if isinstance(requests, dict):
		for rid, raw in requests.items():
			if not isinstance(raw, dict):
				continue
			dt_sort = datetime.min.replace(tzinfo=timezone.utc)
			try:
				dt_raw = raw.get("datetime_utc")
				if isinstance(dt_raw, str) and dt_raw:
					dt_sort = datetime.fromisoformat(dt_raw)
					if dt_sort.tzinfo is None:
						dt_sort = dt_sort.replace(tzinfo=timezone.utc)
					dt_sort = dt_sort.astimezone(timezone.utc)
			except Exception:
				pass
			items.append((dt_sort, str(rid), raw))
	items.sort(key=lambda x: x[0], reverse=True)

	def req_url(msg_id: Optional[int]) -> Optional[str]:
		if not isinstance(msg_id, int) or msg_id <= 0:
			return None
		return _discord_message_url(guild_id=guild_id, channel_id=channel_id, message_id=msg_id)

	accepted_lines: list[str] = []
	unaccepted_lines: list[str] = []
	current_lines: list[str] = []

	current_raw = None
	if isinstance(requests, dict) and isinstance(current_id, str):
		current_raw = requests.get(current_id)
	if isinstance(current_raw, dict):
		clan_a = str(current_raw.get("clan_a", "?"))
		clan_b = str(current_raw.get("clan_b", "?"))
		dt_iso = current_raw.get("datetime_utc") if isinstance(current_raw.get("datetime_utc"), str) else None
		thread_id = current_raw.get("thread_id")
		ev_url = current_raw.get("event_url") if isinstance(current_raw.get("event_url"), str) else None
		accepted_by = current_raw.get("accepted_by") if isinstance(current_raw.get("accepted_by"), list) else []
		current_lines.append(_request_fixture_line(clan_a=clan_a, clan_b=clan_b, dt_iso=dt_iso))
		if isinstance(thread_id, int):
			current_lines.append(f"Thread: <#{thread_id}>")
		if ev_url:
			current_lines.append(f"Event: {ev_url}")
		if accepted_by:
			current_lines.append("Accepted by: " + ", ".join([f"<@{uid}>" for uid in accepted_by if isinstance(uid, int)]))
		else:
			current_lines.append("Accepted by: (none)")
	else:
		current_lines.append("(no current request)")

	for _, rid, raw in items:
		clan_a = str(raw.get("clan_a", "?"))
		clan_b = str(raw.get("clan_b", "?"))
		dt_iso = raw.get("datetime_utc") if isinstance(raw.get("datetime_utc"), str) else None
		accepted_by = raw.get("accepted_by") if isinstance(raw.get("accepted_by"), list) else []
		msg_url = req_url(raw.get("request_message_id"))
		line_base = _request_fixture_line(clan_a=clan_a, clan_b=clan_b, dt_iso=dt_iso)
		if accepted_by:
			for uid in accepted_by:
				if not isinstance(uid, int):
					continue
				accepted_lines.append(f"- <@{uid}> — {line_base}")
		elif not (isinstance(current_id, str) and rid == current_id):
			if msg_url:
				unaccepted_lines.append(f"- {msg_url}")
			else:
				unaccepted_lines.append(f"- {line_base}")

	if len(accepted_lines) > max_lines:
		accepted_lines = accepted_lines[:max_lines]
	if len(unaccepted_lines) > max_lines:
		unaccepted_lines = unaccepted_lines[:max_lines]

	embed = discord.Embed(
		title="Streamer Requests Board",
		description=(
			"Use the buttons below to accept/reject/remove yourself for the **CURRENT** request."
			"\n\n**Accepted Streams**\n" + ("\n".join(accepted_lines) or "(none)") +
			"\n\n**Un-Accepted Requests**\n" + ("\n".join(unaccepted_lines) or "(none)") +
			"\n\n**Current Request**\n" + ("\n".join(current_lines) or "(none)")
		),
		color=discord.Color.blurple(),
	)
	return embed


async def _refresh_streamer_board_and_current(
	bot: commands.Bot,
	guild: discord.Guild,
	channel: discord.TextChannel,
) -> None:
	state = _load_streamer_state()
	board_id = state.get("board_message_id")
	board_msg: Optional[discord.Message] = None
	if isinstance(board_id, int) and board_id > 0:
		try:
			board_msg = await channel.fetch_message(board_id)
		except Exception:
			board_msg = None

	view = StreamerRequestsBoardView(bot)
	embed = _board_embed(guild_id=guild.id, channel_id=channel.id, state=state)
	if board_msg is None:
		try:
			new_msg = await channel.send(embed=embed, view=view)
			state["board_message_id"] = new_msg.id
			_save_streamer_state(state)
			board_msg = new_msg
		except Exception:
			return
	else:
		try:
			await board_msg.edit(embed=embed, view=view)
		except Exception:
			pass

	current_id = state.get("current_request_id")
	requests = state.get("requests", {})
	if not isinstance(current_id, str) or not isinstance(requests, dict):
		return
	entry = requests.get(current_id)
	if not isinstance(entry, dict):
		return
	msg_id = entry.get("request_message_id")
	if not isinstance(msg_id, int) or msg_id <= 0:
		return
	try:
		msg = await channel.fetch_message(msg_id)
	except Exception:
		return
	try:
		await msg.edit(embed=_request_embed_from_entry(entry))
	except Exception:
		return


class StreamerRequestsBoardView(discord.ui.View):
	def __init__(self, bot: commands.Bot):
		super().__init__(timeout=None)
		self.bot = bot

	async def _mutate_current(self, interaction: discord.Interaction, *, action: str) -> None:
		if interaction.guild is None or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Server only.", ephemeral=True)
			return
		if not (isinstance(STREAMER_REQUEST_CHANNEL_ID, int) and STREAMER_REQUEST_CHANNEL_ID > 0):
			await interaction.response.send_message("Streamer request channel is not configured.", ephemeral=True)
			return
		if interaction.channel is None or interaction.channel.id != STREAMER_REQUEST_CHANNEL_ID:
			await interaction.response.send_message("Use this in the streamer request channel.", ephemeral=True)
			return

		state = _load_streamer_state()
		current_id = state.get("current_request_id")
		requests = state.get("requests", {})
		if not isinstance(current_id, str) or not isinstance(requests, dict) or current_id not in requests:
			await interaction.response.send_message("No current request to update.", ephemeral=True)
			return
		r = requests.get(current_id)
		if not isinstance(r, dict):
			await interaction.response.send_message("Current request is invalid.", ephemeral=True)
			return

		accepted_by = r.get("accepted_by") if isinstance(r.get("accepted_by"), list) else []
		rejected_by = r.get("rejected_by") if isinstance(r.get("rejected_by"), list) else []
		uid = interaction.user.id

		def _dedupe_ints(xs: list[Any]) -> list[int]:
			out: list[int] = []
			seen: set[int] = set()
			for x in xs:
				if isinstance(x, int) and x not in seen:
					seen.add(x)
					out.append(x)
			return out

		accepted_by = [x for x in accepted_by if isinstance(x, int)]
		rejected_by = [x for x in rejected_by if isinstance(x, int)]

		if action == "accept":
			accepted_by.append(uid)
			rejected_by = [x for x in rejected_by if x != uid]
		elif action == "reject":
			rejected_by.append(uid)
			accepted_by = [x for x in accepted_by if x != uid]
		elif action == "remove":
			accepted_by = [x for x in accepted_by if x != uid]
			rejected_by = [x for x in rejected_by if x != uid]
		else:
			await interaction.response.send_message("Unknown action.", ephemeral=True)
			return

		r["accepted_by"] = _dedupe_ints(accepted_by)
		r["rejected_by"] = _dedupe_ints(rejected_by)
		requests[current_id] = r
		state["requests"] = requests
		_save_streamer_state(state)

		await interaction.response.defer(ephemeral=True, thinking=False)
		try:
			channel = interaction.guild.get_channel(STREAMER_REQUEST_CHANNEL_ID)
			if isinstance(channel, discord.TextChannel):
				await _refresh_streamer_board_and_current(self.bot, interaction.guild, channel)
		except Exception:
			pass
		await interaction.followup.send("Updated.", ephemeral=True)

	@discord.ui.button(label="Accept Current", style=discord.ButtonStyle.success, custom_id="streamerboard:accept")
	async def accept_current(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self._mutate_current(interaction, action="accept")

	@discord.ui.button(label="Reject Current", style=discord.ButtonStyle.danger, custom_id="streamerboard:reject")
	async def reject_current(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self._mutate_current(interaction, action="reject")

	@discord.ui.button(label="Remove from Current", style=discord.ButtonStyle.secondary, custom_id="streamerboard:remove")
	async def remove_current(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self._mutate_current(interaction, action="remove")


async def maybe_post_streamer_request(
	bot: discord.Client,
	*,
	guild: discord.Guild,
	thread_id: int,
	clan_a: str,
	clan_b: str,
	datetime_utc_iso: Optional[str],
	event_id: int,
	event_url: str,
) -> None:
	"""Create/update the current streamer request and refresh the board."""
	if not (isinstance(STREAMER_REQUEST_CHANNEL_ID, int) and STREAMER_REQUEST_CHANNEL_ID > 0):
		return
	channel = guild.get_channel(STREAMER_REQUEST_CHANNEL_ID)
	if not isinstance(channel, discord.TextChannel):
		return

	state = _load_streamer_state()
	requests = state.get("requests", {})
	if not isinstance(requests, dict):
		requests = {}

	request_id = str(event_id) if event_id else str(thread_id)
	entry = requests.get(request_id)
	if not isinstance(entry, dict):
		entry = {
			"id": request_id,
			"thread_id": thread_id,
			"clan_a": clan_a,
			"clan_b": clan_b,
			"datetime_utc": datetime_utc_iso,
			"event_url": event_url,
			"request_message_id": None,
			"accepted_by": [],
			"rejected_by": [],
			"created_at": datetime.now(timezone.utc).isoformat(),
		}
	else:
		entry["thread_id"] = thread_id
		entry["clan_a"] = clan_a
		entry["clan_b"] = clan_b
		entry["datetime_utc"] = datetime_utc_iso
		entry["event_url"] = event_url
		entry.setdefault("accepted_by", [])
		entry.setdefault("rejected_by", [])

	msg_id = entry.get("request_message_id")
	msg: Optional[discord.Message] = None
	if isinstance(msg_id, int) and msg_id > 0:
		try:
			msg = await channel.fetch_message(msg_id)
		except Exception:
			msg = None

	if msg is None:
		content = None
		allowed_mentions = None
		if isinstance(STREAMER_ROLE_ID, int) and STREAMER_ROLE_ID > 0:
			content = f"<@&{STREAMER_ROLE_ID}>"
			allowed_mentions = discord.AllowedMentions(roles=True)
		try:
			new_msg = await channel.send(
				content=content,
				embed=_request_embed_from_entry(entry),
				allowed_mentions=allowed_mentions,
			)
			entry["request_message_id"] = new_msg.id
			msg = new_msg
		except Exception:
			return
	else:
		try:
			await msg.edit(embed=_request_embed_from_entry(entry))
		except Exception:
			pass

	requests[request_id] = entry
	state["requests"] = requests
	state["current_request_id"] = request_id
	_save_streamer_state(state)

	try:
		if isinstance(bot, commands.Bot):
			await _refresh_streamer_board_and_current(bot, guild, channel)
	except Exception:
		pass


class StreamerCalendar(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._lock = asyncio.Lock()
		bot.add_view(StreamerRequestsBoardView(bot))

	@commands.Cog.listener()
	async def on_ready(self):
		if getattr(self.bot, "user", None) is None:
			return
		if not (isinstance(STREAMER_REQUEST_CHANNEL_ID, int) and STREAMER_REQUEST_CHANNEL_ID > 0):
			return
		async with self._lock:
			channel = self.bot.get_channel(STREAMER_REQUEST_CHANNEL_ID)
			if isinstance(channel, discord.TextChannel) and channel.guild is not None:
				await _refresh_streamer_board_and_current(self.bot, channel.guild, channel)


async def setup(bot: commands.Bot):
	await bot.add_cog(StreamerCalendar(bot))
