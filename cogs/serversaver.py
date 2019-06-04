from discord.ext import commands
import discord
import json
import re
import io
import sys
import traceback


class ServerSaver(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = []

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def copy(self, ctx):
        """Generates a server config .json, uploads both to discord and saves to /saved_servers.
        File to be used with the paste command. """
        data = {"roles": {}, "bans": [], "channels": {}, "categories": {}, "voice_channels": {}}
        guild = ctx.guild

        for role in guild.roles:
            if role.is_default():
                continue
            data["roles"][role.name] = {}
            role_data = data["roles"][role.name]
            role_data["permissions"] = role.permissions.value
            role_data["color"] = role.color.value
            role_data["position"] = role.position
            role_data["mentionable"] = role.mentionable
            role_data["hoisted"] = role.hoist

        bans = await guild.bans()

        for ban in bans:
            if ban[1] is None:
                continue
            data["bans"].append(ban[1].id)

        for channel in guild.channels:
            if isinstance(channel, discord.TextChannel):
                data["channels"][channel.name] = {}
                channel_data = data["channels"][channel.name]
                channel_data["is_nsfw"] = channel.is_nsfw()
                channel_data["category"] = channel.category.name if channel.category else False
                channel_data["topic"] = channel.topic if channel.topic else ''

            elif isinstance(channel, discord.VoiceChannel):
                data["voice_channels"][channel.name] = {}
                channel_data = data["voice_channels"][channel.name]
                channel_data["user_limit"] = channel.user_limit
                channel_data["category"] = channel.category.name if channel.category else False

            elif isinstance(channel, discord.CategoryChannel):
                data["categories"][channel.name] = {}
                channel_data = data["categories"][channel.name]
                channel_data["is_nsfw"] = channel.is_nsfw()

            else:
                print("unknown channel type for channel {}".format(channel.name))
                continue

            channel_data["position"] = channel.position
            channel_data["permissions"] = {}

            for role, permission_overwrite in channel.overwrites[1:]:
                if isinstance(role, (discord.Member, discord.User)):
                    continue
                channel_data["permissions"][role.name] = {}
                overwrite_data = channel_data["permissions"][role.name]
                for perm, value in permission_overwrite:
                    if value is not None:
                        overwrite_data[perm] = value

        filename = "saved_servers/{}.json".format(re.sub(r'[/\\:*?"<>|\x00-\x1f]', '', guild.name))

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        await ctx.send("Done!") #, file=discord.File(fp=filename)) - remove posting of config LOL

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def paste(self, ctx, type, *, filename=None):
        """Pastes a server config. Can either specify a filename, or upload as attachment.
        Possible types: all, roles, categories, channels, bans. Can specify multiple types, seperated by ;, no spaces.
        If channels is done without doing categories first, or categories done without roles first, the result will be unordered."""
        if ctx.guild.id in self.sessions:
            await ctx.send("Already preforming a paste in this server!")
            return
        else:
            self.sessions.append(ctx.guild.id)

        guild = ctx.guild
        if not filename:
            attachments = ctx.message.attachments

            if attachments:
                if ".json" in attachments[0].filename:
                    file_object = io.BytesIO()
                    await attachments[0].save(file_object)
                    try:
                        data = json.load(file_object)
                    except json.JSONDecodeError:
                        await ctx.send("Error parsing attachment {}".format(attachments[0].filename))
                        return
                else:
                    await ctx.send("File must be a json file!")
                    return

        else:
            try:
                with open("saved_servers/{}.json".format(filename.replace(".json", ""))) as f:
                    data = json.load(f)
            except FileNotFoundError:
                await ctx.send("File {} not found!".format(filename+".json"))
                return
            except json.JSONDecodeError:
                await ctx.send("Error parsing file {}".format(filename+".json"))
                return

        async def paste_bans():
            if not guild.me.guild_permissions.ban_members:
                ctx.send("Missing permission: Ban members")
                return
            ban_ids = data["bans"]
            m = await ctx.send("Processing bans... 0/{}".format(len(ban_ids)))
            for pos, id in enumerate(ban_ids):
                fake_member = discord.Object(id=id)
                await guild.ban(fake_member)
                if not pos % 10:
                    await m.edit(content="Processing bans... {}/{}".format(pos, len(ban_ids)))
            await m.edit(content="Processing bans... {0}/{0}\n**Done!**".format(len(ban_ids)))

        async def paste_loop(type_name, data, function, reverse=False, update_every = 5):
            things = data[type_name]
            total = len(things.values())
            m = await ctx.send("Processing {}... 0/{}".format(type_name, total))
            existing_thing_names = {x.name for x in guild.__getattribute__(type_name)}
            passed = []
            sorted_things = sorted(list(things.items()), key=lambda x: x[1]["position"], reverse=reverse)
            skipped = ''
            for pos, (name, thing_data) in enumerate(sorted_things):
                if name in existing_thing_names:
                    passed.append(name)
                    skipped = "Skipped {} items: {}".format(len(passed), ", ".join(passed))
                    continue
                await function(name, thing_data)
                if not pos % update_every:
                    await m.edit(content="Processing {}... {}/{}\n{}".format(type_name, pos, total, skipped))
            final_message = "Processing {0}... {1}/{1}\n{2}\n**Done!**".format(type_name, total, skipped)
            await m.edit(content=final_message)

        async def paste_roles_func(name, role_data):
            await guild.create_role(name=name,
                                    permissions=discord.Permissions(
                                        permissions=role_data["permissions"]),
                                    color=discord.Color(value=role_data["color"]),
                                    mentionable=role_data["mentionable"],
                                    hoist=role_data["hoisted"])


        def to_overwrite_pairs(data:dict):
            out_overwrite = {}

            for key, value in data.items():
                role = discord.utils.get(guild.roles, name=key)
                if role is None:
                    continue
                perm_overwrite = discord.PermissionOverwrite()
                perm_overwrite.update(**value)
                out_overwrite[role] = perm_overwrite

            return out_overwrite

        async def paste_category_func(name, cat_data):
            overwrites = to_overwrite_pairs(cat_data["permissions"])
            category = await guild.create_category(name, overwrites=overwrites)
            if cat_data["is_nsfw"]:
                await category.edit(nsfw=True)

        async def paste_channel_func(name, channel_data):
            overwrites = to_overwrite_pairs(channel_data["permissions"])
            if channel_data["category"]:
                category = discord.utils.get(ctx.guild.categories, name=channel_data["category"])
            else:
                category = None
            channel = await guild.create_text_channel(name, overwrites=overwrites,
                                                      category=category)
            if channel_data["is_nsfw"] or channel_data["topic"]:
                await channel.edit(nsfw=channel_data["is_nsfw"], topic=channel_data["topic"])

        async def paste_voice_channel_func(name, channel_data):
            overwrites = to_overwrite_pairs(channel_data["permissions"])
            if channel_data["category"]:
                category = discord.utils.get(ctx.guild.categories, name=channel_data["category"])
            else:
                category = None
            voice_channel = await guild.create_voice_channel(name, overwrites=overwrites, category=category)
            if channel_data["user_limit"]:
                await voice_channel.edit(user_limit=channel_data["user_limit"])

        type = type.lower()

        if 'all' in type:
            if not all([guild.me.guild_permissions.manage_roles,
                       guild.me.guild_permissions.manage_channels,
                       guild.me.guild_permissions.ban_members]):
                ctx.send("Missing permission(s)")
                return
            await paste_loop("roles", data, paste_roles_func, reverse=True)
            await paste_loop("categories", data, paste_category_func)
            await paste_loop("channels", data, paste_channel_func)
            await paste_bans()
            await paste_loop("voice_channels", data, paste_voice_channel_func)
            await ctx.send("Finished pasting!")
            return

        if "roles" in type:
            if not guild.me.guild_permissions.manage_roles:
                ctx.send("Missing permission: manage_roles")
                return
            await paste_loop("roles", data, paste_roles_func, reverse=True)
        if "categories" in type:
            if not guild.me.guild_permissions.manage_channels:
                ctx.send("Missing permission: manage_channels")
                return
            await paste_loop("categories", data, paste_category_func)
        if "channels" in type:
            if not guild.me.guild_permissions.manage_channels:
                ctx.send("Missing permission: manage_channels")
                return
            await paste_loop("channels", data, paste_channel_func)
            await paste_loop("voice_channels", data, paste_voice_channel_func)
        if "bans" in type:
            if not guild.me.guild_permissions.ban_members:
                ctx.send("Missing permission: ban_members")
                return
            await paste_bans()

        await ctx.send("Finished pasting!")
        self.sessions.remove(ctx.guild.id)


    @paste.error
    async def paste_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            ctx.send("Missing arugments!")
        else:
            try:
                self.sessions.remove(ctx.guild.id)
            except ValueError:
                pass
            print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


def setup(bot):
    bot.add_cog(ServerSaver(bot))