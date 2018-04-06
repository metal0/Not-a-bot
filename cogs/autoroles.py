import logging
from random import choice

import discord
from utils.utilities import Snowflake
from cogs.cog import Cog

logger = logging.getLogger('debug')


class AutoRoles(Cog):
    def __init__(self, bot):
        super().__init__(bot)

    @property
    def dbutil(self):
        return self.bot.dbutil

    async def on_message(self, message):
        if self.bot.test_mode:
            return

        # Autogrant @every
        if message.guild and message.guild.id == 217677285442977792 and message.author.id != 123050803752730624:
            if discord.utils.find(lambda r: r.id == 323098643030736919, message.role_mentions):
                if not discord.utils.get(message.author.roles, id=323098643030736919):
                    await message.author.add_roles(Snowflake(323098643030736919), reason='Pinged every')

    async def on_member_update(self, before, after):
        if self.bot.test_mode:
            return

        guild = after.guild
        if guild.id == 217677285442977792:
            name = before.name if not before.nick else before.nick
            name2 = after.name if not after.nick else after.nick
            if name != name2:
                await self.bot._wants_to_be_noticed(after, guild)

        if self.bot.guild_cache.keeproles(guild.id):
            removed, added = self.compare_roles(before, after)
            if removed:
                self.dbutil.remove_user_roles(removed, before.id)

            if added:
                self.dbutil.add_user_roles(added, before.id, guild.id)

    async def add_random_color(self, member):
        if self.bot.guild_cache.random_color(member.guild.id) and hasattr(self.bot, 'colors'):
            colors = self.bot.colors.get(member.guild.id, {}).values()
            color_ids = {r.role_id for r in colors}
            if not color_ids:
                return

            if {r.id for r in list(member.roles)}.intersection(color_ids):
                return
            await member.add_roles(Snowflake(id=choice(list(color_ids))), reason='Automatic coloring')

    async def on_member_join(self, member):
        guild = member.guild

        bot_member = guild.get_member(self.bot.user.id)
        perms = bot_member.guild_permissions

        # If bot doesn't have manage roles no use in trying to add roles
        if not perms.administrator and not perms.manage_roles:
            return

        roles = set()
        if self.bot.guild_cache.keeproles(guild.id):
            sql = 'SELECT roles.id FROM `users` LEFT OUTER JOIN `userRoles` ON users.id=userRoles.user LEFT OUTER JOIN `roles` ON roles.id=userRoles.role ' \
                  'WHERE roles.guild=%s AND users.id=%s' % (guild.id, member.id)

            session = self.bot.get_session
            roles = {r['id'] for r in session.execute(sql).fetchall()}
            if not roles:
                return await self.add_random_color(member)

            roles.discard(guild.default_role.id)

            muted_role = self.bot.guild_cache.mute_role(guild.id)
            if muted_role in roles:
                try:
                    await member.add_roles(Snowflake(muted_role), reason='[Keeproles] add muted role first')
                    roles.discard(muted_role)
                except discord.HTTPException:
                    logger.exception('[KeepRoles] Failed to add muted role first')

        if self.bot.guild_cache.random_color(guild.id) and hasattr(self.bot, 'colors'):
            if not roles:
                await self.add_random_color(member)
            else:
                colors = self.bot.colors.get(guild.id, {}).values()
                color_ids = {i.role_id for i in colors}
                if not color_ids.intersection(roles):
                    roles.add(choice(list(color_ids)))

        if not roles:
            return

        roles = [Snowflake(r) for r in roles]

        try:
            await member.add_roles(*roles, atomic=False, reason='Keeproles')
        except discord.HTTPException:
            for role in roles:
                try:
                    await member.add_roles(role, reason='Keeproles')
                except discord.errors.Forbidden:
                    pass
                except:
                    logger.exception('Failed to give role on join')

        if guild.id == 217677285442977792:
            await self.bot._wants_to_be_noticed(member, guild)

    async def on_member_remove(self, member):
        if not self.bot.guild_cache.keeproles(member.guild.id):
            return

        roles = [r.id for r in member.roles]
        roles.remove(member.guild.default_role.id)
        self.dbutil.delete_user_roles(member.guild.id, member.id)

        if roles:
            self.dbutil.add_user_roles(roles, member.id, member.guild.id)
            logger.debug('{}/{} saved roles {}'.format(member.guild.id, member.id, ', '.join(roles)))

    @staticmethod
    def compare_roles(before, after):
        default_role = before.guild.default_role.id
        before = set(map(lambda r: r.id, before.roles))
        after = set(map(lambda r: r.id, after.roles))
        removed = before.difference(after)
        added = after.difference(before)

        # No need to keep the default role
        removed.discard(default_role)
        added.discard(default_role)

        return removed, added


def setup(bot):
    bot.add_cog(AutoRoles(bot))
