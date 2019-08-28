import logging
from datetime import datetime

import discord
from asyncpg.exceptions import PostgresError
from discord.ext.commands import BucketType

from bot.bot import command, cooldown, bot_has_permissions
from bot.converters import AnyUser, CommandConverter
from cogs.cog import Cog
from utils.utilities import send_paged_message, format_timedelta

logger = logging.getLogger('debug')
terminal = logging.getLogger('terminal')


class Stats(Cog):
    def __init__(self, bot):
        super().__init__(bot)

    @command(no_pm=True)
    @cooldown(2, 5, type=BucketType.guild)
    @bot_has_permissions(embed_links=True)
    async def mention_stats(self, ctx, page=None):
        """
        Get stats on how many times which roles are mentioned on this server
        Only counts mentions in channels the bot can see
        Also matches all role mentions not just those that ping"""
        guild = ctx.guild

        if page is not None:
            try:
                # No one probably hasn't created this many roles
                if len(page) > 3:
                    return await ctx.send('Page out of range')

                page = int(page)
                if page <= 0:
                    page = 1
            except ValueError:
                page = 1
        else:
            page = 1

        sql = 'SELECT * FROM mention_stats WHERE guild={} ORDER BY amount DESC LIMIT {}'.format(guild.id, 10*page)
        rows = await self.bot.dbutil.fetch(sql)
        if not rows:
            return await ctx.send('No role mentions logged on this server')

        embed = discord.Embed(title='Most mentioned roles in server {}'.format(guild.name))
        added = 0
        p = page*10
        for idx, row in enumerate(rows[p-10:p]):
            added += 1
            role = guild.get_role(row['role'])
            if role:
                role_name, role = role.name, role.id
            else:
                role_name, role = row['role_name'], row['role']

            embed.add_field(name='{}. {}'.format(idx + p-9, role),
                            value='<@&{}>\n{}\nwith {} mentions'.format(role, role_name, row['amount']))

        if added == 0:
            return await ctx.send('Page out of range')

        await ctx.send(embed=embed)

    @command(aliases=['seen'])
    @cooldown(1, 5, BucketType.user)
    async def last_seen(self, ctx, *, user: AnyUser):
        """Get when a user was last seen on this server and elsewhere
        User can be a mention, user id, or full discord username with discrim Username#0001"""

        if isinstance(user, discord.User):
            user_id = user.id
            username = str(user)
        elif isinstance(user, int):
            user_id = user
            username = None
        else:
            user_id = None
            username = user

        if user_id:
            user_clause = 'uid=$1'
        else:
            user_clause = 'username=$1'
            # We need to replace this so the correct type is given
            user_id = username

        guild = ctx.guild
        if guild is not None:
            guild = guild.id
            sql = 'SELECT seen.* FROM last_seen_users seen WHERE guild=$2 AND {0} ' \
                  'UNION ALL (SELECT  seen2.* FROM last_seen_users seen2 WHERE seen2.guild!=$2 AND seen2.{0} ORDER BY seen2.last_seen DESC LIMIT 1)'.format(user_clause)
        else:
            guild = 0
            sql = 'SELECT * FROM last_seen_users WHERE guild=$2 AND %s' % user_clause

        try:
            rows = await self.bot.dbutil.fetch(sql, (user_id, guild))
        except PostgresError:
            terminal.exception('Failed to get last seen from db')
            return await ctx.send('Failed to get user because of an error')

        if len(rows) == 0:
            return await ctx.send("No users found with {}. Either the bot hasn't had the chance to log activity or the name was wrong."
                                  "Names are case sensitive and must include the discrim".format(username))
        local = None
        global_ = None

        for row in rows:
            if not guild or row['guild'] != guild:
                global_ = row

            else:
                local = row

        if user_id is None:
            if local:
                user_id = local['uid']
            else:
                user_id = global_['uid']

        if username is None:
            if local:
                username = local['username']
            elif global_:
                username = global_['username']

        msg = 'User {} `{}`\n'.format(username, user_id)
        if local:
            time = local['last_seen']
            fmt = format_timedelta(datetime.utcnow() - time, accuracy=2)
            msg += 'Last seen on this server `{} UTC` {} ago\n'.format(time, fmt)
        if global_:
            time = global_['last_seen']
            fmt = format_timedelta(datetime.utcnow() - time, accuracy=2)
            msg += 'Last seen elsewhere `{} UTC` {} ago'.format(time, fmt)

        await ctx.send(msg)

    @command(aliases=['cmdstats'])
    @cooldown(1, 5, BucketType.guild)
    async def command_stats(self, ctx, cmd: CommandConverter=None):
        """
        Get command usage statistics. If command is provided only get the stats
        for that command
        """
        if cmd:
            cmd = cmd.qualified_name.split(' ')
            parent = cmd[0]
            name = ' '.join(cmd[1:])
        else:
            parent = None
            name = None

        cmds = await self.bot.dbutil.get_command_stats(parent, name)
        if not cmds:
            return await ctx.send('Failed to get command stats')

        pages = list(cmds)
        size = 15
        pages = [pages[i:i+size] for i in range(0, len(pages), size)]

        def get_page(page, idx):
            if isinstance(page, discord.Embed):
                return page

            desc = ''
            for r in page:
                desc += f'`{r["parent"]}'
                name = r['cmd']
                if name:
                    desc += f' {name}'

                desc += f'` {r["uses"]} uses\n'

            embed = discord.Embed(title='Command usage stats', description=desc)
            embed.set_footer(text=f'{idx+1}/{len(pages)}')
            pages[idx] = embed
            return embed

        await send_paged_message(ctx, pages, embed=True, page_method=get_page)


def setup(bot):
    bot.add_cog(Stats(bot))
