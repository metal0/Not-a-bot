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

from discord.ext.commands import BucketType

from bot.bot import command, cooldown
from cogs.cog import Cog


class Hearthstone(Cog):
    def __init__(self, bot, mashape_key, client):
        super().__init__(bot)
        self.key = mashape_key
        self.client = client

    @command()
    @cooldown(1, 5, type=BucketType.user)
    async def hs(self, ctx, *, name):
        """Search for a hearthstone card"""
        headers = {'content-type': 'application/json', 'X-Mashape-Key': self.key}
        async with self.client.get('https://omgvamp-hearthstone-v1.p.mashape.com/cards/search/%s' % name,
                                   headers=headers) as r:
            if r.status == 200:
                js = await r.json()
                imgs = ''
                for j in js[:10]:
                    try:
                        j['collectible']
                    except KeyError:
                        continue

                    imgs += j['img'] + ' '

                if imgs:
                    return await ctx.send(imgs)

            await ctx.send('No matches')


def setup(bot):
    bot.add_cog(Hearthstone(bot, bot.config.mashape_key, bot.aiohttp_client))
