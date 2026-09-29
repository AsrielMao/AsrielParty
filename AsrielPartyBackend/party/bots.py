"""
人机（机器人玩家）。

一个人 + 一台手机也能把需要好几个人的游戏跑起来：房主往房间里加几个人机，
它们由**后端**自动驱动 —— 轮到自己画会涂几笔，轮到夜晚会选目标，白天会投票。

**没有独立进程**：人机是在客户端轮询时顺手往前推的（和 draw_guess 的倒计时一个套路）。
所以人机只在「房间里至少有一个真人在看页面」的时候才动 —— 对线下局来说永远成立。

节奏：每个机器人两次行动之间至少隔 `BOT_ACT_INTERVAL` 秒，免得一次轮询就把
一整轮操作全跑完，看起来像瞬移。

> 人机的目标是**让你一个人也能把流程走完**，不是「陪你好好玩一局」。
> 尤其是你画我猜：机器人画手只会随手涂几笔（它没法真画出个东西），
> 所以那一轮基本靠机器人自己猜完。
"""

from . import draw_guess as dg
from . import undercover as uc
from . import werewolf as ww
from .auth import ApiError
from .constants import DRAW_GUESS_GAME_ID, UNDERCOVER_GAME_ID, WEREWOLF_GAME_ID
from .models import DrawGame, Room, RoomMember, UndercoverGame, User, WerewolfGame

# 人机的昵称（按需建账号，最多用到跟座位数一样多）
BOT_NAMES = [
    '小林', '阿哲', '丸子', '阿澈', '小满', '老周',
    '豆豆', '阿凯', '小鹿', '果果', '小北', '阿泽',
]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def bot_members(room):
    """房间里的人机成员。"""
    if room is None:
        return []
    return list(room.members.filter(user__is_bot=True).select_related('user'))


def has_bots(room):
    if room is None:
        return False
    return room.members.filter(user__is_bot=True).exists()


def is_bot_user(user):
    return bool(user is not None and getattr(user, 'is_bot', False))


# ---------------------------------------------------------------------------
# 增删
# ---------------------------------------------------------------------------

def _bot_user(index):
    """第 index 个人机账号（没有就建一个）。"""
    openid = 'bot_%02d' % index
    user = User.objects.filter(openid=openid).first()
    if user is not None:
        return user
    return User.objects.create(
        openid=openid,
        nickname=BOT_NAMES[index % len(BOT_NAMES)],
        is_bot=True,
    )


def _free_seat(room):
    used = set(room.members.values_list('seat', flat=True))
    for seat in range(room.max_players):
        if seat not in used:
            return seat
    return None


def _drop_member(room, member):
    """
    把人机从房间里拿掉。

    和「真人退出房间」走同一套收尾：先从对局里摘掉（免得游戏卡在它身上），
    再删成员记录。
    """
    uid = member.user_id
    uc.remove_player(UndercoverGame.objects.filter(room=room).first(), uid)
    ww.remove_player(WerewolfGame.objects.filter(room=room).first(), uid)
    dg.remove_player(DrawGame.objects.filter(room=room).first(), uid)
    member.delete()


def set_bot_count(room, count):
    """
    把房间里的人机数量调到 count 个（房主用）。

    超出座位数会自动截断；人机也占座位。
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ApiError('人机数量不对')
    if count < 0:
        raise ApiError('人机数量不能是负数')

    count = min(count, room.max_players)

    current = bot_members(room)

    # 先减
    for member in current[count:]:
        _drop_member(room, member)
    current = current[:count]

    # 再加
    index = 0
    while len(current) < count:
        if index >= 64:      # 安全阀：别在这儿转不出来
            break
        user = _bot_user(index)
        index += 1

        if room.members.filter(user=user).exists():
            continue

        seat = _free_seat(room)
        if seat is None:
            break            # 坐满了

        current.append(RoomMember.objects.create(
            room=room, seat=seat, user=user, ready=True,
        ))

    room.save(update_fields=['updated_at'])
    return room


def remove_bots(room):
    return set_bot_count(room, 0)


# ---------------------------------------------------------------------------
# 节奏
# ---------------------------------------------------------------------------
# 具体的「该不该动手」判断在 `RoomMember.may_bot_act()`（见 models.py）——
# 放在模型上是为了避免 bots ↔ 各游戏模块互相 import。

def tick(room):
    """让房间里的人机往前走一步（由客户端的轮询顺手调用）。"""
    if room is None or room.status != Room.STATUS_PLAYING:
        return

    bots = bot_members(room)
    if not bots:
        return

    if room.game_id == DRAW_GUESS_GAME_ID:
        dg.bot_step(room, bots)
    elif room.game_id == WEREWOLF_GAME_ID:
        ww.bot_step(room, bots)
    elif room.game_id == UNDERCOVER_GAME_ID:
        uc.bot_step(room, bots)
