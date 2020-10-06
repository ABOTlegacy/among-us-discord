import queue
import threading
import server
from room import *
import asyncio
from time import time
import discord
from discord.ext import commands
from discord.ext.tasks import loop

class Channels:
    room_creation_voice = None
    general_voice = None
    room_category = None

class Embeds:
    help = None

channels = Channels()
discord_loop = None
room_refresh_queue = queue.Queue()
room_set_code_queue = queue.Queue()

async def move_users(src: discord.VoiceChannel, dst: discord.VoiceChannel):
    # await channels.mover_channel.send(f"> move {src.position + 1} {dst.position + 1}")
    for member in src.members:
        await member.move_to(dst)

def add_room_to_refresh(room_: Room):
    room_refresh_queue.put(room_)

def add_room_to_set_code(room_: Room, code):
    room_set_code_queue.put((room_, code))

async def mute_member(member, mute_):
    # try:
    if member.voice.mute != mute_:
        print(int(time()), "- setting mute for", member.name, mute_)
        await member.edit(mute=mute_)

        print(int(time()), "- set mute for", member.name, mute_)
    # except Exception as e:
    #     print("couldn't set \"", member.name, "\"'s server mute to ", mute_, "\n", e, sep="")

def run_discord(settings, embeds):
    global discord_loop
    discord_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(discord_loop)
    client = commands.Bot(command_prefix="!au ")
    client.remove_command('help')

    server_thread = threading.Thread(target=server.create_server, args=(
        settings.bot_server_port, discord_loop, add_room_to_refresh, add_room_to_set_code))
    server_thread.daemon = True
    server_thread.start()

    @client.event
    async def on_voice_state_update(member, before, after):
        if after.channel == before.channel:
            return
        if after.channel is not None:
            found_room = False
            for room_ in rooms:
                room_ = rooms[room_]
                if after.channel.id == room_.voice.id:
                    found_room = True
                    add_room_to_refresh(room_)
                    break
            if not found_room:
                await mute_member(member, 0)

        if before.channel is not None:
            for room_ in rooms:
                room_ = rooms[room_]
                if room_.owner.id == member.id and before.channel.id == room_.voice.id and \
                        (after.channel is None or after.channel.id != room_.voice.id):
                    await room_.close()
                    break

    def check_command(ctx: commands.context.Context):
        return (not ctx.author.bot) and (ctx.guild is not None)

    @client.command()
    async def room(ctx, *args):
        if not check_command(ctx):
            return
        if len(args) >= 1:
            switch = {
                "create": room_create,
                "list": room_list,
                "invite": room_invite,
                "restart": room_restart,
                "close": room_close,
                "debug": room_debug
            }
            await switch.get(args[0], help_dm)(ctx)

    async def room_create(ctx: commands.context.Context):
        message = await ctx.send("Creating room, please wait...")
        try:
            room_ = Room(ctx.author, settings, client)
        except RoomAlreadyExistsException:
            await message.edit(content="Your room already exists.")
            return
        except UserNotInRoomCreationVoiceException:
            link = await create_invite(channels.room_creation_voice)

            await message.edit(
                content=f"You must join the <#{settings.room_creation_voice_id}> voice channel to start a room.\n"
                           f"click here: <{link}>")
            return
        except UserNotInVoiceException:
            await message.edit(
                content=f"You must join a voice channel to start a room.")
            return

        await message.edit(content=await room_invite_text(room_))

    async def room_invite(ctx: commands.context.Context):
        await ctx.send(await room_invite_text(ctx.author.id))

    async def room_restart(ctx: commands.context.Context):
        if ctx.author.id in rooms:
            room_ = rooms[ctx.author.id]
            room_.mute = 0
            add_room_to_refresh(room_)
            await room_.generate_secret()

            await ctx.send(f"Room restarted.")
        else:
            await ctx.send("You do not own any rooms.")

    async def room_invite_text(room_):
        if not isinstance(room_, Room):
            if room_ in rooms:
                room_ = rooms[room_]
            else:
                return "You do not own any rooms."

        link = await create_invite(room_.voice)
        return f"<#{room_.voice.id}> - <{link}>"

    async def room_list(ctx: commands.context.Context):
        content = "Available rooms:"
        for owner_id in rooms:
            content += f"\n{await room_invite_text(owner_id)}"

        await ctx.send(content)

    async def room_close(ctx: commands.context.Context):
        if ctx.author.id in rooms:
            room_ = rooms[ctx.author.id]
            await room_.close()

            await ctx.send(f"Room closed.")
        else:
            await ctx.send("You do not own any rooms.")

    async def room_debug(ctx: commands.context.Context):
        if ctx.author.id in rooms:
            room_ = rooms[ctx.author.id]

            await ctx.send(f"Room {room_}.")
        else:
            await ctx.send("You do not own any rooms.")

    async def create_invite(channel):
        invites = await channel.invites()

        link = None
        for invite in invites:
            if invite.inviter.id == client.user.id:
                link = invite

        if link is None:
            link = await channel.create_invite(unique=False)

        return link

    @client.command()
    async def help(ctx: commands.context.Context):
        if not check_command(ctx):
            return
        await ctx.send(embed=Embeds.help)

    async def help_dm(ctx: commands.context.Context):
        await ctx.author.send(embed=Embeds.help)

    @client.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            if not check_command(ctx):
                return
            await help_dm(ctx)
        print("error", error)

    @client.event
    async def on_message(message: discord.Message):
        if not check_command(message):
            return
        if message.content == client.command_prefix.strip():
            await message.channel.send(embed=Embeds.help)
        await client.process_commands(message)

    @loop(seconds=1)
    async def refresh_rooms_from_queue():
        try:
            room_ = room_refresh_queue.get(False)
        except queue.Empty:
            return
        await room_.refresh()
        room_refresh_queue.task_done()

    @loop(seconds=1)
    async def room_set_code_from_queue():
        try:
            room_, code = room_set_code_queue.get(False)
        except queue.Empty:
            return
        await room_.set_code(code)
        room_set_code_queue.task_done()

    @client.event
    async def on_ready():
        if settings.room_creation_voice_id != "":
            channels.room_creation_voice = client.get_channel(settings.room_creation_voice_id)

        if settings.general_voice_id != "":
            channels.general_voice = client.get_channel(settings.general_voice_id)

        channels.room_category = client.get_channel(settings.room_category_id)

        Embeds.help = discord.Embed.from_dict(embeds[0])

        print("Bot is ready")

    refresh_rooms_from_queue.start()
    room_set_code_from_queue.start()
    client.run(settings.bot_token)