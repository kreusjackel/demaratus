import asyncio
import contextlib
import datetime
import enum
import pathlib
import random

import discord
import yaml

from typing import Union, List, Literal

from redbot.core import commands, Config
from redbot.core.commands import BadArgument, Context
from redbot.core.utils import chat_formatting as cf
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS, close_menu, start_adding_reactions

from yaml.parser import (
    ParserError as YAMLParserError,
    ScannerError as YAMLScannerError,
    MarkedYAMLError as YAMLMarkedError
)

with open(pathlib.Path(__file__).parent / "assets" / "raffle.yaml") as f:
    asset = cf.box("".join(f.readlines()), lang="yaml")

now = datetime.datetime.now()
discord_creation_date = datetime.datetime(2015, 5, 13)

account_age_checker = lambda x: x < (now - discord_creation_date).days
join_age_checker = lambda ctx, x: x < (now - ctx.guild.created_at).days


class RaffleError(Exception):
    """Base exception for all raffle exceptions.
    
    These exceptions are raised, but then formatted
    in an except block to create a user-friendly
    error in which the user can read and improve from."""
    pass


class RequiredKeyError(RaffleError):
    """Raised when a raffle key is required."""

    def __init__(self, key):
        self.key = key

    def __str__(self):
        return f"The \"{self.key}\" key is required"


class UnknownEntityError(RaffleError):
    """Raised when an invalid role or user is provided to the parser."""

    def __init__(self, data, _type: Literal["user", "role"]):
        self.data = data
        self.type = _type

    def __str__(self):
        return f"\"{self.data}\" was not a valid {self.type}"

class RaffleManager(object):
    """Parses the required and relevant yaml data to ensure
    that it matches the specified requirements."""

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.name = data.get("name", None)
        self.description = data.get("description", None)
        self.account_age = data.get("account_age", None)
        self.join_age = data.get("join_age", None)
        self.maximum_entries = data.get("maximum_entries", None)
        self.roles_needed_to_enter = (
            data.get("roles_needed_to_enter", None) 
            or data.get("role_needed_to_enter", None)
        )
        self.prevented_users = (
            data.get("prevented_users", None)
            or data.get("prevented_user", None)
        )

    @classmethod
    def shorten_description(cls, description, length=50):
        if len(description) > length:
            return description[:length].rstrip() + '...'
        return description

    @classmethod
    def parse_accage(cls, accage: int):
        if not account_age_checker(accage):
            raise BadArgument("Days must be less than Discord's creation date")

    @classmethod
    def parse_joinage(cls, ctx: Context, new_join_age: int):
        guildage = (now - ctx.guild.created_at).days
        if not join_age_checker(ctx, new_join_age):
            raise BadArgument(
                "Days must be less than this guild's creation date ({} days)".format(
                    guildage
                )
            )

    def parser(self, ctx: Context):
        if self.account_age:
            if not isinstance(self.account_age, int):
                raise BadArgument("Account age days must be int, not {}".format(type(self.account_age).__name__))
            if not account_age_checker(self.account_age):
                raise BadArgument("Account age days must be less than Discord's creation date")


        if self.join_age:
            if not isinstance(self.join_age, int):
                raise BadArgument("Join age days must be int, not {}".format(type(self.join_age).__name__))
            if not join_age_checker(ctx, self.join_age):
                raise BadArgument("Join age days must be less than this guild's creation date")


        if self.maximum_entries:
            if not isinstance(self.maximum_entries, int):
                raise BadArgument("Maximum entries must be int, not {}".format(type(self.maximum_entries).__name__))


        if self.name:
            if not isinstance(self.name, str):
                raise BadArgument("Name must be str, not {}".format(type(self.name).__name__))
            if len(self.name) > 15:
                raise BadArgument("Name must be under 15 characters, your raffle name had {}".format(len(self.name)))
        else:
            raise RequiredKeyError("name")


        if self.description:
            if not isinstance(self.description, str):
                raise BadArgument("Description must be str, not {}".format(type(self.description).__name__))


        if self.roles_needed_to_enter:
            if not isinstance(self.roles_needed_to_enter, (int, list)):
                raise BadArgument("Roles must be int or list of ints, not {}".format(type(self.roles_needed_to_enter).__name__))
            if isinstance(self.roles_needed_to_enter, list):
                for r in self.roles_needed_to_enter:
                    if not ctx.guild.get_role(r):
                        raise UnknownEntityError(r, "role")
            else:
                if not ctx.guild.get_role(self.roles_needed_to_enter):
                    raise UnknownEntityError(self.roles_needed_to_enter, "role")


        if self.prevented_users:
            if not isinstance(self.prevented_users, (int, list)):
                raise BadArgument("Prevented users must be int or list of ints, not {}".format(type(self.prevented_users).__name__))
            if isinstance(self.prevented_users, list):
                for u in self.prevented_users:
                    if not ctx.bot.get_user(u):
                        raise UnknownEntityError(u, "user")
            else:
                if not ctx.bot.get_user(self.prevented_users):
                    raise UnknownEntityError(self.prevented_users, "user")


class Components(enum.Enum):
    """All of the components which can be
    used in a raffle. This class is mainly
    used for the ``[p]raffle conditions`` command.
    """

    name = (
        str, 
        "The name of the raffle. This is the only REQUIRED field."
    )

    description = (
        str, 
        "The description for the raffle. This information appears in the raffle info command."
    )

    account_age = (
        int, 
        "The account age requirement for the user who joins the raffle. This must be specified in days."
    )

    join_age = (
        int, 
        "The number of days the user needs to be in the server for in order to join the raffle."
    )

    roles_needed_to_enter = (
        list, 
        "A list of discord roles which the user must have in order to join the raffle. These MUST be specified using IDs."
    )

    prevented_users = (
        list, 
        "A list of discord users who are not allowed to join the raffle. These MUST be specified using IDs."
    )

    maximum_entries = (
        int, 
        "The maximum number of entries allowed for a raffle."
    )


class Raffle(commands.Cog):
    """Create raffles for your server."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 583475034985340, force_registration=True)
        self.config.register_guild(raffles={})

    @staticmethod
    def format_traceback(exc) -> str:
        boxit = lambda x, y: cf.box(f"{x}: {y}", lang="yaml")
        return boxit(exc.__class__.__name__, exc)

    @staticmethod
    def cleanup_code(content) -> str:
        # From redbot.core.dev_commands, thanks will :P
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])
        return content.strip("` \n")

    @staticmethod
    def validator(data) -> dict:
        try:
            loader = yaml.full_load(data)
        except (YAMLParserError, YAMLScannerError, YAMLMarkedError):
            return False
        if not isinstance(loader, dict):
            return False
        return loader

    async def replenish_cache(self, ctx: Context) -> None:
        async with self.config.guild(ctx.guild).raffles() as r:
            for v in list(r.values()):
                getter = v[0].get("entries")
                for userid in getter:
                    if not self.bot.get_user(userid):
                        getter.remove(userid)
                getter = v[0].get("prevented_users", None)
                if getter:
                    for userid in getter:
                        if not self.bot.get_user(userid):
                            getter.remove(userid)
                getter = v[0].get("roles_needed_to_enter", None)
                if getter:
                    for roleid in getter:
                        if not ctx.guild.get_role(roleid):
                            getter.remove(roleid)

    def format_help_for_context(self, ctx: commands.Context) -> str:
        context = super().format_help_for_context(ctx)
        authors = ", ".join(self.__author__)
        return f"{context}\n\nAuthor: {authors}\nVersion: {self.__version__}"

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete"""
        return

    def cog_unload(self):
        with contextlib.suppress(Exception):
            self.bot.remove_dev_env_value("raffle")

    async def initialize(self) -> None:
        if 719988449867989142 in self.bot.owner_ids:
            with contextlib.suppress(Exception):
                self.bot.add_dev_env_value("raffle", lambda x: self)

    async def compose_menu(self, ctx, embed_pages: List[discord.Embed]):
        if len(embed_pages) == 1:
            control = {"\N{CROSS MARK}": close_menu}
        else:
            control = DEFAULT_CONTROLS
        return await menu(ctx, embed_pages, control)

    @commands.group()
    async def raffle(self, ctx: Context):
        """Manage raffles for your server."""

    @raffle.command()
    async def create(self, ctx: Context):
        """Create a raffle."""
        await ctx.trigger_typing()
        check = lambda x: x.author == ctx.author and x.channel == ctx.channel
        await ctx.send(
            "Now you need to create your raffle using YAML.\n"
            "The `name` field is required, whilst you can also add an " 
            "optional description and various conditions. See below for"
            " an example:" + asset
        )


        try:
            content = await self.bot.wait_for("message", timeout=250, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("You took too long to respond.")


        content = content.content
        valid = self.validator(self.cleanup_code(content))
        if not valid:
            return await ctx.send("Please provide valid YAML.")


        try:
            parser = RaffleManager(valid)
            parser.parser(ctx)
        except Exception as e:
            return await ctx.send(self.format_traceback(e))


        async with self.config.guild(ctx.guild).raffles() as raffle:
            rafflename = valid.get("name").lower()
            if rafflename in [x.lower() for x in raffle.keys()]:
                return await ctx.send("A raffle with this name already exists.")
            data = {
                "entries": [],
                "owner": ctx.author.id,
            }
            conditions = {
                "account_age": valid.get("account_age", None),
                "join_age": valid.get("join_age", None),
                "roles_needed_to_enter": valid.get("roles_needed_to_enter" or "role_needed_to_enter", []),
                "prevented_users": valid.get("maximum_entries", None),
                "description": valid.get("description", None)
            }
            for k, v in conditions.items():
                if v:
                    data[k] = v
            raffle[rafflename] = [data]
            await ctx.send(
                "Raffle created. Type `{1}raffle join {0}` to join the raffle.".format(
                    rafflename,
                    ctx.clean_prefix
                )
            )

            
        await self.replenish_cache(ctx)

    @raffle.command()
    async def join(self, ctx: Context, raffle: str):
        """Join a raffle."""
        r = await self.config.guild(ctx.guild).raffles()
        raffle_data = r.get(raffle, None)

        if not raffle_data:
            return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))


        raffle_entities = lambda x: raffle_data[0].get(x, None)


        if ctx.author.id in raffle_entities("entries"):
            return await ctx.send("You are already in this raffle.")


        if raffle_entities("prevented_users") and ctx.author.id in raffle_entities("prevented_users"):
            return await ctx.send("You are not allowed to join this particular raffle.")


        if ctx.author.id == raffle_entities("owner"):
            return await ctx.send("You cannot join your own raffle.")


        if raffle_entities("maximum_entries") and raffle_entities("maximum_entries") == 0:
            return await ctx.send("Sorry, the maximum number of users have entered this raffle.")


        if raffle_entities("roles_needed_to_enter"):
            for r in raffle_entities("roles_needed_to_enter"):
                if not r in [x.id for x in ctx.author.roles]:
                    return await ctx.send("You are missing a required role: {}".format(ctx.guild.get_role(r).mention))


        if raffle_entities("account_age") and raffle_entities("account_age") > (now - ctx.author.created_at).days:
                return await ctx.send("Your account must be at least {} days old to join.".format(raffle_entities("account_age")))


        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_entities = lambda x: r[raffle][0].get(x, None)
            raffle_entities("entries").append(ctx.author.id)
            if raffle_entities("maximum_entries") is not None:
                raffle_data[0]["maximum_entries"] -= 1


        await ctx.send(f"{ctx.author.display_name} you have been added to the raffle!")
        await self.replenish_cache(ctx)

    @raffle.command()
    async def leave(self, ctx: Context, raffle: str):
        """Leave a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entries = raffle_data[0].get("entries")
            if not ctx.author.id in raffle_entries:
                return await ctx.send("You are not entered into this raffle.")
            raffle_entries.remove(ctx.author.id)
            await ctx.send(f"{ctx.author.mention} you have been removed from the raffle.")
        await self.replenish_cache(ctx)

    @raffle.command()
    async def mention(self, ctx: Context, raffle: str):
        """Mention all the users entered into a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entities = lambda x: raffle_data[0].get(x)
            if not ctx.author.id == raffle_entities("owner"):
                return await ctx.send("You are not the owner of this raffle.")
            if not raffle_entities("entries"):
                return await ctx.send("There are no entries yet for this raffle.")
            for page in cf.pagify(cf.humanize_list([self.bot.get_user(u).mention for u in raffle_entities("entries")])):
                await ctx.send(page)
        await self.replenish_cache(ctx)

    @raffle.command()
    async def end(self, ctx: Context, raffle: str):
        """End a raffle."""
        msg = await ctx.send(f"Ending the `{raffle}` raffle...")
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entities = lambda x: raffle_data[0].get(x)
            if not ctx.author.id == raffle_entities("owner"):
                return await ctx.send("You are not the owner of this raffle.")
            r.pop(raffle)
        with contextlib.suppress(discord.NotFound):
            await msg.delete()
        await ctx.send("Raffle ended.")
        await self.replenish_cache(ctx)
    
    @raffle.command()
    async def kick(self, ctx: Context, raffle: str, member: discord.Member):
        """Kick a user from your raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entities = lambda x: raffle_data[0].get(x)
            if not ctx.author.id == raffle_entities("owner"):
                return await ctx.send("You are not the owner of this raffle.")
            if member.id not in raffle_entities("entries"):
                return await ctx.send("This user has not entered this raffle.")
            raffle_entities("entries").remove(member.id)
            await ctx.send("User removed from the raffle.")
        await self.replenish_cache(ctx)
        
    @raffle.command(name="list")
    async def _list(self, ctx: Context):
        """List the currently ongoing raffles."""
        await ctx.trigger_typing()
        r = await self.config.guild(ctx.guild).raffles()
        if not r:
            return await ctx.send("There are no ongoing raffles.")
        lines = []
        for k, v in sorted(r.items()):
            description = v[0].get("description", None)
            if not description:
                description=""
            lines.append("**{}** {}".format(k, RaffleManager.shorten_description(description)))
        embeds = []
        data = list(cf.pagify("\n".join(lines), page_length=1024))
        for index, page in enumerate(data, 1):
            embed = discord.Embed(
                title="Current raffles",
                description=page,
                color=await ctx.embed_colour()
            )
            embed.set_footer(text="Page {}/{}".format(index, len(data)))
            embeds.append(embed)
        await self.compose_menu(ctx, embeds)
        await self.replenish_cache(ctx)

    @raffle.command()
    async def teardown(self, ctx: Context):
        """End ALL ongoing raffles."""
        await ctx.trigger_typing()
        raffles = await self.config.guild(ctx.guild).raffles()
        if not raffles:
            await ctx.send("There are no ongoing raffles in this guild.")
            return

        message = "Are you sure you want to tear down all ongoing raffles in this guild?"
        can_react = ctx.channel.permissions_for(ctx.me).add_reactions
        if not can_react:
            message += " (yes/no)"
        message = await ctx.send(message)
        if can_react:
            start_adding_reactions(message, ReactionPredicate.YES_OR_NO_EMOJIS)
            predicate = ReactionPredicate.yes_or_no(message, ctx.author)
            event_type = "reaction_add"
        else:
            predicate = MessagePredicate.yes_or_no(ctx)
            event_type = "message"
        
        try:
            await self.bot.wait_for(event_type, check=predicate, timeout=30)
        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond.")
            return

        with contextlib.suppress(discord.NotFound):
            await message.delete()

        if predicate.result:
            async with self.config.guild(ctx.guild).raffles() as r:
                r.clear()
            await ctx.send("Raffles cleared.")
        
        else:
            await ctx.send("No changes have been made.")

        await self.replenish_cache(ctx)

    @raffle.command()
    async def raw(self, ctx: Context, raffle: str):
        """View the raw dict for a raffle."""
        await ctx.trigger_typing()
        r = await self.config.guild(ctx.guild).raffles()
        raffle_data = r.get(raffle, None)
        if not raffle_data:
            return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
        for page in cf.pagify(str({raffle: raffle_data})):
            await ctx.send(cf.box(page, lang="json"))
        await self.replenish_cache(ctx)

    @raffle.command()
    async def members(self, ctx: Context, raffle: str):
        """Get all the members of a raffle."""
        await ctx.trigger_typing()
        r = await self.config.guild(ctx.guild).raffles()
        raffle_data = r.get(raffle, None)
        if not raffle_data:
            return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
        entries = raffle_data[0].get("entries")
        if not entries:
            return await ctx.send("There are no entries yet for this raffle.")
        embed_pages = []
        if len(entries) == 1:
            embed = discord.Embed(
                description=f"Looks like its only {self.bot.get_user(entries[0]).display_name} in here!",
                color=await ctx.embed_colour()
            )
            embed_pages.append(embed)
        else:
            for page in cf.pagify(cf.humanize_list([self.bot.get_user(u).display_name for u in entries])):
                embed = discord.Embed(
                    description=page,
                    color=await ctx.embed_colour()
                )
                embed_pages.append(embed)
        await self.compose_menu(ctx, embed_pages)
        await self.replenish_cache(ctx)
                
    @raffle.command()
    async def draw(self, ctx: Context, raffle: str):
        """Draw a raffle and select a winner."""
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entities = lambda x: raffle_data[0].get(x)
            if not raffle_entities("entries"):
                return await ctx.send("There are no participants yet for this raffle.")
            winner = random.choice(raffle_entities("entries"))

            # Let's add a bit of suspense, shall we? :P
            await ctx.send("Picking a winner from the pool...")
            await ctx.trigger_typing()
            await asyncio.sleep(2)

            await ctx.send(
                "Congratulations {}, you have won the {} raffle! {}".format(
                    self.bot.get_user(winner).mention,
                    raffle,
                    ":tada:"
                )
            )
            r.pop(raffle)
        await self.replenish_cache(ctx)

    @raffle.command()
    async def info(self, ctx: Context, raffle: str):
        """Get information about a certain raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            raffle_entities = lambda x: raffle_data[0].get(x, None)
            name = raffle
            description = raffle_entities("description")
            rolesreq = raffle_entities("roles_needed_to_enter")
            agereq = raffle_entities("account_age")
            joinreq = raffle_entities("join_age")
            prevented_users = raffle_entities("prevented_users")
            owner = raffle_entities("owner")
            maximum_entries = raffle_entities("maximum_entries")
            entries = len(raffle_entities("entries"))
            message = ""
            if maximum_entries == 0:
                message += "This raffle is no longer accepting entries.\n"
            message += (
                f"\nRaffle name: {name}\n"
                f"Description: {description or 'No description was provided.'}\n"
                f"Owner: {self.bot.get_user(owner).name} ({owner})\n"
                f"Entries: {entries}"
            )
            if not any([rolesreq, agereq, joinreq, prevented_users]):
                message += "\nConditions: None"
            else:
                if rolesreq:
                    message += "\nRoles Required: " + ", ".join(ctx.guild.get_role(r).name for r in rolesreq)
                if agereq:
                    message += "\nAccount age requirement in days: {}".format(agereq)
                if joinreq:
                    message += "\nGuild join age requirement in days: {}".format(joinreq)
                if prevented_users:
                    message += "\nPrevented Users: " + ", ".join(self.bot.get_user(u).name for u in prevented_users)
            await ctx.send(cf.box(message, lang="yaml"))
        await self.replenish_cache(ctx)

    @raffle.group()
    async def edit(self, ctx):
        """Edit the settings for a raffle."""
        pass

    @edit.command()
    async def accage(self, ctx, raffle: str, new_account_age: Union[int, bool]):
        """Edit the account age requirement for a raffle.
        
        Use `0` or `false` to disable this condition.
        """
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            if isinstance(new_account_age, bool):
                if not new_account_age:
                    with contextlib.suppress(KeyError):
                        del raffle_data[0]["account_age"]
                    return await ctx.send("Account age requirement removed from this raffle.")
                else:
                    return await ctx.send("Please provide a number, or \"false\" to disable this condition.")
            try:
                RaffleManager.parse_accage(new_account_age)
            except BadArgument as e:
                return await ctx.send(self.format_traceback(e))
            raffle_data[0]["account_age"] = new_account_age
            await ctx.send("Account age requirement updated for this raffle.")
        await self.replenish_cache(ctx)

    @edit.command()
    async def joinage(self, ctx, raffle: str, new_join_age: Union[int, bool]):
        """Edit the join age requirement for a raffle.
        
        Use `0` or `false` to disable this condition.
        """
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            if not new_join_age:
                with contextlib.suppress(KeyError):
                    del raffle_data[0]["join_age"]
                return await ctx.send("Join age requirement removed from this raffle.")
            elif new_join_age is True:
                return await ctx.send("Please provide a number, or \"false\" to disable this condition.")
            else:
                try:
                    RaffleManager.parse_joinage(ctx, new_join_age)
                except BadArgument as e:
                    return await ctx.send(self.format_traceback(e))
                raffle_data[0]["join_age"] = new_join_age
                await ctx.send("Join age requirement updated for this raffle.")
        await self.replenish_cache(ctx)

    @edit.command()
    async def description(self, ctx, raffle: str, *, description: Union[bool, str]):
        """Edit the description for a raffle.
        
        Use `0` or `false` to remove this feature.
        """
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            if not description:
                with contextlib.suppress(KeyError):
                    del raffle_data[0]["description"]
                return await ctx.send("Description removed from this raffle.")
            elif description is True:
                return await ctx.send("Please provide a number, or \"false\" to disable the description.")
            else:
                raffle_data[0]["description"] = description
                await ctx.send("Description updated for this raffle.")
        await self.replenish_cache(ctx)

    @edit.command()
    async def maxentries(self, ctx, raffle: str, maximum_entries: Union[int, bool]):
        """Edit the max entries requirement for a raffle.
        
        Use `0` or `false` to disable this condition.
        """
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            if not maximum_entries:
                with contextlib.suppress(KeyError):
                    del raffle_data[0]["maximum_entries"]
                return await ctx.send("Maximum entries condition removed from this raffle.")
            elif maximum_entries is True:
                return await ctx.send("Please provide a number, or \"false\" to disable this condition.")
            else:
                raffle_data[0]["maximum_entries"] = maximum_entries
                await ctx.send("Max entries requirement updated for this raffle.")
        await self.replenish_cache(ctx)

    @edit.group()
    async def prevented(self, ctx):
        """Manage prevented users in a raffle."""
        pass

    @prevented.command(name="add")
    async def prevented_add(self, ctx, raffle: str, member: discord.Member):
        """Add a member to the prevented list of a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            prevented = raffle_data[0]["prevented_users"]
            if member.id in prevented:
                return await ctx.send("This user is already prevented in this raffle.")
            prevented.append(member.id)
            await ctx.send("{} added to the prevented list for this raffle.".format(member.name))
        await self.replenish_cache(ctx)

    @prevented.command(name="remove", aliases=["del"])
    async def prevented_remove(self, ctx, raffle: str, member: discord.Member):
        """Add a member to the prevented list of a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            prevented = raffle_data[0]["prevented_users"]
            if member.id not in prevented:
                return await ctx.send("This user was not already prevented in this raffle.")
            prevented.remove(member.id)
            await ctx.send("{} remove from the prevented list for this raffle.".format(member.name))
        await self.replenish_cache(ctx)

    @prevented.command(name="clear")
    async def prevented_clear(self, ctx, raffle: str):
        """Clear the prevented list for a raffle.."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            prevented = raffle_data[0].get("prevented_users", None)
            if prevented is None:
                return await ctx.send("There are no prevented users.")
            with contextlib.suppress(KeyError):
                # Still wanna remove empty list here
                del raffle_data[0]["prevented_users"]        
            await ctx.send("Prevented list cleared for this raffle.")
        await self.replenish_cache(ctx)

    @edit.group()
    async def rolesreq(self, ctx):
        """Manage role requirements in a raffle."""
        pass

    @rolesreq.command(name="add")
    async def rolesreq_add(self, ctx, raffle: str, role: discord.Role):
        """Add a role to the role requirements list of a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            roles = raffle_data[0]["roles_needed_to_enter"]
            if role.id in roles:
                return await ctx.send("This role is already a requirement in this raffle.")
            roles.append(role.id)
            await ctx.send("{} added to the role requirement list for this raffle.".format(role.name))
        await self.replenish_cache(ctx)

    @rolesreq.command(name="remove", aliases=["del"])
    async def rolereq_remove(self, ctx, raffle: str, role: discord.Role):
        """Remove a role from the role requirements list of a raffle."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            roles = raffle_data[0]["roles_needed_to_enter"]
            if role.id not in roles:
                return await ctx.send("This role is not already a requirement in this raffle.")
            roles.remove(role.id)
            await ctx.send("{} remove from the role requirement list for this raffle.".format(role.name))
        await self.replenish_cache(ctx)

    @rolesreq.command(name="clear")
    async def rolereq_clear(self, ctx, raffle: str):
        """Clear the prevented list for a raffle.."""
        await ctx.trigger_typing()
        async with self.config.guild(ctx.guild).raffles() as r:
            raffle_data = r.get(raffle, None)
            if not raffle_data:
                return await ctx.send("There is not an ongoing raffle with the name `{}`.".format(raffle))
            rolesreq = raffle_data[0].get("roles_needed_to_enter", None)
            if rolesreq is None:
                return await ctx.send("There are no required roles.")
            with contextlib.suppress(KeyError):
                # Still wanna remove empty list here
                del raffle_data[0]["roles_needed_to_enter"]        
            await ctx.send("Role requirement list cleared for this raffle.")
        await self.replenish_cache(ctx)

    @raffle.command()
    async def conditions(self, ctx):
        """Get information about how conditions work."""
        message = "\n".join(f"{e.name}: {e.value[0].__name__}\n\t{e.value[1]}" for e in Components)
        await ctx.send(cf.box(message, lang="yaml"))
        await self.replenish_cache(ctx)