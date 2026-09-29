"""
全部 API 视图。

路径见 party/urls.py，协议见 AsrielParty/docs/backend-api.md。
每个视图都用 @api_view 包住，失败统一返回 { "detail": "中文提示" }，
小程序 utils/request.js 会把它直接 toast 出来。
"""

import os
import random
import uuid

from django.conf import settings
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone

from . import bots
from . import draw_guess as draw
from . import undercover as uc
from . import werewolf as ww
from .auth import ApiError, api_view, payload, require_user
from .constants import (
    DEFAULT_SEATS,
    DRAW_GUESS_GAME_ID,
    GAMES,
    ROOM_CODE_CHARS,
    ROOM_CODE_LENGTH,
    UNDERCOVER_GAME_ID,
    VOICE_MAX_PLAYERS,
    WEREWOLF_GAME_ID,
    find_variant,
    game_by_id,
)
from .models import DrawGame, Room, RoomMember, UndercoverGame, User, WerewolfGame
from .presenters import room_to_dict, user_to_dict
from .wechat import code2session, voip_signature


def as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def touch(room):
    """房间有任何变化都刷新 updatedAt —— 房间列表按它排序。"""
    room.save(update_fields=['updated_at'])


def gen_room_code():
    for _ in range(60):
        code = ''.join(random.choice(ROOM_CODE_CHARS) for _ in range(ROOM_CODE_LENGTH))
        if not Room.objects.filter(code=code).exists():
            return code
    raise ApiError('房间号分配失败，请稍后重试')


def find_room(data):
    """按 roomId 或房间号找房间；找不到按协议返回 404（小程序据此自动退出房间页）。"""
    room_id = data.get('roomId')
    room = None

    if room_id:
        room = Room.objects.filter(pk=as_int(room_id)).first()
    else:
        code = (data.get('code') or '').strip().upper()
        if code:
            room = Room.objects.filter(code=code).first()

    if room is None:
        raise ApiError('房间不存在或已解散', status=404)
    return room


def require_owner(room, user):
    if room.owner_id != user.id:
        raise ApiError('只有房主可以进行这个操作')


def undercover_game_of(room):
    return UndercoverGame.objects.filter(room=room).first()


def draw_game_of(room):
    return DrawGame.objects.filter(room=room).first()


# ===========================================================================
# 根路径
# ===========================================================================

def _pending_migrations():
    """
    还没应用到这个库上的迁移。

    **这个必须盯着**：代码里加了字段但库没迁移，接口会直接 500
    （比如加 `User.is_bot` 之后没跑 migrate，登录就炸了）。
    """
    try:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        return executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:  # noqa: BLE001  库都连不上时别把状态页本身也搞挂
        return []


def index(request):
    """
    根路径上的一个「服务在跑」状态页。

    后端只提供 /api/... 接口、没有网页界面，所以根路径原本是 404 ——
    用浏览器打开看到一坨 Page not found，很容易让人以为服务没起来。
    这里放个小页面把话说清楚，顺便列出接口清单和「小程序怎么连过来」。
    """
    from . import urls as party_urls

    routes = []
    for pattern in party_urls.urlpatterns:
        methods = getattr(pattern.callback, 'allowed_methods', None)
        routes.append({
            'path': '/' + str(pattern.pattern),
            'methods': '/'.join(methods) if methods else '',
        })

    configured = bool(getattr(settings, 'WECHAT_APPSECRET', ''))
    pending = _pending_migrations()

    return render(request, 'backend_status.html', {
        'now': timezone.localtime().strftime('%Y-%m-%d %H:%M:%S'),
        'debug_text': '开' if settings.DEBUG else '关',
        'secret_text': '已配置' if configured else '未配置（登录走开发降级）',
        'secret_ok': configured,
        'pending_count': len(pending),
        'pending_names': [plan[0].name for plan in pending],
        'routes': routes,
    })


# ===========================================================================
# 登录与用户
# ===========================================================================

@api_view(['POST'])
def login(request):
    """用 wx.login 的 code 换 openid 并登录。"""
    data = payload(request)
    code = (data.get('code') or '').strip()
    if not code:
        raise ApiError('缺少 code')

    session = code2session(code)
    user = User.objects.filter(openid=session['openid']).first()
    nickname = (data.get('nickname') or '').strip()[:32]
    avatar = data.get('avatar') or ''

    if user is None:
        user = User.objects.create(
            openid=session['openid'],
            unionid=session.get('unionid') or '',
            nickname=nickname or '微信用户',
            avatar=avatar,
        )
    else:
        fields = []
        if nickname and nickname != user.nickname:
            user.nickname = nickname
            fields.append('nickname')
        if avatar and avatar != user.avatar:
            user.avatar = avatar
            fields.append('avatar')
        if session.get('unionid') and session['unionid'] != user.unionid:
            user.unionid = session['unionid']
            fields.append('unionid')
        if fields:
            user.save(update_fields=fields)

    return {'token': user.token, 'user': user_to_dict(user)}


@api_view(['GET', 'PATCH'])
def user_me(request):
    """GET 取资料，PATCH 改资料。"""
    user = require_user(request)

    if request.method == 'GET':
        return user_to_dict(user)

    data = payload(request)
    fields = []

    if 'nickname' in data:
        nickname = (data.get('nickname') or '').strip()
        if not nickname:
            raise ApiError('昵称不能为空')
        if len(nickname) > 20:
            raise ApiError('昵称不能超过 20 个字')
        user.nickname = nickname
        fields.append('nickname')

    if 'avatar' in data:
        user.avatar = data.get('avatar') or ''
        fields.append('avatar')

    if fields:
        user.save(update_fields=fields)

    return user_to_dict(user)


@api_view(['POST'])
def user_avatar(request):
    """头像上传（multipart/form-data，字段名 avatar），返回 { url }。"""
    require_user(request)

    uploaded = request.FILES.get('avatar')
    if uploaded is None:
        raise ApiError('没有收到头像文件')

    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        ext = '.png'

    rel_path = 'avatars/%s%s' % (uuid.uuid4().hex, ext)
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, 'wb+') as dest:
        for chunk in uploaded.chunks():
            dest.write(chunk)

    return {'url': request.build_absolute_uri(settings.MEDIA_URL + rel_path)}


# ===========================================================================
# 游戏
# ===========================================================================

@api_view(['GET'])
def list_games(request):
    """
    返回空数组。

    游戏规则文案（介绍 / 角色 / 分节规则 / 小贴士）是静态内容，留在小程序里
    （AsrielParty/utils/games.js），没必要每次开局都从服务器拉一遍；
    小程序 utils/api.js 拿到空数组时会自动回落到本地那份数据。

    后端自己用 party/constants.py 的 GAMES 做人数校验。
    什么时候想把规则做成可后台配置的，再把这个接口填上即可。
    """
    return []


# ===========================================================================
# 房间
# ===========================================================================

@api_view(['GET'])
def rooms_mine(request):
    user = require_user(request)
    rooms = (
        Room.objects.filter(members__user=user)
        .select_related('owner')
        .prefetch_related('members__user')
        .order_by('-updated_at')
    )
    return [room_to_dict(room, user) for room in rooms]


@api_view(['POST'])
def room_create(request):
    user = require_user(request)
    data = payload(request)

    game = game_by_id(data.get('gameId'))
    if game is None:
        raise ApiError('请选择一个游戏类型')

    # 座位数：有版型的游戏（狼人杀）由版型决定，其余游戏固定用默认座位数
    wanted_variant = data.get('variantId')
    variant = find_variant(game['id'], wanted_variant)
    if wanted_variant and variant is None:
        raise ApiError('版型不存在')

    max_players = variant['seats'] if variant else DEFAULT_SEATS

    if max_players < game['min_players']:
        raise ApiError('%s 至少需要 %d 人' % (game['name'], game['min_players']))

    title = (data.get('title') or '').strip()[:64] or ('%s 的房间' % user.nickname)

    with transaction.atomic():
        room = Room.objects.create(
            code=gen_room_code(),
            title=title,
            game_id=game['id'],
            variant_id=variant['id'] if variant else '',
            max_players=max_players,
            owner=user,
            status=Room.STATUS_WAITING,
            undercover_count=1,
        )
        RoomMember.objects.create(room=room, user=user, seat=0, ready=False)

    return room_to_dict(room, user)


@api_view(['GET'])
def room_detail(request):
    user = require_user(request)
    room = find_room(payload(request))
    # 两个「顺手推进」：
    #   - 你画我猜的倒计时是真的，到点了得有人推它一把
    #   - 人机也要往前走（谁在轮询谁就顺手带着它们）
    draw.tick(draw_game_of(room))
    bots.tick(room)
    return room_to_dict(room, user)


@api_view(['POST'])
def room_join(request):
    user = require_user(request)
    room = find_room(payload(request))

    with transaction.atomic():
        room = Room.objects.select_for_update().get(pk=room.pk)
        existing = RoomMember.objects.filter(room=room, user=user).first()
        if existing is None:
            if room.members.count() >= room.max_players:
                raise ApiError('房间已满')
            used = set(room.members.values_list('seat', flat=True))
            seat = next(i for i in range(room.max_players) if i not in used)
            RoomMember.objects.create(room=room, user=user, seat=seat, ready=False)
        touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def room_leave(request):
    user = require_user(request)
    room = find_room(payload(request))

    with transaction.atomic():
        # 先把他从对局里摘掉，免得「等所有人投完」永远等不到
        uc.remove_player(undercover_game_of(room), user.id)
        ww.remove_player(werewolf_game_of(room), user.id)
        draw.remove_player(draw_game_of(room), user.id)

        RoomMember.objects.filter(room=room, user=user).delete()

        if room.members.count() == 0:
            room.delete()
        else:
            if room.owner_id == user.id:
                first = room.members.select_related('user').order_by('seat').first()
                if first is not None:
                    room.owner = first.user
                    room.save(update_fields=['owner', 'updated_at'])
            else:
                touch(room)

    return {'ok': True}


@api_view(['PATCH'])
def room_update(request):
    """房主改游戏类型 / 房间名，或结束本局（status=waiting）。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    require_owner(room, user)

    fields = []

    new_game_id = data.get('gameId')
    if new_game_id:
        game = game_by_id(new_game_id)
        if game is None:
            raise ApiError('游戏类型不存在')

        # 换游戏时顺带换版型 —— 座位数由版型决定
        wanted_variant = data.get('variantId')
        variant = find_variant(game['id'], wanted_variant)
        if wanted_variant and variant is None:
            raise ApiError('版型不存在')

        new_seats = variant['seats'] if variant else room.max_players
        if new_seats < game['min_players']:
            raise ApiError('%s 至少需要 %d 人' % (game['name'], game['min_players']))

        member_count = room.members.count()
        if member_count > new_seats:
            raise ApiError('房间里已经有 %d 个人，这个版型只有 %d 个座位' % (member_count, new_seats))

        room.game_id = game['id']
        room.variant_id = variant['id'] if variant else ''
        room.max_players = new_seats
        room.members.update(ready=False)
        # 换了游戏，上一局作废
        UndercoverGame.objects.filter(room=room).delete()
        WerewolfGame.objects.filter(room=room).delete()
        DrawGame.objects.filter(room=room).delete()
        fields.extend(['game_id', 'variant_id', 'max_players'])

    title = data.get('title')
    if isinstance(title, str) and title.strip():
        room.title = title.strip()[:64]
        fields.append('title')

    if data.get('status') == Room.STATUS_WAITING and room.status == Room.STATUS_PLAYING:
        room.status = Room.STATUS_WAITING
        room.members.update(ready=False)
        # 结束本局 -> 这一局作废（三个游戏的对局记录都要清掉）
        UndercoverGame.objects.filter(room=room).delete()
        WerewolfGame.objects.filter(room=room).delete()
        DrawGame.objects.filter(room=room).delete()
        fields.append('status')

    if fields:
        room.save(update_fields=fields + ['updated_at'])

    return room_to_dict(room, user)


@api_view(['POST'])
def room_bots(request):
    """
    房主设置房间里的人机数量（一个人 + 一台手机也能玩需要好几个人的游戏）。

    请求：`{ "roomId": 1, "count": 3 }` —— count 是「想要几个人机」，
    后端会把人机调到这个数量；超出座位数会自动截断，人机也占座位。

    游戏进行中不允许改（先「结束本局」）。
    """
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    require_owner(room, user)

    if room.status == Room.STATUS_PLAYING:
        raise ApiError('游戏进行中不能改人机，先结束本局')

    bots.set_bot_count(room, as_int(data.get('count')))

    return room_to_dict(room, user)


@api_view(['POST'])
def room_ready(request):
    user = require_user(request)
    data = payload(request)
    room = find_room(data)

    member = RoomMember.objects.filter(room=room, user=user).first()
    if member is None:
        raise ApiError('你不在这个房间里')

    member.ready = bool(data.get('ready'))
    member.save(update_fields=['ready'])
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def room_kick(request):
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    require_owner(room, user)

    target_id = as_int(data.get('userId'))
    if not target_id or target_id == user.id:
        raise ApiError('不能把自己移出房间')

    member = RoomMember.objects.filter(room=room, user_id=target_id).first()
    if member is None:
        raise ApiError('该玩家已不在房间中')

    uc.remove_player(undercover_game_of(room), target_id)
    ww.remove_player(werewolf_game_of(room), target_id)
    draw.remove_player(draw_game_of(room), target_id)
    member.delete()
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def room_start(request):
    """房主开局。人数够就行，不要求坐满，也不要求全员准备。"""
    user = require_user(request)
    room = find_room(payload(request))
    require_owner(room, user)

    game = game_by_id(room.game_id)
    if game is None:
        raise ApiError('请先选择游戏类型')

    count = room.members.count()
    if count < game['min_players']:
        raise ApiError(
            '%s 至少需要 %d 人，现在只有 %d 人' % (game['name'], game['min_players'], count)
        )

    # **必须原子完成**：改状态和建对局要一起成功或一起失败。
    # 拆开做的后果很坑 —— 中途出错（比如库没迁移）会留下「房间是 playing、
    # 但一局都没建起来」的脏状态，前端就会以为还在游戏中，
    # 一直去轮询一个根本不存在的对局（后端只能一直回 400）。
    with transaction.atomic():
        room.status = Room.STATUS_PLAYING
        room.save(update_fields=['status', 'updated_at'])

        # 重开就重新发牌
        UndercoverGame.objects.filter(room=room).delete()
        WerewolfGame.objects.filter(room=room).delete()
        DrawGame.objects.filter(room=room).delete()

        if room.game_id == UNDERCOVER_GAME_ID:
            uc.start_game(room)
        elif room.game_id == WEREWOLF_GAME_ID:
            ww.start_game(room)
        elif room.game_id == DRAW_GUESS_GAME_ID:
            draw.start_game(room)

    return room_to_dict(room, user)


# ===========================================================================
# 实时语音
# ===========================================================================

@api_view(['POST'])
def voice_signature(request):
    """
    签发给 wx.joinVoIPChat 的入场凭证。

    音频流本身走微信自己的通道，不经过这台服务器 —— 这里只负责用 AppSecret 算出签名。
    """
    require_user(request)
    room = find_room(payload(request))

    if room.members.count() > VOICE_MAX_PLAYERS:
        raise ApiError('实时语音单个房间最多 %d 人' % VOICE_MAX_PLAYERS)

    return voip_signature('ap_room_' + room.code)


# ===========================================================================
# 谁是卧底
# ===========================================================================

@api_view(['POST'])
def undercover_setup(request):
    """房主设定卧底人数。上限是「必须小于房间人数的一半」。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    require_owner(room, user)

    if room.status == Room.STATUS_PLAYING:
        raise ApiError('游戏已经开始了，中途不能改卧底人数')
    if room.game_id != UNDERCOVER_GAME_ID:
        raise ApiError('当前房间不是谁是卧底')

    cap = uc.max_undercover(room.members.count())
    if cap < 1:
        raise ApiError('人太少啦，至少要 4 个人才能玩谁是卧底')

    wanted = as_int(data.get('undercoverCount'), 1)
    room.undercover_count = max(1, min(wanted, cap))
    room.save(update_fields=['undercover_count', 'updated_at'])

    return room_to_dict(room, user)


@api_view(['POST'])
def undercover_vote(request):
    """投票。所有人都投完后自动结算本轮。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)

    game = undercover_game_of(room)
    if game is None:
        raise ApiError('游戏还没开始')

    target_id = as_int(data.get('targetUserId'))
    if not target_id:
        raise ApiError('缺少 targetUserId')

    uc.cast_vote(game, user, target_id)
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def undercover_next_round(request):
    """房主开下一轮。"""
    user = require_user(request)
    room = find_room(payload(request))
    require_owner(room, user)

    game = undercover_game_of(room)
    if game is None:
        raise ApiError('游戏还没开始')

    uc.next_round(game)
    touch(room)

    return room_to_dict(room, user)


# ===========================================================================
# 狼人杀
# ===========================================================================

def werewolf_game_of(room):
    return WerewolfGame.objects.filter(room=room).first()


def require_werewolf_game(room):
    game = werewolf_game_of(room)
    if game is None:
        raise ApiError('狼人杀还没开始')
    return game


@api_view(['POST'])
def werewolf_act(request):
    """
    提交一个动作。同一个接口扛下所有：

      - 夜晚行动：查验 / 守护 / 摄梦 / 刀人 / 验尸 —— 具体做什么由
        「现在轮到哪个角色」决定，只有被唤醒的角色能操作；
      - 女巫用药：多传一个 `skill`（`save` 解药 / `poison` 毒药），
        不传 skill、也不传目标就是「这晚不用药」；
      - 猎人出局后的开枪：这时也只有他能调。
    """
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    game = require_werewolf_game(room)

    ww.act(
        game,
        user,
        as_int(data.get('targetUserId')) or None,
        data.get('skill') or '',
    )
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def werewolf_vote(request):
    """放逐投票 / PK 投票。targetUserId 为空（或不传）表示弃票。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    game = require_werewolf_game(room)

    ww.cast_vote(game, user, as_int(data.get('targetUserId')))
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def werewolf_advance(request):
    """
    推进当前阶段。**不需要房主** —— 由「当前该操作的人」决定：

      天亮宣布  -> 谁点一下都能往下走
      发言/PK   -> 只有当前发言人能点「我说完了」
      结算      -> 谁点一下都能进入下一夜
      夜晚/投票 -> 不用点，各自会自动流转
    """
    user = require_user(request)
    room = find_room(payload(request))
    game = require_werewolf_game(room)

    ww.advance(game, user)
    touch(room)

    return room_to_dict(room, user)


# ===========================================================================
# 你画我猜
# ===========================================================================

def require_draw_game(room):
    game = draw_game_of(room)
    if game is None:
        raise ApiError('你画我猜还没开始')
    return game


@api_view(['POST'])
def draw_pick(request):
    """画手从候选词里挑一个（只有这一轮的画手能调）。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    game = require_draw_game(room)

    draw.pick_word(game, user, data.get('wordIndex'))
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def draw_strokes(request):
    """
    画手提交一批笔画。

    请求：`{ "roomId": 1, "strokes": [{ "points": [[x, y], ...], "color": "#f3f0ff", "width": 6 }] }`

    坐标是 **0~1 的归一化值**（客户端按自己的画布尺寸换算），
    这样不管谁的手机多大，看到的都是同一张画。

    返回很轻（只回一个序号），因为画手会频繁调它。
    """
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    game = require_draw_game(room)

    strokes = data.get('strokes')
    if not isinstance(strokes, list):
        raise ApiError('strokes 必须是数组')

    draw.add_strokes(game, user, strokes)
    touch(room)

    return {'ok': True, 'strokeSeq': game.stroke_seq}


@api_view(['POST'])
def draw_guess(request):
    """提交一个猜测。猜对会自动计分；所有人都猜对时这一轮直接结束。"""
    user = require_user(request)
    data = payload(request)
    room = find_room(data)
    game = require_draw_game(room)

    draw.submit_guess(game, user, data.get('text'))
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def draw_advance(request):
    """本轮结算看完，进入下一轮（谁都能点）。最后一轮之后整局结束。"""
    user = require_user(request)
    room = find_room(payload(request))
    game = require_draw_game(room)

    draw.advance(game, user)
    touch(room)

    return room_to_dict(room, user)


@api_view(['POST'])
def draw_clear(request):
    """画手把画布擦了重来（会作为一条「清屏」信号同步给所有人）。"""
    user = require_user(request)
    room = find_room(payload(request))
    game = require_draw_game(room)

    draw.clear_canvas(game, user)
    touch(room)

    return {'ok': True, 'strokeSeq': game.stroke_seq}


@api_view(['GET'])
def draw_poll(request):
    """
    画板的快速轮询（客户端大约每 0.7 秒调一次）。

    一次带三类东西回去：

      - `sinceSeq` 之后**新增的笔画**（增量同步，画手那边攒一会儿再批量提交）
      - 最近的猜测流水、谁已经猜对了
      - 阶段与剩余秒数 —— 顺手调一下 `tick()`，让超时的阶段自己往下走

    请求：`GET /api/rooms/draw/poll?roomId=1&sinceSeq=0`

    注意：换轮时服务端会把笔画清空并把序号归零，客户端要靠返回里的 `round`
    发现「该清屏了」，然后把 `sinceSeq` 也重置成 0。
    """
    user = require_user(request)
    data = payload(request)
    room = find_room(data)

    # 顺手推进两件事：超时的阶段、还有房间里的人机（谁在轮询谁就带着它们）
    draw.tick(draw_game_of(room))
    bots.tick(room)

    # 人机可能刚动过（比如替自己选了词），所以这里重新取一次
    game = require_draw_game(room)
    state = draw.public_state(game, user)

    return {
        'strokes': draw.strokes_since(game, as_int(data.get('sinceSeq'))),
        'seq': game.stroke_seq,
        'round': state['round'],
        'phase': state['phase'],
        'remainSeconds': state['remainSeconds'],
        'solvedUserIds': state['solvedUserIds'],
        'guesses': state['guesses'],
        'mySolved': state['mySolved'],
        'myWord': state['myWord'],
        'canPick': state['canPick'],
        'canDraw': state['canDraw'],
        'canGuess': state['canGuess'],
        'canAdvance': state['canAdvance'],
    }
