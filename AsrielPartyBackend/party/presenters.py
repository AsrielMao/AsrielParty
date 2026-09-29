"""
把模型转成小程序要的 JSON 结构。

字段名统一 camelCase，字段集必须和 AsrielParty/utils/api.js 的 decorateRoom 对齐
（小程序那边拿到数据后会再补 isSelf / isOwner 之类的派生字段）。
改这里要同步 docs/backend-api.md。
"""

from . import draw_guess as draw
from . import undercover as uc
from . import werewolf as ww
from .constants import DRAW_GUESS_GAME_ID, UNDERCOVER_GAME_ID, WEREWOLF_GAME_ID
from .models import DrawGame, UndercoverGame, WerewolfGame


def user_to_dict(user):
    if user is None:
        return None
    return {
        'id': user.id,
        'openid': user.openid,
        'unionid': user.unionid or None,
        'nickname': user.nickname,
        'avatar': user.avatar or '',
        'createdAt': int(user.created_at.timestamp() * 1000),
    }


def member_to_dict(member):
    return {
        'userId': member.user_id,
        # 实时语音靠 openid 匹配「谁在说话」，必须下发
        'openid': member.user.openid,
        'nickname': member.user.nickname,
        'avatar': member.user.avatar or '',
        'ready': member.ready,
        'seat': member.seat,
        'online': True,
        # 人机（后端自动驱动的那种），前端会在座位上标出来
        'isBot': bool(member.user.is_bot),
    }


def room_to_dict(room, me):
    """
    房间对象。

    gameState 一定要按 me 过滤 —— 卧底游戏里别人的词、狼人杀里别人的身份，
    都绝不能出现在这里。两个游戏的 public_state() 各自负责这件事。
    """
    members = list(room.members.select_related('user').all())

    game_state = None
    if room.game_id == UNDERCOVER_GAME_ID:
        game = UndercoverGame.objects.filter(room=room).first()
        if game is not None:
            game_state = uc.public_state(game, me)
    elif room.game_id == WEREWOLF_GAME_ID:
        game = WerewolfGame.objects.filter(room=room).first()
        if game is not None:
            game_state = ww.public_state(game, me)
    elif room.game_id == DRAW_GUESS_GAME_ID:
        game = DrawGame.objects.filter(room=room).first()
        if game is not None:
            game_state = draw.public_state(game, me)

    return {
        'id': room.id,
        'code': room.code,
        'title': room.title,
        'gameId': room.game_id,
        # 版型（狼人杀用），小程序据此决定房间布局和可打的标签
        'variantId': room.variant_id or '',
        'maxPlayers': room.max_players,
        'ownerId': room.owner_id,
        'status': room.status,
        'createdAt': int(room.created_at.timestamp() * 1000),
        'updatedAt': int(room.updated_at.timestamp() * 1000),
        'undercoverCount': room.undercover_count,
        'gameState': game_state,
        'members': [member_to_dict(m) for m in members],
    }
