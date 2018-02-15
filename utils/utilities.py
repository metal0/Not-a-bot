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

import asyncio
import logging
import mimetypes
import os
import re
import shlex
import subprocess
import time
from collections import OrderedDict
from datetime import timedelta
from random import randint

import discord
import numpy
from sqlalchemy.exc import SQLAlchemyError
from validators import url as test_url
from bot.globals import BlacklistTypes, PermValues

from bot.exceptions import NoCachedFileException, PermissionError

logger = logging.getLogger('debug')
audio = logging.getLogger('audio')

# https://stackoverflow.com/a/4628148/6046713
# Added days and and aliases for names
# Also fixed any string starting with the first option (e.g. 10dd would count as 10 days) being accepted
time_regex = re.compile(r'((?P<days>\d+?) ?(d|days)( |$))?((?P<hours>\d+?) ?(h|hours)( |$))?((?P<minutes>\d+?) ?(m|minutes)( |$))?((?P<seconds>\d+?) ?(s|seconds)( |$))?')
timeout_regex = re.compile(r'((?P<days>\d+?) ?(d|days)( |$))?((?P<hours>\d+?) ?(h|hours)( |$))?((?P<minutes>\d+?) ?(m|minutes)( |$))?((?P<seconds>\d+?) ?(s|seconds)( |$))?(?P<reason>.*)+?',
                        re.DOTALL)


class Object:
    # Empty class to store variables
    def __init__(self):
        pass


def split_string(to_split, list_join='', maxlen=2000, splitter=' '):
    if isinstance(to_split, str):
        if len(to_split) < maxlen:
            return [to_split]

        to_split = [s + splitter for s in to_split.split(splitter)]
        to_split[-1] = to_split[-1][:-1]
        length = 0
        split = ''
        splits = []
        for s in to_split:
            l = len(s)
            if length + l > maxlen:
                splits += [split]
                split = s
                length = l
            else:
                split += s
                length += l

        splits.append(split)

        return splits

    if isinstance(to_split, dict):
        splits_dict = OrderedDict()
        splits = []
        length = 0
        for key in to_split:
            joined = list_join.join(to_split[key])
            if length + len(joined) > maxlen:
                splits += [splits_dict]
                splits_dict = OrderedDict()
                splits_dict[key] = joined
                length = len(joined)
            else:
                splits_dict[key] = joined
                length += len(joined)

        if length > 0:
            splits += [splits_dict]

        return splits

    logger.debug('Could not split string {}'.format(to_split))
    raise NotImplementedError('This only works with dicts and strings for now')


async def mean_volume(file, loop, threadpool, avconv=False, duration=0):
    """Gets the mean volume from"""
    audio.debug('Getting mean volume')
    ffmpeg = 'ffmpeg' if not avconv else 'avconv'
    file = '"{}"'.format(file)

    if not duration:
        start, stop = 0, 180
    else:
        start = int(duration * 0.2)
        stop = start + 180
    cmd = '{0} -i {1} -ss {2} -t {3} -filter:a "volumedetect" -vn -sn -f null /dev/null'.format(ffmpeg, file, start, stop)

    args = shlex.split(cmd)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    out, err = await loop.run_in_executor(threadpool, process.communicate)
    out += err

    matches = re.findall('mean_volume: [\-\d\.]+ dB', str(out))

    if not matches:
        return
    try:
        volume = float(matches[0].split(' ')[1])
        audio.debug('Parsed volume is {}'.format(volume))
        return volume
    except ValueError:
        return


def get_cached_song(name):
    if os.path.isfile(name):
        return name
    else:
        raise NoCachedFileException


# Write the contents of an iterable or string to a file
def write_playlist(file, contents, mode='w'):
    if not isinstance(contents, str):
        contents = '\n'.join(contents) + '\n'
        if contents == '\n':
            return

    with open(file, mode, encoding='utf-8') as f:
        f.write(contents)


# Read lines from a file and put them to a list without newlines
def read_lines(file):
    if not os.path.exists(file):
        return []

    with open(file, 'r', encoding='utf-8') as f:
        songs = f.read().splitlines()

    return songs


# Empty the contents of a file
def empty_file(file):
    with open(file, 'w', encoding='utf-8'):
        pass


def timestamp():
    return time.strftime('%d-%m-%Y--%H-%M-%S')


def get_config_value(config, section, option, option_type=None, fallback=None):
    try:
        if option_type is None:
            return config.get(section, option, fallback=fallback)

        if issubclass(option_type, bool):
            return config.getboolean(section, option, fallback=fallback)

        elif issubclass(option_type, str):
            return str(config.get(section, option, fallback=fallback))

        elif issubclass(option_type, int):
            return config.getint(section, option, fallback=fallback)

        elif issubclass(option_type, float):
            return config.getfloat(section, option, fallback=fallback)

        else:
            return config.get(section, option, fallback=fallback)

    except ValueError as e:
        print('[ERROR] {0}\n{1} value is not {2}. {1} set to {3}'.format(e, option, str(option_type), str(fallback)))
        return fallback


def write_wav(stdout, filename):
    with open(filename, 'wb') as f:
        f.write(stdout.read(36))

        # ffmpeg puts metadata that breaks bpm detection. We are going to remove that
        stdout.read(34)

        while True:
            data = stdout.read(100000)
            if len(data) == 0:
                break
            f.write(data)

    return filename


def y_n_check(msg):
    msg = msg.content.lower().strip()
    return msg in ['y', 'yes', 'n', 'no']


def y_check(s):
    s = s.lower().strip()
    return s in ['y', 'yes']


def bool_check(s):
    msg = s.lower().strip()
    return msg in ['y', 'yes', 'true', 'on']


def check_negative(n):
    if n < 0:
        return -1
    else:
        return 1


def get_emote_url(emote):
    animated, emote_id = get_emote_id(emote)
    if emote_id is None:
        return

    extension = 'png' if not animated else 'gif'
    return 'https://cdn.discordapp.com/emojis/{}.{}'.format(emote_id, extension)


def emote_url_from_id(id):
    return 'https://cdn.discordapp.com/emojis/%s.png' % id


def get_picture_from_msg(msg):
    if msg is None:
        return

    if len(msg.attachments) > 0:
        return msg.attachments[0]['url']

    words = msg.content.split(' ')
    for word in words:
        if test_url(word):
            return word


def normalize_text(s):
    # Get cleaned emotes
    matches = re.findall('<:\w+:\d+>', s)

    for match in matches:
        s = s.replace(match, match.split(':')[1])

    #matches = re.findall('<@\d+>')
    return s


def slots2dict(obj, d: dict=None, replace=True):
    # Turns slots and @properties to a dict

    if d is None:
        d = {k: getattr(obj, k, None) for k in obj.__slots__}
    else:
        for k in obj.__slots__:
            if not replace and k in d:
                continue

            d[k] = getattr(obj, k, None)

    for k in dir(obj):
        if k.startswith('_'):  # We don't want any private variables
            continue

        v = getattr(obj, k)
        if not callable(v):    # Don't need methods here
            if not replace and k in d:
                continue

            d[k] = v

    return d


async def retry(f, *args, retries_=3, **kwargs):
    e = None
    for i in range(0, retries_):
        try:
            retval = await f(*args, **kwargs)
        except Exception as e_:
            e = e_
        else:
            return retval

    return e


def get_emote_id(s):
    emote = re.match('(?:<(a)?:\w+:)(\d+)(?=>)', s)
    if emote:
        return emote.groups()

    return None, None


def get_emote_name(s):
    emote = re.match('(?:<(a)?:)(\w+)(?::\d+)(?=>)', s)
    if emote:
        return emote.groups()

    return None, None


def get_emote_name_id(s):
    emote = re.match('(?:<(a)?:)(\w+)(?::)(\d+)(?=>)', s)
    if emote:
        return emote.groups()

    return None, None, None


def get_image_from_message(ctx, *messages):
    image = None
    if len(ctx.message.attachments) > 0:
        image = ctx.message.attachments[0]['url']
    elif messages and messages[0] is not None:
        image = str(messages[0])
        if not test_url(image):
            if re.match('<@!?\d+>', image) and ctx.message.mentions:
                image = get_avatar(ctx.message.mentions[0])
            elif get_emote_id(image)[1]:
                image = get_emote_url(image)
            else:
                try:
                    int(image)
                except ValueError:
                    pass
                else:
                    user = discord.utils.find(lambda u: u.id == image, ctx.bot.get_all_members())
                    if user:
                        image = get_avatar(user)

    if image is None:
        channel = ctx.message.channel
        server = channel.server
        session = ctx.bot.get_session
        sql = 'SELECT attachment FROM `messages` WHERE server={} AND channel={} ORDER BY `message_id` DESC LIMIT 25'.format(server.id, channel.id)
        try:
            rows = session.execute(sql).fetchall()
            for row in rows:
                attachment = row['attachment']
                if not attachment:
                    continue

                if is_image_url(attachment):
                    image = attachment
                    break

                # This is needed because some images like from twitter
                # are usually in the format of someimage.jpg:large
                # and mimetypes doesn't recognize that
                elif is_image_url(':'.join(attachment.split(':')[:-1])):
                    image = attachment
                    break

        except SQLAlchemyError:
            pass

    return image


def random_color():
    """
    Create a random color to be used in discord
    Returns:
        Random discord.Color
    """

    return discord.Color(randint(0, 16777215))


# https://stackoverflow.com/a/4628148/6046713
def parse_time(time_str):
    parts = time_regex.match(time_str)
    if not parts:
        return

    parts = parts.groupdict()
    time_params = {name: int(param) for name, param in parts.items() if param}

    return timedelta(**time_params)


def parse_timeout(time_str):
    parts = timeout_regex.match(time_str)
    if not parts:
        return

    parts = parts.groupdict()
    reason = parts.pop('reason')
    time_params = {name: int(param) for name, param in parts.items() if param}

    return timedelta(**time_params), reason


def datetime2sql(datetime):
    return '{0.year}-{0.month}-{0.day} {0.hour}:{0.minute}:{0.second}'.format(datetime)


def call_later(func, loop, timeout, *args, **kwargs):
    """
    Call later for async functions
    Args:
        func: async function
        loop: asyncio loop
        timeout: how long to wait

    Returns:
        asyncio.Task
    """
    async def wait():
        if timeout > 0:
            try:
                await asyncio.sleep(timeout)
            except asyncio.CancelledError:
                return

        await func(*args, **kwargs)

    return loop.create_task(wait())


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


def check_channel_mention(msg, word):
    if msg.channel_mentions:
        if word != msg.channel_mentions[0].mention:
            return False
        return True
    return False


def get_channel(channels, s, name_matching=False, only_text=True):
    channel = get_channel_id(s)
    try:
        int(s)
    except ValueError:
        pass
    else:
        channel = discord.utils.find(lambda c: c.id == s, channels)

    if not channel and name_matching:
        channel = discord.utils.find(lambda c: c.name == s, channels)
        if not channel:
            return
    else:
        return

    if only_text and channel.type != discord.ChannelType.text:
        return

    return channel


def check_role_mention(msg, word, server):
    if msg.raw_role_mentions:
        id = msg.raw_role_mentions[0]
        if id not in word:
            return False
        role = list(filter(lambda r: r.id == id, server.roles))
        if not role:
            return False
        return role[0]
    return False


def get_role(role, roles, name_matching=False):
    try:
        int(role)
        role_id = role
    except:
        role_id = get_role_id(role)

        if role_id is None and name_matching:
            for role_ in roles:
                if role_.name == role:
                    return role_
        elif role_id is None:
            return

    return discord.utils.find(lambda r: r.id == role_id, roles)


def check_user_mention(msg, word):
    if msg.mentions:
        if word != msg.mentions[0].mention:
            return False
        return True
    return False


def get_avatar(user):
    return user.avatar_url or user.default_avatar_url


def get_role_id(s):
    regex = re.compile(r'(?:<@&)?(\d+)(?:>)?(?: |$)')
    match = regex.match(s)
    if match:
        return match.groups()[0]


def get_user_id(s):
    regex = re.compile(r'(?:<@!?)?(\d+)(?:>)?(?: |$)')
    match = regex.match(s)
    if match:
        return match.groups()[0]


def get_channel_id(s):
    regex = re.compile(r'(?:<#)?(\d+)(?:>)?')
    match = regex.match(s)
    if match:
        return match.groups()[0]


def seconds2str(seconds):
    seconds = int(round(seconds, 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)

    def check_plural(string, i):
        if i != 1:
            return '%s %ss ' % (str(i), string)
        return '%s %s ' % (str(i), string)

    d = check_plural('day', d) if d else ''
    h = check_plural('hour', h) if h else ''
    m = check_plural('minute', m) if m else ''
    s = check_plural('second', s) if s else ''

    return d + h + m + s.strip()


def find_user(s, members, case_sensitive=False, ctx=None):
    if not s:
        return
    if ctx:
        # if ctx is present check mentions first
        if ctx.message.mentions and ctx.message.mentions[0].mention.replace('!', '') == s.replace('!', ''):
            return ctx.message.mentions[0]

        try:
            uid = int(s)
        except:
            pass
        else:
            found = list(filter(lambda u: int(u.id) == uid, members))
            if found:
                return found[0]

    def filter_users(predicate):
        for member in members:
            if predicate(member):
                return member

            if member.nick and predicate(member.nick):
                return member

    p = lambda u: str(u).startswith(s) if case_sensitive else lambda u: str(u).lower().startswith(s)
    found = filter_users(p)
    s = '`<@!{}>` {}'
    if found:
        return found

    p = lambda u: s in str(u) if case_sensitive else lambda u: s in str(u).lower()
    found = filter_users(p)

    return found


def is_image_url(url):
    if url is None:
        return

    if not test_url(url):
        return False

    mimetype, encoding = mimetypes.guess_type(url)
    return mimetype and mimetype.startswith('image')


def msg2dict(msg):
    d = {}
    attachments = [attachment['url'] for attachment in msg.attachments if 'url' in attachment]
    d['attachments'] = ', '.join(attachments)
    return d


def format_on_edit(before, after, message, check_equal=True):
    bef_content = before.content
    aft_content = after.content
    if check_equal:
        if bef_content == aft_content:
            return

    user = before.author

    d = slots2dict(user)
    d = slots2dict(after, d)
    for e in ['name', 'before', 'after']:
        d.pop(e, None)

    d['channel'] = after.channel.mention
    message = message.format(name=str(user), **d,
                             before=bef_content, after=aft_content)

    return message


def format_join_leave(member, message):
    d = slots2dict(member)
    d.pop('user', None)
    message = message.format(user=str(member), **d)
    return message


def format_on_delete(msg, message):
    content = msg.content
    user = msg.author

    d = slots2dict(user)
    d = slots2dict(msg, d)
    for e in ['name', 'message']:
        d.pop(e, None)

    d['channel'] = msg.channel.mention
    message = message.format(name=str(user), message=content, **d)
    return message


# https://stackoverflow.com/a/14178717/6046713
def find_coeffs(pa, pb):
    matrix = []
    for p1, p2 in zip(pa, pb):
        matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0]*p1[0], -p2[0]*p1[1]])
        matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1]*p1[0], -p2[1]*p1[1]])

    A = numpy.matrix(matrix, dtype=numpy.float)
    B = numpy.array(pb).reshape(8)

    res = numpy.dot(numpy.linalg.inv(A.T * A) * A.T, B)
    return numpy.array(res).reshape(8)


def check_perms(values, return_raw=False):
    # checks if the command can be used based on what rows are given
    # ONLY GIVE this function rows of one command or it won't work as intended
    smallest = 18
    for value in values:
        if value['type'] == BlacklistTypes.WHITELIST:
            v1 = PermValues.VALUES['whitelist']
        else:
            v1 = PermValues.VALUES['blacklist']

        if value['user'] is not None:
            v2 = PermValues.VALUES['user']
        elif value['role'] is not None:
            v2 = PermValues.VALUES['role']
        elif value['channel'] is not None:
            v2 = PermValues.VALUES['channel']
        else:
            v2 = PermValues.VALUES['server']

        v = v1 | v2
        if v < smallest:
            smallest = v

    return PermValues.RETURNS.get(smallest, False) if not return_raw else smallest


def is_superset(ctx, command_):
    if ctx.override_perms is None and command_.required_perms is not None:
        perms = ctx.message.channel.permissions_for(ctx.message.author)

        if not perms.is_superset(command_.required_perms):
            req = [r[0] for r in command_.required_perms if r[1]]
            raise PermissionError('%s' % ', '.join(req))

    return True

