import json
import os
import re
from random import choice
from threading import Lock

import discord
from colour import Color
from discord.ext.commands import cooldown, BucketType
from datetime import datetime

from bot.bot import command
from utils.utilities import slots2dict, split_string, call_later, datetime2sql, parse_timeout
import logging

logger = logging.getLogger('debug')


class ManagementHandler:
    def __init__(self, bot):
        self.bot = bot
        self.servers = {}
        self.path = os.path.join(os.getcwd(), 'data', 'servers.json')
        self._lock = Lock()
        self.reload_config()

    def reload_config(self):
        if os.path.exists(self.path):
            with open(self.path, 'r') as f:
                self.servers = json.load(f)

    @staticmethod
    def msg2dict(msg):
        d = {}
        attachments = [attachment['url'] for attachment in msg.attachments if 'url' in attachment]
        d['attachments'] = ', '.join(attachments)
        return d

    def format_on_edit(self, before, after, conf, check_equal=True):
        bef_content = before.content
        aft_content = after.content
        if check_equal:
            if bef_content == aft_content:
                return

        user = before.author

        message = conf['message']
        d = self.msg2dict(before)
        d = slots2dict(user, d)
        for e in ['name', 'before', 'after']:
            d.pop(e, None)

        d['channel'] = after.channel.mention
        message = message.format(name=str(user), **d,
                                 before=bef_content, after=aft_content)

        return message

    @staticmethod
    def format_join_leave(member, conf):
        d = slots2dict(member)
        d.pop('user', None)
        message = conf['message'].format(user=str(member), **d)
        return message

    def format_on_delete(self, msg, conf):
        content = msg.content
        user = msg.author

        message = conf['message']
        d = self.msg2dict(msg)
        d = slots2dict(user, d)
        for e in ['name', 'message']:
            d.pop(e, None)

        d['channel'] = msg.channel.mention
        message = message.format(name=str(user), message=content, **d)
        return message

    @staticmethod
    def get_channel(s, server):
        matches = re.findall(r'(?!<#)*\d+(?=>)*', s)
        if matches:
            id = matches[0]
            channel = server.get_channel(id)
            return channel

    def add_color_to_json(self, name, serverid, roleid):
        colors = self.get_colors(serverid)
        colors[name] = roleid

        self.save_json()

    def delete_color_from_json(self, name, serverid):
        colors = self.get_colors(serverid)
        try:
            del colors[name]
        except Exception as e:
            print(e)

        self.save_json()

    @staticmethod
    def get_roles_from_ids(server_roles, *ids):
        roles = []
        for r in ids:
            try:
                int(r)
            except:
                continue

            r = discord.utils.find(lambda role: role.id == str(r), server_roles)
            if r:
                roles.append(r)

        return roles

    @staticmethod
    def get_users_from_ids(server, *ids):
        users = []
        for i in ids:
            try:
                int(i)
            except:
                continue

            user = server.get_member(i)
            if user:
                users.append(user)

        return users

    async def join_leave(self, ctx, channel, message, add_color, join=True):
        key = 'join' if join else 'leave'
        user = ctx.message.author
        channel_ = ctx.message.channel
        server = ctx.message.server
        if not user.permissions_in(channel_).manage_channels:
            return await self.bot.send_message(channel_, "You don't have manage channel permissions")

        chn = self.get_channel(channel, server)
        if chn is None:
            return await self.bot.send_message(channel_, 'Could not get channel %s' % channel)

        config = self.servers.get(server.id, {})
        config[key] = {'message': message, 'channel': chn.id,
                       'add_color': add_color}
        self.servers[server.id] = config
        self.save_json()
        config_ = json.dumps(config[key], ensure_ascii=False)
        await self.bot.say_timeout('Current {} message config ```json\n{}```'.format(key, config_),
                                   channel_, 120)

        return config

    async def message_edited(self, ctx, channel, message, key='on_edit'):
        user = ctx.message.author
        channel_ = ctx.message.channel
        server = ctx.message.server
        if not user.permissions_in(channel_).manage_channels:
            return await self.bot.send_message(channel_, "You don't have manage channel permissions")

        config = self.get_config(server.id)
        chn = self.get_channel(channel, server)
        if chn is None:
            return await self.bot.send_message(channel_,
                                               'Could not get channel %s' % channel)

        config[key] = {'message': message, 'channel': chn.id}
        self.save_json()
        config_ = json.dumps(config[key], ensure_ascii=False)
        await self.bot.send_message(channel_,
                                    '{} config ```json\n{}```'.format(key, config_))

        return config

    def save_json(self):
        def save():
            try:
                with open(self.path, 'w', encoding='utf-8') as f:
                    json.dump(self.servers, f, ensure_ascii=False, indent=4)
                    return True
            except:
                return False

        with self._lock:
            for i in range(2):
                if save():
                    return

    def get_config(self, serverid):
        conf = self.servers.get(serverid, None)
        if conf is None:
            conf = {}
            self.servers[serverid] = conf

        return conf

    def get_join(self, serverid):
        config = self.get_config(serverid)
        return config.get('join', None)

    def get_leave(self, serverid):
        config = self.get_config(serverid)
        return config.get('leave', None)

    def get_colors(self, serverid):
        config = self.servers.get(serverid, {})
        if serverid not in self.servers:
            self.servers[serverid] = config

        colors = config.get('colors', {})
        if 'colors' not in config:
            config['colors'] = colors

        return colors

    def get_mute_whitelist(self, serverid):
        config = self.get_config(serverid)
        if 'unmutable' not in config:
            config['unmutable'] = []

        return config.get('unmutable', [])

    def set_muted_role(self, serverid, roleid):
        conf = self.get_config(serverid)
        conf['muted_role'] = roleid

        self.save_json()

    def remove_removed_colors(self, serverid):
        server = self.bot.get_server(serverid)
        roles = server.roles
        colors = self.get_colors(serverid)

        removed = []

        for key, color in list(colors.items()):
            role = discord.utils.find(lambda r: r.id == color, roles)
            if role is None:
                self.delete_color_from_json(key, serverid)
                removed.append(key)

        return removed


class Management:
    def __init__(self, bot):
        self.bot = bot
        self.utils = ManagementHandler(bot)
        self.bot.management = self.utils
        self._load_timeouts()

    def _load_timeouts(self):
        session = self.bot.get_session
        sql = 'SELECT * FROM `timeouts`'
        rows = session.execute(sql)
        for row in rows:
            try:
                print(datetime.utcnow(), row['expires_on'])
                time = row['expires_on'] - datetime.utcnow()
                print(time)
                call_later(self.untimeout, self.bot.loop, time.total_seconds(),
                           str(row['user']), str(row['server']))

            except:
                logger.exception('Could not untimeout %s' % row)

    async def on_message_delete(self, msg):
        if msg.author.bot or msg.channel.id == '336917918040326166':
            return

        conf = self.utils.get_config(msg.server.id).get('on_delete', None)
        if conf is None:
            return

        channel = msg.server.get_channel(conf['channel'])
        if channel is None:
            return

        message = self.utils.format_on_delete(msg, conf)
        message = split_string(message)
        for m in message:
            await self.bot.send_message(channel, m)

    async def on_message_edit(self, before, after):
        if before.author.bot or before.channel.id == '336917918040326166':
            return

        conf = self.utils.get_config(before.server.id).get('on_edit', None)
        if not conf:
            return

        channel = before.server.get_channel(conf['channel'])
        if channel is None:
            return

        message = self.utils.format_on_edit(before, after, conf)
        if message is None:
            return

        message = split_string(message, maxlen=1960)
        for m in message:
            await self.bot.send_message(channel, m)

    async def on_member_join(self, member):
        server = member.server

        server_config = self.utils.get_config(server.id)
        if server_config is None:
            return

        conf = server_config.get('join', None)
        if conf is None:
            return

        channel = server.get_channel(conf['channel'])
        if channel is None:
            return

        message = self.utils.format_join_leave(member, conf)

        await self.bot.send_message(channel, message)

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
                    await self.bot.add_roles(member, role[0])

        if server.id == '217677285442977792':
            await self.bot._wants_to_be_noticed(member, server, remove=False)

    async def on_member_remove(self, member):
        server = member.server
        conf = self.utils.get_leave(server.id)
        if conf is None:
            return

        channel = server.get_channel(conf['channel'])
        if channel is None:
            return

        d = slots2dict(member)
        d.pop('user', None)
        message = conf['message'].format(user=str(member), **d)
        await self.bot.send_message(channel, message)

    @command(pass_context=True)
    async def leave_message(self, ctx, channel, message=None):
        if channel.lower() == 'off':
            self.utils.get_config(ctx.message.server.id).pop('leave', None)
            await self.bot.say('Removed on leave config')
            return

        if message is None:
            return await self.bot.say('You need to specify a message')

        old = self.utils.get_config(ctx.message.server.id).get('leave', {})
        conf = await self.utils.join_leave(ctx, channel, message, False, join=False)
        if not isinstance(conf, dict):
            return

        try:
            self.utils.format_on_delete(ctx.message, conf['leave'])
        except Exception as e:
            conf['leave'] = old
            self.utils.save_json()
            await self.bot.say('New format failed with error "{}"\n'
                               'reverting back'.format(e))

    @command(pass_context=True)
    async def join_message(self, ctx, channel, message=None, add_color=False):
        if channel.lower() == 'off':
            self.utils.get_config(ctx.message.server.id).pop('join', None)
            await self.bot.say('Removed on join config')
            return

        if not message:
            return await self.bot.say('You need to specify a message')

        old = self.utils.get_config(ctx.message.server.id).get('join', {})
        conf = await self.utils.join_leave(ctx, channel, message, add_color,  join=True)
        if not isinstance(conf, dict):
            return

        try:
            self.utils.format_on_delete(ctx.message, conf['join'])
        except Exception as e:
            conf['join'] = old
            self.utils.save_json()
            await self.bot.say('New format failed with error "{}"\n'
                               'reverting back'.format(e))

    @command(pass_context=True)
    async def on_edit_message(self, ctx, channel, *, message):
        user = ctx.message.author
        channel_ = ctx.message.channel
        if not user.permissions_in(channel_).manage_channels:
            return await self.bot.send_message(channel_, "You don't have manage channel permissions")

        if channel.lower() == 'off':
            self.utils.get_config(ctx.message.server.id).pop('on_edit', None)
            await self.bot.say('Removed on message edit config')
            return

        old = self.utils.get_config(ctx.message.server.id).get('on_edit', {})
        conf = await self.utils.message_edited(ctx, channel, message)
        if not isinstance(conf, dict):
            return

        try:
            self.utils.format_on_edit(ctx.message, ctx.message, conf['on_edit'], False)
        except Exception as e:
            conf['on_edit'] = old
            self.utils.save_json()
            await self.bot.say('New format failed with error "{}"\n'
                               'reverting back'.format(e))

    @command(pass_context=True)
    async def on_delete_message(self, ctx, channel, message):
        user = ctx.message.author
        channel_ = ctx.message.channel
        if not user.permissions_in(channel_).manage_channels:
            return await self.bot.send_message(channel_, "You don't have manage channel permissions")

        if channel.lower() == 'off':
            self.utils.get_config(ctx.message.server.id).pop('on_delete', None)
            await self.bot.say('Removed on message delete config')
            return

        old = self.utils.get_config(ctx.message.server.id).get('on_delete', {})
        conf = await self.utils.message_edited(ctx, channel, message, key='on_delete')

        if not isinstance(conf, dict):
            return

        try:
            self.utils.format_on_delete(ctx.message, conf['on_delete'])
        except Exception as e:
            conf['on_delete'] = old
            self.utils.save_json()
            await self.bot.say('New format failed with error "{}"\n'
                               'reverting back'.format(e))

    @command(pass_context=True, owner_only=True)
    async def delete_color(self, ctx, *, name):
        server = ctx.message.server
        colors = self.utils.get_colors(server.id)
        color = colors.get(name, None)
        if not color:
            return await self.bot.say('Color %s not found' % name)

        v = colors.values()
        role = discord.utils.find(lambda r: r.id == color, server.roles)
        list(filter(lambda r: r.id in v, ctx.message.server.roles))

        if not role:
            return await self.bot.say('Color %s not found' % name)

        try:
            await self.bot.delete_role(server, role)
        except Exception as e:
            await self.bot.say('Could not delete color %s\n%s' % (name, e))
            return

        self.utils.delete_color_from_json(name.lower(), server.id)
        await self.bot.say('Deleted color %s' % name)

    @command(pass_context=True, owner_only=True)
    async def mute_whitelist(self, ctx, *roles):
        role_mentions = ctx.message.role_mentions.copy()

        server_roles = ctx.message.server.roles
        role_mentions.extend(self.utils.get_roles_from_ids(server_roles, *roles))

        if not role_mentions:
            return await self.bot.say(
                'Use the role ids or mention roles to add them to the whitelist')

        conf = self.utils.get_mute_whitelist(ctx.message.server.id)
        for role in role_mentions:
            if role.id not in conf:
                conf.append(role.id)

        self.utils.save_json()
        role_mentions = list(map(lambda r: r.name, role_mentions))
        await self.bot.say('Roles {} added to the whitelist'.format(', '.join(role_mentions)))

    @command(pass_context=True, owner_only=True)
    async def muted_role(self, ctx, *roles):
        server = ctx.message.server
        role_mentions = ctx.message.role_mentions
        role_mentions.extend(self.utils.get_roles_from_ids(server.roles, *roles))
        if not role_mentions:
            return await self.bot.say('No role/role id specified')

        whitelist = self.utils.get_mute_whitelist(server.id)
        role = role_mentions[0]
        if role.id in whitelist:
            return await self.bot.say('Role is already in the mute whitelist. '
                                      'Remove it from there first using !remove_mute_whitelist')

        self.utils.set_muted_role(server.id, role.id)
        await self.bot.say('Muted role set to {0.name}: {0.id}'.format(role))

    async def _mute_check(self, ctx, *user):
        server = ctx.message.server
        mute_role = self.utils.get_config(server.id).get('muted_role', None)
        if mute_role is None:
            await self.bot.say('No mute role set')
            return False

        users = ctx.message.mentions.copy()
        users.extend(self.utils.get_users_from_ids(server, *user))

        if not users:
            await self.bot.say('No user ids or mentions')
            return False

        mute_role = discord.utils.find(lambda r: r.id == str(mute_role), server.roles)
        if mute_role is None:
            await self.bot.say('Could not find the muted role')
            return False

        return users, mute_role

    @command(pass_context=True, owner_only=True)
    async def mute(self, ctx, user, *reason):
        retval = await self._mute_check(ctx, user)
        if isinstance(retval, tuple):
            users, mute_role = retval
        else:
            return

        try:
            server = ctx.message.server
            user = users[0]
            await self.bot.add_roles(user, mute_role)
            await self.bot.say('Muted user {} `{}`'.format(user.name, user.id))
            chn = server.get_channel(self.bot.server_cache.get_modlog(server.id))
            if chn:
                author = ctx.message.author
                description = '{} muted {} {}'.format(author.mention, user, user.id)
                embed = discord.Embed(title='🤐 Moderation action [MUTE]',
                                      timestamp=datetime.utcnow(),
                                      description=description)
                reason = ' '.join(reason) if reason else 'No reason <:HYPERKINGCRIMSONANGRY:334717902962032640>'
                embed.add_field(name='Reason', value=reason)
                embed.set_thumbnail(url=user.avatar_url or user.default_avatar_url)
                embed.set_footer(text=str(author), icon_url=author.avatar_url or author.default_avatar_url)
                await self.bot.send_message(chn, embed=embed)
        except:
            await self.bot.say('Could not mute user {}'.format(str(users[0])))

    async def untimeout(self, user, server):
        mute_role = self.utils.get_config(server).get('muted_role', None)
        if mute_role is None:
            return

        server = self.bot.get_server(server)
        user = server.get_member(user)
        if not user:
            return

        if discord.utils.find(lambda r: r.id == mute_role, user.roles):
            try:
                await self.bot.replace_role(user, user.roles,
                                            [r for r in user.roles if r.id != mute_role])
            except:
                logger.exception('Could not autounmute user %s' % user.id)

        try:
            session = self.bot.get_session
            sql = 'DELETE FROM `timeouts` WHERE `server` = %s AND `user` = %s' % (server.id, user.id)
            session.execute(sql)
            session.commit()
        except:
            logger.exception('Could not delete untimeout')

    @command(pass_context=True, aliases=['temp_mute'])
    async def timeout(self, ctx, user, *, timeout):
        """Mute user for a specified amount of time
         `timeout` is the duration of the mute.
         The format is `n d|days` `n h|hours` `n m|minutes` `n s|seconds`
         where at least one of them must be provided.
         Maximum length for a timeout is 30 days
        """
        # Hardcoded whitelist for now
        # Tem, QT, Honk, s0hvaperuna
        if ctx.message.author.id != '123050803752730624':
            return await self.bot.say('Under construction')
        whitelist = ['266236554572333058', '216903801582518273', '218753123659808768', '123050803752730624']
        if ctx.message.author.id not in whitelist:
            return await self.bot.say("You aren't whitelisted")

        retval = await self._mute_check(ctx, user)
        if isinstance(retval, tuple):
            users, mute_role = retval
        else:
            return

        time, reason = parse_timeout(timeout)
        if not time:
            return await self.bot.say('Invalid time string')

        if time.days > 30:
            return await self.bot.say("Timeout can't be longer than 30 days")

        now = datetime.utcnow()
        expires_on = datetime2sql(now + time)
        user = users[0]
        session = self.bot.get_session
        try:
            sql = 'INSERT INTO `timeouts` (`server`, `user`, `expires_on`) VALUES ' \
                  '(:server, :user, :expires_on) ON DUPLICATE KEY UPDATE expires_on=expires_on'

            d = {'server': ctx.message.server.id, 'user': user.id, 'expires_on': expires_on}
            session.execute(sql, params=d)
            session.commit()
        except:
            logger.exception('Could not save timeout')
            return await self.bot.say('Could not save timeout. Canceling action')

        server = ctx.message.server
        try:
            await self.bot.add_roles(user, mute_role)
            await self.bot.say('Muted user {} for {}'.format(str(user), time))
            chn = server.get_channel(self.bot.server_cache.get_modlog(server.id))
            if chn:
                author = ctx.message.author
                description = '{} muted {} `{}` for {}'.format(author.mention,
                                                               user, user.id, time)

                embed = discord.Embed(title='🕓 Moderation action [TIMEOUT]',
                                      timestamp=datetime.utcnow(),
                                      description=description)
                reason = reason if reason else 'No reason <:HYPERKINGCRIMSONANGRY:334717902962032640>'
                embed.add_field(name='Reason', value=reason)
                embed.set_thumbnail(url=user.avatar_url or user.default_avatar_url)
                embed.set_footer(text=str(author), icon_url=author.avatar_url or author.default_avatar_url)

                await self.bot.send_message(chn, embed=embed)
        except:
            await self.bot.say('Could not mute user {}'.format(str(users[0])))

        call_later(self.untimeout, self.bot.loop,
                   time.total_seconds(), user.id, ctx.message.server.id)

    @command(pass_context=True, owner_only=True)
    async def unmute(self, ctx, *user):
        server = ctx.message.server
        mute_role = self.utils.get_config(server.id).get('muted_role', None)
        if mute_role is None:
            return await self.bot.say('No mute role set')

        users = ctx.message.mentions.copy()
        users.extend(self.utils.get_users_from_ids(server, *user))

        if not users:
            return await self.bot.say('No user ids or mentions')

        mute_role = discord.utils.find(lambda r: r.id == str(mute_role), server.roles)
        if mute_role is None:
            return await self.bot.say('Could not find the muted role')

        try:
            await self.bot.remove_roles(users[0], mute_role, remove_manually=False)
            await self.bot.say('Unmuted user {}'.format(users[0].name))
        except:
            await self.bot.say('Could not unmute user {}'.format(users[0].name))

    @command(pass_context=True, owner_only=True)
    async def remove_mute_whitelist(self, ctx, *roles):
        role_mentions = ctx.message.role_mentions.copy()

        server_roles = ctx.message.server.roles
        role_mentions.extend(self.utils.get_roles_from_ids(server_roles, *roles))

        if not role_mentions:
            return await self.bot.say(
                'Use the role ids or mention roles to remove them from the whitelist')

        conf = self.utils.get_mute_whitelist(ctx.message.server.id)
        removed = []
        for role in role_mentions:
            try:
                conf.remove(role.id)
            except ValueError:
                pass
            else:
                removed.append('```{0.name}```'.format(role))

        self.utils.save_json()
        await self.bot.say('Roles {} removed from the whitelist'.format(', '.join(removed)))

    @command(pass_context=True, ignore_extra=True)
    async def show_colors(self, ctx, color: str=None):
        embed = discord.Embed(title='Colors')
        colors = self.utils.get_colors(ctx.message.server.id)
        if color is not None:
            key = discord.utils.find(lambda key: key.lower() == color.lower(), colors)
            if key is not None:
                colors = {key: colors[key]}
            else:
                colors = None

        if not colors:
            return await self.bot.say('No colors found')

        for name, role in colors.items():
            embed.add_field(name=name, value='<@&%s>' % role)

        await self.bot.send_message(ctx.message.channel, embed=embed)

    @command(pass_context=True, owner_only=True)
    async def add_color(self, ctx, color, *, name):
        try:
            color = Color(color)
        except (ValueError, AttributeError):
            return await self.bot.say('Color %s is invalid' % color)

        try:
            color = color.get_hex_l()
            if color.startswith('#'):
                color = color[1:]

            color = discord.Color(int(color, 16))
            everyone = ctx.message.server.default_role
            perms = discord.Permissions(everyone.permissions.value)
            role = await self.bot.create_role(ctx.message.server, name=name,
                                              colour=color,
                                              permissions=perms,
                                              mentionable=False, hoist=False)
        except Exception as e:
            print('[ERROR] Exception while creating role. %s' % e)
            return await self.bot.say('Could not create role')

        self.utils.add_color_to_json(name.lower(), ctx.message.server.id, role.id)
        await self.bot.say('Color %s added' % name)

    @command(pass_context=True, owner_only=True)
    async def test2(self, ctx, mention, role1, role2, replaced):
        mention = ctx.message.server.get_member(mention)
        await self.bot.replace_role(mention, (role1, role2), (replaced, ))

    @command(pass_context=True, aliases=['colour'])
    async def color(self, ctx, *, color):
        server = ctx.message.server
        roles = server.roles
        color = color.lower()
        colors = self.utils.get_colors(server.id)
        color_id = colors.get(color, None)
        role_ = list(filter(lambda r: r.id == color_id, roles))
        if not role_:
            return await self.bot.say('Color %s not found. Use !colors for all the available colors.' % color)

        role_ = role_[0]
        _roles = []
        for role in ctx.message.author.roles:
            v = colors.values()
            if role.id in v and role != role_:
                _roles.append(role)

        try:
            await self.bot.replace_role(ctx.message.author, _roles, (role_,))
        except Exception as e:
            print(e)
            await self.bot.say('Failed to change color')
        else:
            await self.bot.say('Color set to %s' % color)

    @command(pass_context=True)
    async def colors(self, ctx):
        await self.bot.say('Available colors: {}'.format(', '.join(self.utils.get_colors(ctx.message.server.id).keys())))

    @command(pass_context=True, owner_only=True)
    async def check_colors(self, ctx):
        server = ctx.message.server
        removed = self.utils.remove_removed_colors(server.id)
        if removed:
            await self.bot.say('removed colors {}'.format(', '.join(removed)))
        else:
            await  self.bot.say('No colors to remove')

    @command(pass_context=True, owner_only=True)
    async def color_uncolored(self, ctx):
        server = ctx.message.server
        removed = self.utils.remove_removed_colors(server.id)
        if removed:
            await self.bot.say('Removed colors without role. {}'.format(', '.join(removed)))

        colors = self.utils.get_colors(server.id)
        color_ids = list(colors.values())
        if not colors:
            return

        roles = server.roles
        colored = 0
        for member in list(server.members):
            m_roles = member.roles
            found = list(filter(lambda r: r.id in color_ids, m_roles))
            if not found:
                color = choice(color_ids)
                role = list(filter(lambda r: r.id == color, roles))
                try:
                    await self.bot.add_roles(member, *role)
                    colored += 1
                except Exception:
                    pass
            elif len(found) > 1:
                try:
                    await self.bot.replace_role(member, color_ids, (found[0],))
                    colored += 1
                except Exception:
                    pass

        await self.bot.say('Colored %s users without color role' % colored)

    @command(name='roles', pass_context=True, ignore_extra=True)
    @cooldown(1, 5, BucketType.server)
    async def get_roles(self, ctx, page=''):
        server_roles = sorted(ctx.message.server.roles, key=lambda r: r.name)
        print_all = page.lower() == 'all'
        idx = 0
        if print_all and ctx.message.author.id != self.bot.owner:
            return await self.bot.say('Only the owner can use the all modifier', delete_after=30)
        elif page and not print_all:
            try:
                idx = int(page) - 1
                if idx < 0:
                    return await self.bot.say('Index must be bigger than 0')
            except ValueError:
                return await self.bot.say('%s is not a valid integer' % page, delete_after=30)

        maxlen = 1950
        roles = 'A total of %s roles\n' % len(server_roles)
        for role in server_roles:
            roles += '{}: {}\n'.format(role.name, role.mention)

        roles = split_string(roles, splitter='\n', maxlen=maxlen)
        if not print_all:
            try:
                roles = (roles[idx],)
            except IndexError:
                return await self.bot.say('Page index %s is out of bounds' % idx, delete_after=30)

        for s in roles:
            await self.bot.say('```' + s + '```')

    @command(owner_only=True)
    async def reload_config(self):
        self.utils.reload_config()
        await self.bot.say('Reloaded config')


def setup(bot):
    bot.add_cog(Management(bot))