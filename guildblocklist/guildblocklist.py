import io
import discord

from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import bold


class GuildBlocklist(commands.Cog):
    """
    Blacklist guilds from being able to add [botname].
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 34237423098423094, force_registration=True)
        self.config.register_global(blacklist=[])

    @commands.group(name="guildblocklist", aliases=["gbl", "guildblacklist"])
    async def gbl(self, ctx):
        """
        Guild blocklist management.
        """

    @gbl.command(usage="<guild_id>")
    async def add(self, ctx, guild: int):
        """Add a guild to the guild blocklist."""
        b = await self.config.blacklist()
        b.append(guild)
        await self.config.blacklist.set(b)
        await ctx.send("Guild added to blocklist.")

    @gbl.command(usage="<guild_id>")
    async def remove(self, ctx, guild: int):
        """Remove a guild from the guild blocklist."""
        b = await self.config.blacklist()
        if guild in b:
            b.remove(guild)
            await self.config.blacklist.set(b)
        else:
            return await ctx.send("This guild is not on the blocklist.")
        await ctx.send("Guild removed from blocklist.")

    @gbl.command(name="list")
    async def _list(self, ctx):
        """Lists guilds on the blocklist."""
        b = await self.config.blacklist()
        title = bold("Blocklisted guilds:")
        if not b:
            return await ctx.send("There are no blocklisted guilds.")
        if len(b) == 1:
            s = ''
        else:
            s = 's'
        title = bold(f"Blocklisted guild{s}:")
        await ctx.send(title + '\n\n' + ", ".join(f"`{x}`" for x in b))

    @gbl.command()
    async def clear(self, ctx):
        """Clears the guild blocklist."""
        await self.config.blacklist.clear()
        await ctx.tick()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        config = await self.config.blacklist()
        if guild.id in config:
            return await guild.leave()