import discord
from discord import app_commands
from discord.ext import commands
from league_config import GUILD_ID


# Guild-scoped commands require a guild sync (see on_ready below).
ECHO_GUILD_ID = GUILD_ID
ECHO_ROLE_ID = 1462383096019157149
TARGET_GUILD = discord.Object(id=ECHO_GUILD_ID)


def _has_echo_role(interaction: discord.Interaction) -> bool:
	# If not configured, deny by default.
	if not isinstance(ECHO_ROLE_ID, int) or ECHO_ROLE_ID <= 0:
		return False
	user = interaction.user
	if not isinstance(user, discord.Member):
		return False
	return any(role.id == ECHO_ROLE_ID for role in user.roles)


async def _handle_echo_check_failure(
	interaction: discord.Interaction,
	error: app_commands.AppCommandError,
) -> bool:
	if not isinstance(error, app_commands.CheckFailure):
		return False

	msg = "You don't have permission to use this command."
	if not isinstance(ECHO_ROLE_ID, int) or ECHO_ROLE_ID <= 0:
		msg = "This command isn't configured yet (ECHO_ROLE_ID is not set)."

	if interaction.response.is_done():
		await interaction.followup.send(msg, ephemeral=True)
	else:
		await interaction.response.send_message(msg, ephemeral=True)
	return True


class Echo(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		self._did_guild_sync = False

	@app_commands.guilds(TARGET_GUILD)
	@app_commands.guild_only()
	@app_commands.command(name="echo", description="Send a user-defined message.")
	@app_commands.describe(message="The message to send")
	@app_commands.check(_has_echo_role)
	async def echo(self, interaction: discord.Interaction, message: str):
		# Ephemeral ack so the channel doesn't show "<user> used /echo".
		await interaction.response.send_message("Sent.", ephemeral=True)
		if interaction.channel is not None:
			await interaction.channel.send(message)

	@app_commands.guilds(TARGET_GUILD)
	@app_commands.guild_only()
	@app_commands.command(name="echoembed", description="Send a user-defined embed message.")
	@app_commands.describe(
		title="Optional embed title",
		message="The embed body text",
		image="Optional image attachment to append to the embed",
	)
	@app_commands.check(_has_echo_role)
	async def echoembed(
		self,
		interaction: discord.Interaction,
		message: str,
		title: str | None = None,
		image: discord.Attachment | None = None,
	):
		if image is not None and not str(getattr(image, "content_type", "")).startswith("image/"):
			await interaction.response.send_message("The attachment must be an image.", ephemeral=True)
			return

		embed = discord.Embed(
			title=title,
			description=message,
			color=discord.Color.blurple(),
		)
		if image is not None:
			embed.set_image(url=image.url)

		await interaction.response.send_message("Sent.", ephemeral=True)
		if interaction.channel is not None:
			await interaction.channel.send(embed=embed)

	@echo.error
	async def echo_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
		if await _handle_echo_check_failure(interaction, error):
			return
		raise error

	@echoembed.error
	async def echoembed_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
		if await _handle_echo_check_failure(interaction, error):
			return
		raise error

	@commands.Cog.listener()
	async def on_ready(self):
		# Ensure the guild-scoped command is registered quickly.
		if self._did_guild_sync:
			return
		try:
			await self.bot.tree.sync(guild=TARGET_GUILD)
			self._did_guild_sync = True
			print(f"[Echo] Commands synced to guild {ECHO_GUILD_ID}.")
		except Exception as e:
			print(f"[Echo] Sync error: {e}")


async def setup(bot: commands.Bot):
	await bot.add_cog(Echo(bot))

