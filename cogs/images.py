import base64
import logging
import os
import time
from asyncio import Lock
from functools import partial
from io import BytesIO
from random import randint
from typing import Optional

from PIL import (Image, ImageSequence, ImageFont, ImageDraw, ImageChops,
                 GifImagePlugin)
from bs4 import BeautifulSoup
from discord import File
from discord.ext.commands import BucketType, BotMissingPermissions
from discord.ext.commands.errors import BadArgument
from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.common.exceptions import WebDriverException
from selenium.webdriver import Chrome
from selenium.webdriver.chrome.options import Options

from bot.bot import command, cooldown
from bot.converters import CleanContent
from bot.exceptions import NoPokeFoundException, BotException
from cogs.cog import Cog
from utils.imagetools import (resize_keep_aspect_ratio, gradient_flash, sepia,
                              optimize_gif, func_to_gif,
                              get_duration, convert_frames, apply_transparency)
from utils.utilities import (get_image_from_ctx, find_coeffs, check_botperm,
                             split_string, get_image, dl_image)

logger = logging.getLogger('debug')
terminal = logging.getLogger('terminal')
TEMPLATES = os.path.join('data', 'templates')


class Pokefusion:
    RANDOM = '%'

    def __init__(self, client, bot):
        self._last_dex_number = 0
        self._pokemon = {}
        self._poke_reverse = {}
        self._last_updated = 0
        self._client = client
        self._data_folder = os.path.join(os.getcwd(), 'data', 'pokefusion')
        self._driver_lock = Lock(loop=bot.loop)
        self._bot = bot
        self._update_lock = Lock(loop=bot.loop)

        p = self.bot.config.chromedriver
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        binary = self.bot.config.chrome
        if binary:
            options.binary_location = binary

        self.driver = Chrome(p, chrome_options=options)

    @property
    def bot(self):
        return self._bot

    @property
    def last_dex_number(self):
        return self._last_dex_number

    @property
    def client(self):
        return self._client

    def is_dex_number(self, s):
        # No need to convert when the number is that big
        if len(s) > 5:
            return False
        try:
            return int(s) <= self.last_dex_number
        except ValueError:
            return False

    async def cache_types(self, start=1):
        name = 'sprPKMType_{}.png'
        url = 'http://pokefusion.japeal.com/sprPKMType_{}.png'
        while True:
            r = await self.client.get(url.format(start))
            if r.status == 404:
                r.close()
                break

            with open(os.path.join(self._data_folder, name.format(start)), 'wb') as f:
                f.write(await r.read())

            start += 1

    async def update_cache(self):
        if self._update_lock.locked():
            # If and update is in progress wait for it to finish and then continue
            await self._update_lock.acquire()
            self._update_lock.release()
            return

        await self._update_lock.acquire()
        success = False
        try:
            logger.info('Updating pokecache')
            r = await self.client.get('http://pokefusion.japeal.com/PKMSelectorV3.php')
            soup = BeautifulSoup(await r.text(), 'lxml')
            selector = soup.find(id='s1')
            if selector is None:
                logger.debug('Failed to update pokefusion cache')
                return False

            pokemon = selector.find_all('option')
            for idx, p in enumerate(pokemon[1:]):
                name = ' #'.join(p.text.split(' #')[:-1])
                self._pokemon[name.lower()] = idx + 1
                self._poke_reverse[idx + 1] = name.lower()

            self._last_dex_number = len(pokemon) - 1
            types = filter(lambda f: f.startswith('sprPKMType_'), os.listdir(self._data_folder))
            await self.cache_types(start=max(len(list(types)), 1))
            self._last_updated = time.time()
            success = True
        except:
            logger.exception('Failed to update pokefusion cache')
        finally:
            self._update_lock.release()
            return success

    def get_by_name(self, name):
        poke = self._pokemon.get(name.lower())
        if poke is None:
            for poke_, v in self._pokemon.items():
                if name in poke_:
                    return v
        return poke

    def get_by_dex_n(self, n: int):
        return n if n <= self.last_dex_number else None

    def get_pokemon(self, name):
        if name == self.RANDOM and self.last_dex_number > 0:
            return randint(1, self._last_dex_number)
        if self.is_dex_number(name):
            return int(name)
        else:
            return self.get_by_name(name)

    async def get_url(self, url):
        # Attempt at making phantomjs async friendly
        # After visiting the url remember to put 1 item in self.queue
        # Otherwise the browser will be locked

        # If lock is not locked lock it until this operation finishes
        unlock = False
        if not self._driver_lock.locked():
            await self._driver_lock.acquire()
            unlock = True

        f = partial(self.driver.get, url)
        await self.bot.loop.run_in_executor(self.bot.threadpool, f)
        if unlock:
            try:
                self._driver_lock.release()
            except RuntimeError:
                pass

    async def fuse(self, poke1=RANDOM, poke2=RANDOM, poke3=None):
        # Update cache once per day
        if time.time() - self._last_updated > 86400:
            if not await self.update_cache():
                raise BotException('Could not cache pokemon')

        dex_n = []
        for p in (poke1, poke2):
            poke = self.get_pokemon(p)
            if poke is None:
                raise NoPokeFoundException(p)
            dex_n.append(poke)

        if poke3 is None:
            color = 0
        else:
            color = self.get_pokemon(poke3)
            if color is None:
                raise NoPokeFoundException(poke3)

        url = 'http://pokefusion.japeal.com/PKMColourV5.php?ver=3.2&p1={}&p2={}&c={}&e=noone'.format(*dex_n, color)
        async with self._driver_lock:
            try:
                await self.get_url(url)
            except UnexpectedAlertPresentException:
                self.driver.switch_to.alert.accept()
                raise BotException('Invalid pokemon given')

            data = self.driver.execute_script("return document.getElementById('image1').src")
            types = self.driver.execute_script("return document.querySelectorAll('*[width=\"30\"]')")
            name = self.driver.execute_script("return document.getElementsByTagName('b')[0].textContent")

        data = data.replace('data:image/png;base64,', '', 1)
        img = Image.open(BytesIO(base64.b64decode(data)))
        type_imgs = []

        for tp in types:
            file = tp.get_attribute('src').split('/')[-1].split('?')[0]
            try:
                im = Image.open(os.path.join(self._data_folder, file))
                type_imgs.append(im)
            except (FileNotFoundError, OSError):
                raise BotException('Error while getting type images')

        bg = Image.open(os.path.join(self._data_folder, 'poke_bg.png'))

        # Paste pokemon in the middle of the background
        x, y = (bg.width//2-img.width//2, bg.height//2-img.height//2)
        bg.paste(img, (x, y), img)

        w, h = type_imgs[0].size
        padding = 2
        # Total width of all type images combined with padding
        type_w = len(type_imgs) * (w + padding)
        width = bg.width
        start_x = (width - type_w)//2
        y = y + img.height

        for tp in type_imgs:
            bg.paste(tp, (start_x, y), tp)
            start_x += w + padding

        font = ImageFont.truetype(os.path.join('M-1c', 'mplus-1c-bold.ttf'), 36)
        draw = ImageDraw.Draw(bg)
        w, h = draw.textsize(name, font)
        draw.text(((bg.width-w)//2, bg.height//2-img.height//2 - h), name, font=font, fill='black')

        s = 'Fusion of {} and {}'.format(self._poke_reverse[dex_n[0]], self._poke_reverse[dex_n[1]])
        if color:
            s += ' using the color palette of {}'.format(self._poke_reverse[color])
        return bg, s


class Images(Cog):
    def __init__(self, bot):
        super().__init__(bot)
        self.threadpool = bot.threadpool
        try:
            self._pokefusion = Pokefusion(self.bot.aiohttp_client, bot)
        except WebDriverException:
            terminal.exception('failed to load pokefusion')
            self._pokefusion = None

    def cog_unload(self):
        if self._pokefusion:
            self._pokefusion.driver.quit()

    def cog_check(self, ctx):
        if not check_botperm('attach_files', ctx=ctx):
            raise BotMissingPermissions(('attach_files', ))

        return True

    async def image_func(self, func, *args, **kwargs):
        return await self.bot.loop.run_in_executor(self.bot.threadpool, func, *args, **kwargs)

    @staticmethod
    def save_image(img, format='PNG'):
        data = BytesIO()
        img.save(data, format)
        data.seek(0)
        return data

    @command()
    @cooldown(3, 5, type=BucketType.guild)
    async def anime_deaths(self, ctx, image=None):
        """Generate a top 10 anime deaths image based on provided image"""
        path = os.path.join(TEMPLATES, 'saddest-anime-deaths.png')
        img = await get_image(ctx, image)
        if img is None:
            return

        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            x, y = 9, 10
            w, h = 854, 480
            template = Image.open(path)
            img = resize_keep_aspect_ratio(img, (w, h), can_be_bigger=False, resample=Image.BILINEAR)
            new_w, new_h = img.width, img.height
            if new_w != w:
                x += int((w - new_w)/2)

            if new_h != h:
                y += int((h - new_h) / 2)

            img = img.convert("RGBA")
            template.paste(img, (x, y), img)
            return self.save_image(template)

        await ctx.send(file=File(await self.image_func(do_it), filename='top10-anime-deaths.png'))

    @command()
    @cooldown(3, 5, type=BucketType.guild)
    async def anime_deaths2(self, ctx, image=None):
        """same as anime_deaths but with a transparent bg"""
        path = os.path.join(TEMPLATES, 'saddest-anime-deaths2.png')
        img = await get_image(ctx, image)
        if img is None:
            return

        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            x, y = 9, 10
            w, h = 854, 480
            template = Image.open(path)
            img = resize_keep_aspect_ratio(img, (w, h), can_be_bigger=False, resample=Image.BILINEAR)
            new_w, new_h = img.width, img.height
            if new_w != w:
                x += int((w - new_w)/2)

            if new_h != h:
                y += int((h - new_h) / 2)

            img = img.convert("RGBA")
            template.paste(img, (x, y), img)
            return self.save_image(template)

        await ctx.send(file=File(await self.image_func(do_it), filename='top10-anime-deaths.png'))

    @command()
    @cooldown(3, 5, type=BucketType.guild)
    async def trap(self, ctx, image=None):
        """Is it a trap?
        """
        img = await get_image(ctx, image)
        if img is None:
            return

        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            path = os.path.join(TEMPLATES, 'is_it_a_trap.png')
            path2 = os.path.join(TEMPLATES, 'is_it_a_trap_layer.png')
            img = img.convert("RGBA")
            x, y = 820, 396
            w, h = 355, 505
            rotation = -22.5

            img = resize_keep_aspect_ratio(img, (w, h), can_be_bigger=False,
                                           resample=Image.BILINEAR)
            img = img.rotate(rotation, expand=True, resample=Image.BILINEAR)
            x_place = x - int(img.width / 2)
            y_place = y - int(img.height / 2)

            template = Image.open(path)

            template.paste(img, (x_place, y_place), img)
            layer = Image.open(path2)
            template.paste(layer, (0, 0), layer)
            return self.save_image(template)

        await ctx.send(file=File(await self.image_func(do_it), filename='is_it_a_trap.png'))

    @command(aliases=['jotaro_no'])
    @cooldown(3, 5, BucketType.guild)
    async def jotaro(self, ctx, image=None):
        """Jotaro wasn't pleased"""
        img = await get_image(ctx, image)
        if img is None:
            return
        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            # The size we want from the transformation
            width = 524
            height = 326
            d_x = 90
            w, h = img.size

            coeffs = find_coeffs(
                [(d_x, 0), (width - d_x, 0), (width, height), (0, height)],
                [(0, 0), (w, 0), (w, h), (0, h)])

            img = img.transform((width, height), Image.PERSPECTIVE, coeffs,
                                Image.BICUBIC)

            template = os.path.join(TEMPLATES, 'jotaro.png')
            template = Image.open(template)

            white = Image.new('RGBA', template.size, 'white')

            x, y = 9, 351
            white.paste(img, (x, y))
            white.paste(template, mask=template)

            return self.save_image(white)

        await ctx.send(file=File(await self.image_func(do_it), filename='jotaro_no.png'))

    @command(aliases=['jotaro2'])
    @cooldown(2, 5, BucketType.guild)
    async def jotaro_photo(self, ctx, image=None):
        """Jotaro takes an image and looks at it"""
        # Set to false because discord doesn't embed it correctly
        # Should be used if it can be embedded since the file size is much smaller
        use_webp = False

        img = await get_image(ctx, image)
        if img is None:
            return

        extension = 'webp' if use_webp else 'gif'
        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            r = 34.7
            x = 6
            y = -165
            width = 468
            height = 439
            duration = [120, 120, 120, 120, 120, 120, 120, 120, 120, 120, 120, 120,
                        80, 120, 120, 120, 120, 120, 30, 120, 120, 120, 120, 120,
                        120, 120, 760, 2000]  # Frame timing

            frames = [frame.copy().convert('RGBA') for frame in ImageSequence.Iterator(Image.open(os.path.join(TEMPLATES, 'jotaro_photo.gif')))]
            photo = os.path.join(TEMPLATES, 'photo.png')
            finger = os.path.join(TEMPLATES, 'finger.png')

            im = Image.open(photo)
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (width, height), resample=Image.BICUBIC,
                                           can_be_bigger=False, crop_to_size=True,
                                           center_cropped=True, background_color='black')
            w, h = img.size
            width, height = (472, 441)
            coeffs = find_coeffs(
                [(0, 0), (437, 0), (width, height), (0, height)],
                [(0, 0), (w, 0), (w, h), (0, h)])
            img = img.transform((width, height), Image.PERSPECTIVE, coeffs,
                                Image.BICUBIC)
            img = img.rotate(r, resample=Image.BICUBIC, expand=True)
            im.paste(img, box=(x, y), mask=img)
            finger = Image.open(finger)
            im.paste(finger, mask=finger)
            frames[-1] = im

            if use_webp:
                # We save room for some colors when not using the shadow in a gif
                shadow = os.path.join(TEMPLATES, 'photo.png')
                im.alpha_composite(shadow)
                kwargs = {}
            else:
                # Duration won't work in the save() params when using a gif so I have to do it this way
                frames[0].info['duration'] = duration
                kwargs = {'optimize': True}

            file = BytesIO()
            frames[0].save(file, format=extension, save_all=True, append_images=frames[1:], duration=duration, **kwargs)
            if file.tell() > 8000000:
                raise BotException('Generated image was too big in filesize')

            file.seek(0)
            return optimize_gif(file.getvalue())

        await ctx.send(file=File(await self.image_func(do_it), filename='jotaro_photo.{}'.format(extension)))

    @command(aliases=['jotaro3'])
    @cooldown(2, 5, BucketType.guild)
    async def jotaro_smile(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return
        await ctx.trigger_typing()

        def do_it():
            nonlocal img

            im = Image.open(os.path.join(TEMPLATES, 'jotaro_smile.png'))
            img = img.convert('RGBA')
            i = Image.new('RGBA', im.size, 'black')
            size = (337, 350)
            img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                           crop_to_size=True, center_cropped=True,
                                           resample=Image.BICUBIC)
            img = img.rotate(13.7, Image.BICUBIC, expand=True)
            x, y = (207, 490)
            i.paste(img, (x, y), mask=img)
            i.paste(im, mask=im)

            return self.save_image(i)

        await ctx.send(file=File(await self.image_func(do_it), filename='jotaro.png'))

    @command(aliases=['jotaro4'])
    @cooldown(2, 5, BucketType.guild)
    async def jotaro_photo2(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'jotaro_photo2.png'))
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (305, 440), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            img = img.rotate(5, Image.BICUBIC, expand=True)
            bg = Image.new('RGBA', template.size)

            bg.paste(img, (460, 841), img)
            bg.alpha_composite(template)
            return self.save_image(bg)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='jotaro_photo.png'))

    @command(aliases=['tbc'])
    @cooldown(2, 5, BucketType.guild)
    async def tobecontinued(self, ctx, image=None, no_sepia=False):
        """Make a to be continued picture
        Usage: {prefix}{name} `image/emote/mention` `[optional sepia filter off] on/off`
        Sepia filter is on by default
        """
        img = await get_image(ctx, image)
        if not img:
            return

        await ctx.trigger_typing()

        def do_it():
            nonlocal img
            if not no_sepia:
                img = sepia(img)

            width, height = img.width, img.height
            if width < 300:
                width = 300

            if height < 200:
                height = 200

            img = resize_keep_aspect_ratio(img, (width, height), resample=Image.BILINEAR)
            width, height = img.width, img.height
            tbc = Image.open(os.path.join(TEMPLATES, 'tbc.png'))
            x = int(width * 0.09)
            y = int(height * 0.90)
            tbc = resize_keep_aspect_ratio(tbc, (width * 0.5, height * 0.3),
                                           can_be_bigger=False, resample=Image.BILINEAR)

            if y + tbc.height > height:
                y = height - tbc.height - 10

            img.paste(tbc, (x, y), tbc)

            return self.save_image(img)

        await ctx.send(file=File(await self.image_func(do_it), filename='To_be_continued.png'))

    @command(aliases=['heaven', 'heavens_door'])
    @cooldown(2, 5, BucketType.guild)
    async def overheaven(self, ctx, image=None):
        img = await get_image(ctx, image)
        if not img:
            return
        await ctx.trigger_typing()

        def do_it():
            nonlocal img
            overlay = Image.open(os.path.join(TEMPLATES, 'heaven.png'))
            base = Image.open(os.path.join(TEMPLATES, 'heaven_base.png'))
            size = (750, 750)
            img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                           crop_to_size=True, center_cropped=True)

            img = img.convert('RGBA')
            x, y = (200, 160)
            base.paste(img, (x, y), mask=img)
            base.alpha_composite(overlay)
            return self.save_image(base)

        await ctx.send(file=File(await self.image_func(do_it), filename='overheaven.png'))

    @command(aliases=['puccireset'])
    @cooldown(2, 5, BucketType.guild)
    async def pucci(self, ctx, image=None):
        img = await get_image(ctx, image)
        if not img:
            return
        await ctx.trigger_typing()

        def do_it():
            nonlocal img
            img = img.convert('RGBA')
            im = Image.open(os.path.join(TEMPLATES, 'pucci_bg.png'))
            overlay = Image.open(os.path.join(TEMPLATES, 'pucci_faded.png'))
            size = (682, 399)
            img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                           crop_to_size=True, center_cropped=True)
            x, y = (0, 367)
            im.paste(img, (x, y), mask=img)
            im.alpha_composite(overlay)
            return self.save_image(im)

        await ctx.send(file=File(await self.image_func(do_it), filename='pucci_reset.png'))

    @command(aliases=['epitaph'])
    @cooldown(2, 5, BucketType.guild)
    async def doppio(self, ctx, image=None):
        """image of doppio"""
        img = await get_image(ctx, image)
        if not img:
            return
        await ctx.trigger_typing()

        def do_it():
            nonlocal img
            img = img.convert('RGBA')
            im = Image.open(os.path.join(TEMPLATES, 'doppio.png'))
            bg = Image.new('RGBA', im.size, 'black')

            x, y = (135, 196)
            width = 500
            height = 408
            if img.width > img.height:
                img = resize_keep_aspect_ratio(img, (None, height), resample=Image.BICUBIC)
                x = x + (width - img.width)//2
            else:
                img = resize_keep_aspect_ratio(img, (width, None), resample=Image.BICUBIC)
                y = y + (height - img.height)//2

            bg.paste(img, (x, y), mask=img)
            bg.alpha_composite(im)
            return self.save_image(bg)

        await ctx.send(file=File(await self.image_func(do_it), filename='epitaph.png'))

    @command()
    @cooldown(1, 10, BucketType.guild)
    async def party(self, ctx, image=None):
        """Takes a long ass time to make the gif"""
        img = await get_image(ctx, image)
        if img is None:
            return

        async with ctx.typing():
            img = await self.bot.loop.run_in_executor(self.threadpool, partial(gradient_flash, img, get_raw=True))
        await ctx.send(content=f"Use {ctx.prefix}party2 if transparency guess went wrong",
                       file=File(img, filename='party.gif'))

    @command()
    @cooldown(1, 10, BucketType.guild)
    async def party2(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        async with ctx.typing():
            img = await self.bot.loop.run_in_executor(self.threadpool, partial(gradient_flash, img, get_raw=True, transparency=False))
        await ctx.send(file=File(img, filename='party.gif'))

    @command()
    @cooldown(2, 2, type=BucketType.guild)
    async def blurple(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            im = Image.new('RGBA', img.size, color='#7289DA')
            img = img.convert('RGBA')
            if img.format == 'GIF':
                def multiply(frame):
                    return ImageChops.multiply(frame, im)

                data = func_to_gif(img, multiply,  get_raw=True)
                name = 'blurple.gif'
            else:
                img = ImageChops.multiply(img, im)
                data = self.save_image(img)
                name = 'blurple.png'

            return data, name

        async with ctx.typing():
            file = File(*await self.image_func(do_it))
        await ctx.send(file=file)

    @command(aliases=['gspd', 'gif_spd', 'speedup'])
    @cooldown(2, 5)
    async def gif_speed(self, ctx, image, speed=None):
        """
        Speed up or slow a gif down by multiplying the frame delay
        the specified speed (higher is faster, lower is slower, 1 is default speed)
        Due to the fact that different engines render gifs differently higher speed
        might not actually mean faster gif. After a certain threshold
        the engine will start throttling and set the frame delay to a preset default
        If this happens try making the speed value smaller
        """
        if speed is None:
            img = await get_image(ctx, None)
            speed = image
        else:
            img = await get_image(ctx, image)

        if img is None:
            return

        if not isinstance(img, GifImagePlugin.GifImageFile):
            raise BadArgument('Image must be a gif')

        try:
            speed = float(speed)
        except (ValueError, TypeError) as e:
            raise BadArgument(str(e))

        if speed == 1:
            return await ctx.send("Setting speed to 1 won't change the speed ya know")

        if not 0 < speed <= 10:
            raise BadArgument('Speed must be larger than 0 and less or equal to 10')

        def do_speedup():
            frames = convert_frames(img, 'RGBA')
            durations = get_duration(frames)

            def transform(duration):
                # Frame delay is stored as an unsigned 2 byte int
                # A delay of 0 would mean that the frame would change as fast
                # as the pc can do it which is useless. Also rendering engines
                # like to round delays higher up to 10 and most don't display the
                # smallest delays
                duration = min(max(duration//speed, 5), 65535)
                return duration

            durations = list(map(transform, durations))
            frames[0].info['duration'] = durations
            for f, d in zip(frames, durations):
                f.info['duration'] = d

            frames = apply_transparency(frames)
            file = BytesIO()
            frames[0].save(file, format='GIF', duration=durations, save_all=True,
                           append_images=frames[1:], loop=65535, optimize=False, disposal=2)
            file.seek(0)
            data = file.getvalue()
            if len(data) > 8000000:
                return optimize_gif(file.getvalue())

            return data

        async with ctx.typing():
            file = await self.image_func(do_speedup)
        await ctx.send(file=File(file, filename='speedup.gif'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def smug(self, ctx, image=None):
        img = await get_image(ctx, image)

        if img is None:
            return

        def do_it():
            nonlocal img
            img = img.convert('RGBA')
            template = Image.open(os.path.join(TEMPLATES, 'smug_man.png'))

            w, h = 729, 607
            img = resize_keep_aspect_ratio(img, (w, h), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)
            template.paste(img, (168, 827), img)
            return self.save_image(template)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='smug_man.png'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def seeyouagain(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'seeyouagain.png'))
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (360, 300), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            template.paste(img, (800, 915), img)
            return self.save_image(template)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='see_you_again.png'))

    @command(aliases=['sha'])
    @cooldown(2, 5, BucketType.guild)
    async def sheer_heart_attack(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'sheer_heart_attack.png'))
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (1000, 567), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True, background_color='white')

            template.paste(img, (0, 563), img)
            return self.save_image(template)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='sha.png'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def kira(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'kira.png'))
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (810, 980), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            bg = Image.new('RGBA', (1918, 2132), (0, 0, 0, 0))

            bg.paste(img, (610, 1125), img)
            bg.alpha_composite(template)
            return self.save_image(bg)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='kira.png'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def josuke(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'josuke.png'))
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (198, 250), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            bg = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))

            bg.paste(img, (1000, 155), img)
            bg.alpha_composite(template)
            return self.save_image(bg)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='josuke.png'))

    @command(aliases=['josuke2'])
    @cooldown(2, 5, BucketType.guild)
    async def josuke_binoculars(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'josuke_binoculars.png'))
            img = img.convert('RGBA')
            size = (700, 415)
            img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            bg = Image.new('RGBA', template.size, (255, 255, 255))

            bg.paste(img, (50, 460), img)
            bg.alpha_composite(template)
            return self.save_image(bg)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='josuke_binoculars.png'))

    @command(aliases=['02'])
    @cooldown(2, 5, BucketType.guild)
    async def zerotwo(self, ctx, image=None):
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'zerotwo.png')).convert('RGBA')
            img = img.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (840, 615), can_be_bigger=False,
                                           resample=Image.BICUBIC, crop_to_size=True,
                                           center_cropped=True)

            img = img.rotate(4, Image.BICUBIC, expand=True)

            template.alpha_composite(img, (192, 29))
            return self.save_image(template)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='02.png'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def dante(self, ctx, image=None):
        """Dante looking at a scene"""
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'dante.png')).convert('RGBA')
            img = img.convert('RGBA')
            img = img.resize((1316, 990), resample=Image.BICUBIC)

            img.alpha_composite(template, (0, 0))
            return self.save_image(img)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='dante.png'))

    @command()
    @cooldown(2, 8, BucketType.guild)
    async def v(self, ctx, image1, image2):
        """Image of V reading a book. Needs 2 images for both of the pages"""
        img1 = await dl_image(ctx, image1)
        if img1 is None:
            return

        img2 = await dl_image(ctx, image2)
        if not img2:
            return

        def do_it():
            nonlocal img1, img2
            template = Image.open(os.path.join(TEMPLATES, 'v.png')).convert('RGBA')
            img = img1.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (370, 475), can_be_bigger=False,
                                           resample=Image.BILINEAR, crop_to_size=True,
                                           center_cropped=True)

            template.alpha_composite(img, (100, 590))

            img = img2.convert('RGBA')
            img = resize_keep_aspect_ratio(img, (380, 475), can_be_bigger=False,
                                           resample=Image.BILINEAR, crop_to_size=True,
                                           center_cropped=True)
            template.alpha_composite(img, (518, 592))

            return self.save_image(template)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='v.png'))

    @command(aliases=['cj'])
    @cooldown(2, 5, BucketType.guild)
    async def ah_shit(self, ctx, stretch: Optional[bool]=True, image=None):
        """
        If stretch is set off the image will not be stretched to size
        """
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'ah_shit.png'))
            img = img.convert('RGBA')
            size = (843, 553)
            if stretch:
                img = img.resize(size, resample=Image.BICUBIC)
            else:
                img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                               resample=Image.BICUBIC,
                                               crop_to_size=True,
                                               center_cropped=True)

            img.alpha_composite(template)
            return self.save_image(img)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='ah_shit.png'))

    @command(aliases=['greatview'])
    @cooldown(2, 5, BucketType.guild)
    async def giorno(self, ctx, stretch: Optional[bool]=True, image=None):
        """
        If stretch is set off the image will not be stretched to size
        """
        img = await get_image(ctx, image)
        if img is None:
            return

        def do_it():
            nonlocal img
            template = Image.open(os.path.join(TEMPLATES, 'whatagreatview.png'))
            img = img.convert('RGBA')
            size = (868, 607)
            if stretch:
                img = img.resize(size, resample=Image.BICUBIC)
            else:
                img = resize_keep_aspect_ratio(img, size, can_be_bigger=False,
                                               resample=Image.BICUBIC,
                                               crop_to_size=True,
                                               center_cropped=True)

            bg = Image.new('RGBA', template.size, 'white')
            bg.paste(img, (212, 608), img)
            bg.alpha_composite(template)
            return self.save_image(bg)

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='02.png'))

    @command()
    @cooldown(2, 5, BucketType.guild)
    async def narancia(self, ctx, *, text: CleanContent(escape_markdown=True, fix_channel_mentions=True,
                                                        remove_everyone=False, fix_emotes=True)):
        """
        Make narancia write your choice of text on paper
        """
        text = text.strip('\u200b \n\r\t')

        def do_it():
            nonlocal text
            # Linearly decreasing fontsize
            fontsize = int(round(45.0 - 0.08 * len(text)))
            fontsize = min(max(fontsize, 15), 45)
            font = ImageFont.truetype(os.path.join('M-1c', 'mplus-1c-bold.ttf'), fontsize)
            im = Image.open(os.path.join(TEMPLATES, 'narancia.png'))
            shadow = Image.open(os.path.join(TEMPLATES, 'narancia_shadow.png'))
            draw = ImageDraw.Draw(im)
            size = (250, 350)  # Size of the page
            spot = (400, 770)  # Pasting spot for first page
            text = text.replace('\n', ' ')

            # We need to replace the height of the text with the height of A
            # Since that what draw.text uses in it's text drawing methods but not
            # in the text size methods. Nice design I know. It makes textsize inaccurate
            # so don't use that method
            text_size = font.getsize(text)
            text_size = (text_size[0], font.getsize('A')[1])

            # Linearly growing spacing
            spacing = int(round(0.5 + 0.167 * fontsize))
            spacing = min(max(spacing, 3), 6)

            # We add 2 extra to compensate for measuring inaccuracies
            line_height = text_size[1]
            spot_changed = False

            all_lines = []
            # Split lines based on average width
            # If max characters per line is less than the given max word
            # use max line width as max word width
            max_line = int(len(text) // (text_size[0] / size[0]))
            lines = split_string(text, maxlen=max_line, max_word=min(max_line, 30))
            total_y = 0

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                total_y += line_height
                if total_y > size[1]:
                    draw.multiline_text(spot, '\n'.join(all_lines), font=font,
                                        fill='black', spacing=spacing)
                    all_lines = []
                    if spot_changed:
                        # We are already on second page. Let's stop here
                        break

                    spot_changed = True
                    # Pasting spot and size for second page
                    spot = (678, 758)
                    size = (250, 350)
                    total_y = line_height

                total_y += spacing

                all_lines.append(line)

            draw.multiline_text(spot, '\n'.join(all_lines), font=font,
                                fill='black', spacing=spacing)

            im.alpha_composite(shadow)
            return self.save_image(im, 'PNG')

        async with ctx.typing():
            file = await self.image_func(do_it)
        await ctx.send(file=File(file, filename='narancia.png'))

    @command(aliases=['poke'])
    @cooldown(2, 2, type=BucketType.guild)
    async def pokefusion(self, ctx, poke1=Pokefusion.RANDOM, poke2=Pokefusion.RANDOM, color_poke=None):
        """
        Gets a random pokemon fusion from http://pokefusion.japeal.com
        You can specify the wanted fusion by specifying their pokedex index or their name or just a part of their name.
        Color poke defines the pokemon whose color palette will be used. By default it's not used
        Passing % as a parameter will randomize that value
        """
        if not self._pokefusion:
            return await ctx.send('Pokefusion not supported')
        await ctx.trigger_typing()
        try:
            img, s = await self._pokefusion.fuse(poke1, poke2, color_poke)
        except NoPokeFoundException as e:
            return await ctx.send(str(e))

        file = BytesIO()
        img.save(file, 'PNG')
        file.seek(0)
        await ctx.send(s, file=File(file, filename='pokefusion.png'))

    @command(aliases=['get_im'])
    @cooldown(3, 3, BucketType.guild)
    async def get_image(self, ctx, *, data=None):
        """Get's the latest image in the channel if data is None
        otherwise gets the image based on data. If data is an id, first avatar lookup is done
        then message lookup. If data is an image url this will just return that url"""
        img = await get_image_from_ctx(ctx, data)
        s = img if img else 'No image found'
        return await ctx.send(s)

    @command(owner_only=True)
    async def update_poke_cache(self, ctx):
        if await self._pokefusion.update_cache() is False:
            await ctx.send('Failed to update cache')
        else:
            await ctx.send('Successfully updated cache')


def setup(bot):
    bot.add_cog(Images(bot))
