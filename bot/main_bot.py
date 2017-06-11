#!/usr/bin/env python
# -*-coding=utf-8 -*-

"""
MIT License

Copyright (c) 2017 s0hvaperuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import logging
import re
import asyncio
from random import choice

import discord
from aiohttp import ClientSession
from validators import url as test_url

from bot import audio
from bot.bot import Bot
from bot.exceptions import *
from bot.globals import *
from bot.management import Management
from bot.permissions import parse_permissions
from utils import wolfram, memes, hearthstone, jojo
from utils.search import Search
from utils.utilities import (write_playlist, read_lines, empty_file, y_n_check,
                             split_string, slots2dict, retry)
from utils.voting import VoteManager
from bot.emotes import Emotes

logger = logging.getLogger('debug')
HALFWIDTH_TO_FULLWIDTH = str.maketrans(
    '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!"#$%&()*+,-./:;<=>?@[]^_`{|}~',
    '０１２３４５６７８９ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ！゛＃＄％＆（）＊＋、ー。／：；〈＝〉？＠［］＾＿‘｛｜｝～')


def start(config, permissions):
    client = ClientSession()
    bot = Bot(command_prefix='!', config=config, aiohttp_client=client, pm_help=True, permissions=permissions)
    permissions.bot = bot
    hi_new = {ord(c): '' for c in ", '"}

    sound = audio.Audio(bot, client)
    search = Search(bot, client)
    management = Management(bot)
    votes = VoteManager(bot)

    async def _wants_to_be_noticed(member, server, remove=True):
        role = list(filter(lambda r: r.id == '318762162552045568', server.roles))
        if not role:
            return

        role = role[0]

        name = member.name if not member.nick else member.nick
        if ord(name[0]) <= 46:
            for i in range(0, 2):
                try:
                    await bot.add_roles(member, role)
                except:
                    pass
                else:
                    break

        elif remove and role in member.roles:
                await retry(bot.remove_roles, member, role)

    @bot.command(pass_context=True, owner_only=True)
    async def test(ctx, channel, msg):
        channel = bot.get_channel(channel)
        message = await bot.get_message(channel, msg)
        val = await votes.get_most_voted(message)

    @bot.event
    async def on_ready():
        print('[INFO] Logged in as {0.user.name}'.format(bot))
        await bot.change_presence(game=discord.Game(name=config.game))

    @bot.event
    async def on_member_join(member):
        server = member.server
        server_config = management.get_config(server.id)
        if server_config is None:
            return

        conf = server_config.get('join', None)
        if conf is None:
            return

        channel = server.get_channel(conf['channel'])
        if channel is None:
            return

        message = management.format_join_leave(member, conf)

        await bot.send_message(channel, message)

        if conf['add_color']:
            colors = server_config.get('colors', {})

            if colors and channel is not None:
                role = None
                for i in range(3):
                    color = choice(list(colors.values()))
                    roles = server.roles
                    role = list(filter(lambda r: r.id == color, roles))
                    if role:
                        break

                if role:
                    await bot.add_roles(member, role[0])

        if server.id == '217677285442977792':
            await _wants_to_be_noticed(member, server, remove=False)

    @bot.event
    async def on_member_remove(member):
        server = member.server
        conf = management.get_leave(server.id)
        if conf is None:
            return

        channel = server.get_channel(conf['channel'])
        if channel is None:
            return

        d = slots2dict(member)
        d.pop('user', None)
        message = conf['message'].format(user=str(member), **d)
        await bot.send_message(channel, message)

    @bot.event
    async def on_member_update(before, after):
        server = after.server
        if server.id == '217677285442977792':
            name = before.name if not before.nick else before.nick
            name2 = after.name if not after.nick else after.nick
            if name == name2:
                return

            await _wants_to_be_noticed(after, server)

    @bot.command(pass_context=True, owner_only=True)
    async def notice_me(ctx):
        server = ctx.message.server
        if server.id == '217677285442977792':
            await bot.request_offline_members(server)
            for member in list(server.members):
                print(member.name)
                await _wants_to_be_noticed(member, server)

    @bot.command(pass_context=True, owner_only=True)
    async def convert_colors(ctx):
        server = ctx.message.server
        roles = server.roles
        colors = management.get_colors(server.id)
        colors_temp = {}
        roles = list(filter(lambda r: str(r) in colors, roles))

        for role in roles:
            colors_temp[role.name.lower()] = role.id

        conf = management.get_config(server.id)
        conf['colors'] = colors_temp
        management.save_json()

    @bot.event
    async def on_message_edit(before, after):
        if before.author.bot:
            return

        conf = management.get_config(before.server.id).get('on_edit', None)
        if not conf:
            return

        channel = before.server.get_channel(conf['channel'])
        if channel is None:
            return

        bef_content = before.content
        aft_content = after.content
        if bef_content == aft_content:
            return

        user = before.author

        message = conf['message']
        d = slots2dict(user)
        for e in ['name', 'before', 'after']:
            d.pop(e, None)

        d['channel'] = after.channel.mention
        message = message.format(name=str(user), **d,
                                 before=bef_content, after=aft_content)

        message = split_string(message, maxlen=1960)
        for m in message:
            await bot.send_message(channel, m)

    @bot.command(pass_context=True)
    @bot.event
    async def on_message_delete(msg):
        if msg.author.bot:
            return

        conf = management.get_config(msg.server.id).get('on_delete', None)
        if conf is None:
            return

        channel = msg.server.get_channel(conf['channel'])
        if channel is None:
            return

        content = msg.content
        user = msg.author

        message = conf['message']
        d = slots2dict(user)
        for e in ['name', 'message']:
            d.pop(e, None)

        d['channel'] = msg.channel.mention
        message = message.format(name=str(user), message=content, **d)
        message = split_string(message)
        for m in message:
            await bot.send_message(channel, m)

    async def get_ow(bt):
        async with client.get('https://api.lootbox.eu/pc/eu/%s/profile' % bt) as r:
            if r.status == 200:
                js = await r.json()
                js = js['data']
                print(js)
                quick = js['games']['quick']
                cmp = js['games']['competitive']
                winrate_qp = round(int(quick['wins']) / int(quick['played']) * 100, 2)
                winrate_cmp = round(int(cmp['wins']) / int(cmp['played']) * 100, 2)

                return 'Winrate for {0} is {1}% in quick play and {2}% in ' \
                       'competitive.'.format(bt.replace('-', '#'), winrate_qp,
                                             winrate_cmp)

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return

        if message.server.id == '217677285442977792':
            if len(message.mentions) + len(message.role_mentions) > 10:
                whitelist = management.get_mute_whitelist(message.server.id)
                invulnerable = discord.utils.find(lambda r: r.id in whitelist,
                                                  message.server.roles)
                if invulnerable is None or invulnerable not in message.author.roles:
                    role = discord.utils.find(lambda r: r.id == '322837972317896704',
                                              message.server.roles)
                    if role is not None:
                        await bot.add_roles(message.author, role)
                        await bot.send_message(message.channel,
                                               'Muted {0.mention}'.format(message.author))

        # If the message is a command do that instead
        if message.content.startswith('!'):
            await bot.process_commands(message)
            return

        if message.content.lower() == 'o shit':
            msg = 'waddup'
            await bot.send_message(message.channel, msg)

            herecome = await bot.wait_for_message(timeout=12, author=message.author, content='here come')
            if herecome is None:
                await bot.send_message(message.channel, ':(')
            else:
                await bot.send_message(message.channel, 'dat boi')
            return

        elif message.content.lower().translate(hi_new) == 'hiimnew':
            await bot.send_message(message.channel, 'Hi new, I\'m dad')

    @bot.command(pass_context=True)
    async def set_battletag(ctx, battletag):
        """
        Set a battletag that matches your discord account
        so you don't have to specify it all the time.
        """
        msg = ctx.message
        with open('battletags.json', 'r+') as f:
            data = json.load(f)
            battletag = battletag.replace('#', '-')
            data['users'][msg.author.id] = battletag
            f.seek(0)
            json.dump(data, f, indent=4)
        await bot.send_message(msg.channel, "Battle.net account for %s was set." % msg.author.name)

    @bot.command(name='commands', pass_context=True, ignore_extra=True)
    async def bot_commands(ctx):
        s = ''

        seen = set()
        commands = bot.commands.values()
        commands = [seen.add(c.name) or c for c in commands if c.name not in seen]
        del seen
        commands = sorted(commands, key=lambda c: c.name)

        for command in commands:
            try:
                s += '{}: level {}\n'.format(command.name, command.level)
            except Exception as e:
                print('[ERROR] Command info failed. %s' % e)

        s = split_string(s, splitter='\n')
        for string in s:
            await bot.send_message(ctx.message.author, string)

    async def check_commands(commands, level, channel):
        if commands is None:
            return

        _commands = set()
        for command in commands:
            if command.strip() == '':
                continue

            if command not in bot.commands:
                raise BotValueError('Command %s not found' % command)

            c = commands[command]
            if c.level > level:
                await bot.say_timeout('Cannot add command %s because commands requires level %s and yours is %s', channel, 120)
            _commands.add(c.name)

        return ', '.join(_commands)

    @bot.command(pass_context=True)
    async def permission_options(ctx):
        s = 'Permission group options and default values'
        for k, v in PERMISSION_OPTIONS.items():
            s += '\n{}={}'.format(k, v)

        await bot.send_message(ctx.message.channel, s)

    @bot.command(name='eval', pass_context=True, owner_only=True)
    async def eval_(ctx, *, message):
        return
        nonlocal bot
        try:
            retval = eval(message)
            if asyncio.iscoroutine(retval):
                retval = await retval

        except Exception as e:
            import traceback
            traceback.print_exc()
            retval = 'Exception\n%s' % e

        if not retval:
            retval = 'Done'

        await bot.say(retval)

    @bot.command(name='exec', pass_context=True, owner_only=True)
    async def exec_(ctx, *, message):
        return
        nonlocal bot
        try:
            retval = exec(message)
            if asyncio.iscoroutine(retval):
                retval = await retval

        except Exception as e:
            import traceback
            traceback.print_exc()
            retval = 'Exception\n%s' % e

        if not retval:
            retval = 'Done'

        await bot.say(retval)

    @bot.command(name='roles', pass_context=True, level=5)
    async def get_roles(ctx):
        server_roles = sorted(ctx.message.server.roles, key=lambda r: r.name)
        roles = 'A total of %s roles\n' % len(server_roles)
        for role in server_roles:
            roles += '{}: {}\n'.format(role.name, role.mention)

        roles = split_string(roles, splitter='\n')
        for s in roles:
            await bot.say('```' + s + '```')

    @bot.command(pass_context=True, level=5)
    async def create_permissions(ctx, *args):
        print(args, ctx)
        user_permissions = ctx.user_permissions
        args = ' '.join(args)
        args = re.findall(r'([\w\d]+=[\w\d\s]+)(?= [\w\d]+=[\w\d\s]+|$)', args)  # Could be improve but I don't know how

        kwargs = {}

        for arg in args:
            try:
                k, v = arg.split('=')
            except ValueError:
                raise BotValueError('Value %s could not be parsed' % arg)

            kwargs[k] = v.strip()

        channel = ctx.message.channel
        kwargs = parse_permissions(kwargs, user_permissions)
        kwargs['whitelist'] = await check_commands(kwargs['whitelist'], user_permissions.level, channel)
        kwargs['blacklist'] = await check_commands(kwargs['blacklist'], user_permissions.level, channel)

        msg = 'Confirm the creation if a permission group with{}\ny/n'.format(kwargs)
        await bot.say_timeout(msg, ctx.message.channel, 40)
        msg = await bot.wait_for_message(timeout=30, author=ctx.message.author, channel=channel, check=y_n_check)

        if msg is None or msg in ['n', 'no']:
            return await bot.say_timeout('Cancelling', ctx.message.channel, 40)

        bot.permissions.create_permissions_group(**kwargs)

    @bot.command(pass_context=True, level=5)
    async def set_permissions(ctx, group_name, *args):
        group = bot.permissions.get_permission_group(group_name)
        channel = ctx.message.channel
        perms = ctx.user_permissions
        if perms is None:
            return

        if group is None:
            return await bot.say_timeout('Permission group %s not found' % group_name, channel, 60)

        if group.level >= perms.level >= 0 and not perms.master_override:
            raise BotException('Your level must be higher than the groups level')

        if group.master_override and not perms.master_override:
            raise BotException("You cannot set roles with master override on if you don't have it yourself")

        u = ctx.message.mentions

        users = []
        if perms.master_override:
            users = [(None, i) for i in u]
        else:
            for user in u:
                users.append((bot.permissions.get_permissions(user.id), user))

        for role in ctx.message.role_mentions:
            usrs = bot.get_role_members(role, ctx.message.server)
            for user in usrs:
                if perms.master_override:
                    users.append((None, user))
                else:
                    users.append((bot.permissions.get_permissions(user.id), user))

        valid_users = []
        for user_perms, user in users:
            if perms.master_override:
                valid_users.append(user)
            elif 0 <= perms.level <= user_perms.level:
                await bot.say('Cannot change permission of %s because your level is too low' % user.name)
            else:
                valid_users.append(user)

        errors = bot.permissions.set_permissions(group, *valid_users)

        for user, e in errors.items():
            await bot.say('Could not change the permissions of %s because of an error. %s' % (user.name, e))

        await bot.say('Permissions set for %s users' % len(valid_users))

    @bot.command(pass_context=True)
    async def ow_stats(ctx, battletag=None):
        """Gets your winrate in competitive and quick play"""
        msg = ctx.message

        with open('battletags.json', 'r+') as f:
            data = json.load(f)

        if battletag is None:
            try:
                battletag = data['users'][msg.author.id]
            except KeyError:
                await bot.send_message(msg.channel, "No battle.net account associated with this discord user.")
                return

        await bot.send_message(msg.channel, await get_ow(battletag))

    @bot.command(pass_context=True)
    async def math(ctx, *args):
        """Queries a math problem to be solved by wolfram alpha"""
        calc = ' '.join([*args])
        await bot.send_message(ctx.message.channel, await wolfram.math(calc, client, config.wolfram_key))

    @bot.command(pass_context=True, ignore_extra=True)
    async def twitchquote(ctx):
        """Random twitch quote from twitchquotes.com"""
        await bot.send_message(ctx.message.channel, await memes.twitch_poems(client))

    @bot.command(name='say', pass_context=True)
    async def say_command(ctx, *, words):
        """Says the text that was put as a parameter"""
        await bot.send_message(ctx.message.channel, '{0} {1}'.format(ctx.message.author.mention, words))

    @bot.command(pass_context=True, ignore_extra=True, owner_only=True)
    async def add_all(ctx):
        songs = set(read_lines(ADD_AUTOPLAYLIST))

        invalid = []
        for song in list(songs):
            if not test_url(song):
                songs.remove(song)
                invalid.append(song)

        if invalid:
            await bot.say_timeout('Invalid url(s):\n%s' % ', '.join(invalid), ctx.message.channel, 40)

        write_playlist(AUTOPLAYLIST, songs, 'a')
        empty_file(ADD_AUTOPLAYLIST)

        amount = len(songs)
        await bot.say_timeout('Added %s song(s) to autoplaylist' % amount, ctx.message.channel, 60)

    @bot.command(pass_context=True, ignore_extra=True, owner_only=True)
    async def delete_all(ctx):
        delete_songs = set(read_lines(DELETE_AUTOPLAYLIST))

        songs = set(read_lines(AUTOPLAYLIST))

        failed = 0
        succeeded = 0
        for song in delete_songs:
            try:
                songs.remove(song)
                succeeded += 1
            except KeyError as e:
                failed += 1
                print('[EXCEPTION] KeyError: %s' % e)

        write_playlist(AUTOPLAYLIST, songs)

        empty_file(DELETE_AUTOPLAYLIST)

        await bot.say_timeout('Successfully deleted {0} songs and failed {1}'.format(succeeded, failed),
                              ctx.message.channel, 60)

    @bot.command(pass_context=True, ignore_extra=True)
    async def playlists(ctx):
        p = os.path.join(os.getcwd(), 'data', 'playlists')
        files = os.listdir(p)
        sort = filter(lambda f: os.path.isfile(os.path.join(p, f)), files)
        await bot.say_timeout('Playlists: {}'.format(', '.join(sort)), ctx.message.channel)

    @bot.command(pass_context=True, owner_only=True)
    async def shutdown(ctx):
        try:
            await bot.change_presence()
            await sound.shutdown()
            for message in bot.timeout_messages.copy():
                await message.delete_now()
                message.cancel_tasks()

            bot.aiohttp_client.close()

        except Exception as e:
            print('[ERROR] Error while shutting down %s' % e)
        finally:
            await bot.close()

    bot.add_cog(search)
    bot.add_cog(sound)
    bot.add_cog((hearthstone.Hearthstone(bot, config.mashape_key, bot.aiohttp_client)))
    bot.add_cog(jojo.JoJo(bot))
    bot.add_cog(management)
    bot.add_cog(Emotes(bot))
    bot.add_cog(votes)

    bot.run(config.token)
