"""
谁是卧底 · 游戏规则。

这套规则和小程序端 mock/undercover.js 是同一套（那是开发期的本地模拟，正式运行时以这里为准）。
改动任何判定（卧底上限、平票、胜负条件）时，两处要保持一致。

关键点：下发给小程序的状态只包含「自己的词」—— 见 public_state()。
"""

import random

from .auth import ApiError
from .models import UndercoverGame, UndercoverPlayer, UndercoverWordPair


def max_undercover(player_count):
    """
    卧底人数上限：规则是「必须小于当前人数的一半」，即 2 * 卧底 < 人数。
    4 人最多 1 个、5 人最多 2 个、10 人最多 4 个。
    """
    if not player_count or player_count < 2:
        return 0
    return (player_count - 1) // 2


def alive_players(game):
    return [p for p in game.players.all() if p.alive]


def _players(game):
    return list(game.players.select_related('user').all())


# ---------------------------------------------------------------------------
# 开局
# ---------------------------------------------------------------------------

def start_game(room):
    """
    开局发牌：随机抽一组词，再随机决定谁拿卧底词。
    调用方负责把 room.status 置为 playing。
    """
    members = list(room.members.select_related('user').all())
    if not members:
        raise ApiError('房间里没有人')

    count = max(1, min(room.undercover_count or 1, max_undercover(len(members))))

    pairs = list(UndercoverWordPair.objects.all())
    if not pairs:
        raise ApiError('词库是空的，请先在服务器上执行 python manage.py seed_words')

    index = random.randrange(len(pairs))
    pair = pairs[index]

    # 同一组词，随机决定哪个当平民词 —— 换个局可能就反过来了
    words = [pair.word_a, pair.word_b]
    random.shuffle(words)
    civilian_word, undercover_word = words[0], words[1]

    game = UndercoverGame.objects.create(
        room=room,
        phase=UndercoverGame.PHASE_REVEAL,
        round_no=1,
        undercover_count=count,
        civilian_word=civilian_word,
        undercover_word=undercover_word,
        word_index=index,
        winner='',
        round_result=None,
    )

    undercover_ids = set(random.sample([m.user_id for m in members], count))
    UndercoverPlayer.objects.bulk_create([
        UndercoverPlayer(
            game=game,
            user_id=m.user_id,
            seat=m.seat,
            role=(
                UndercoverPlayer.ROLE_UNDERCOVER
                if m.user_id in undercover_ids
                else UndercoverPlayer.ROLE_CIVILIAN
            ),
            word=undercover_word if m.user_id in undercover_ids else civilian_word,
            alive=True,
            left=False,
        )
        for m in members
    ])

    return game


# ---------------------------------------------------------------------------
# 下发（关键：按请求者过滤）
# ---------------------------------------------------------------------------

def public_state(game, me):
    """
    生成给某个客户端看的状态。
    只带他自己的词和身份；别人的 word / role 一个字都不出现；
    投票只暴露「投没投」，不暴露投给谁（票型只在结算后的 roundResult 里公开）。
    """
    if game is None:
        return None

    players = _players(game)
    alive = [p for p in players if p.alive]
    me_player = next((p for p in players if p.user_id == me.id), None)
    voted_count = len([p for p in alive if p.vote_target_id])

    can_vote_now = bool(
        me_player
        and me_player.alive
        and not game.winner
        and game.phase != UndercoverGame.PHASE_RESULT
    )

    return {
        'phase': game.phase,
        'round': game.round_no,
        'undercoverCount': game.undercover_count,
        'playerCount': len(players),
        'aliveCount': len(alive),
        'votedCount': voted_count,
        'allVoted': bool(alive) and voted_count >= len(alive),

        'myUserId': me_player.user_id if me_player else None,
        'myRole': me_player.role if me_player else '',
        'myWord': me_player.word if me_player else '',
        'myAlive': bool(me_player and me_player.alive),
        'myVotedTarget': me_player.vote_target_id if me_player else None,
        'canVoteNow': can_vote_now,

        'players': [
            {
                'userId': p.user_id,
                'seat': p.seat,
                'nickname': p.user.nickname,
                'alive': p.alive,
                'left': p.left,
                'isMe': p.user_id == me.id,
                'voted': bool(p.vote_target_id),
                'canVote': bool(can_vote_now and p.alive and p.user_id != me.id),
            }
            for p in players
        ],

        'roundResult': game.round_result,
        'winner': game.winner or None,
    }


# ---------------------------------------------------------------------------
# 投票 / 结算 / 判定
# ---------------------------------------------------------------------------

def cast_vote(game, voter, target_user_id):
    """投票。所有人都投完后自动结算本轮。允许改票（覆盖上一次）。"""
    if game is None:
        raise ApiError('游戏还没开始')
    if game.phase == UndercoverGame.PHASE_OVER:
        raise ApiError('这局已经结束了')
    if game.phase == UndercoverGame.PHASE_RESULT:
        raise ApiError('本轮已经结算，等房主开下一轮')

    players = _players(game)

    voter_player = next((p for p in players if p.user_id == voter.id), None)
    if voter_player is None:
        raise ApiError('你不在这一局里')
    if not voter_player.alive:
        raise ApiError('你已经出局了，不能再投票')

    target = next((p for p in players if p.user_id == target_user_id), None)
    if target is None or not target.alive:
        raise ApiError('只能投给还在场上的玩家')
    if target.user_id == voter.id:
        raise ApiError('不能投自己')

    voter_player.vote_target_id = target_user_id
    voter_player.save(update_fields=['vote_target'])

    if game.phase == UndercoverGame.PHASE_REVEAL:
        game.phase = UndercoverGame.PHASE_VOTING

    alive = [p for p in players if p.alive]
    if alive and all(p.vote_target_id for p in alive):
        settle(game, players)
    else:
        game.save()

    return game


def settle(game, players=None):
    """本轮结算：票最多者出局；平票则本轮无人出局。"""
    if players is None:
        players = _players(game)

    alive = [p for p in players if p.alive]
    tally = {}
    for p in alive:
        if p.vote_target_id:
            tally[p.vote_target_id] = tally.get(p.vote_target_id, 0) + 1

    if tally:
        top_count = max(tally.values())
        top_ids = [uid for uid, n in tally.items() if n == top_count]
    else:
        top_ids = []

    rows = sorted(
        [
            {
                'userId': uid,
                'nickname': next((p.user.nickname for p in players if p.user_id == uid), '?'),
                'count': n,
            }
            for uid, n in tally.items()
        ],
        key=lambda row: -row['count'],
    )

    if len(top_ids) == 1:
        out = next(p for p in players if p.user_id == top_ids[0])
        out.alive = False
        out.save(update_fields=['alive'])
        game.round_result = {
            'tie': False,
            'outUserId': out.user_id,
            'outNickname': out.user.nickname,
            'outRole': out.role,
            'outWord': out.word,
            'tally': rows,
        }
    else:
        game.round_result = {
            'tie': True,
            'outUserId': None,
            'outNickname': '',
            'outRole': '',
            'outWord': '',
            'tally': rows,
        }

    game.phase = UndercoverGame.PHASE_RESULT
    check_winner(game, players)
    game.save()
    return game


def check_winner(game, players=None):
    """
    胜负判定：
      - 卧底全部出局 -> 平民胜
      - 卧底人数 >= 平民人数 -> 卧底胜
    """
    if players is None:
        players = _players(game)

    alive = [p for p in players if p.alive]
    undercover = len([p for p in alive if p.role == UndercoverPlayer.ROLE_UNDERCOVER])
    civilian = len(alive) - undercover

    if undercover == 0:
        game.winner = 'civilian'
    elif undercover >= civilian:
        game.winner = 'undercover'
    else:
        game.winner = ''

    if game.winner:
        game.phase = UndercoverGame.PHASE_OVER

    return game


def next_round(game):
    """房主开下一轮。"""
    if game is None:
        raise ApiError('游戏还没开始')
    if game.phase == UndercoverGame.PHASE_OVER:
        raise ApiError('这局已经结束了')
    if game.phase != UndercoverGame.PHASE_RESULT:
        raise ApiError('本轮还没投完票')

    game.round_no += 1
    game.round_result = None
    game.phase = UndercoverGame.PHASE_REVEAL
    game.save()

    game.players.update(vote_target=None)
    return game


def remove_player(game, user_id):
    """
    有人中途退出房间或被踢：把他标记成出局。
    否则「等所有人投完」会永远等不到他。
    """
    if game is None:
        return game

    players = _players(game)
    target = next((p for p in players if p.user_id == user_id), None)
    if target is None or not target.alive:
        return game

    target.alive = False
    target.left = True
    target.vote_target = None
    target.save(update_fields=['alive', 'left', 'vote_target'])

    alive = [p for p in players if p.alive]
    if alive and game.phase in (UndercoverGame.PHASE_REVEAL, UndercoverGame.PHASE_VOTING):
        if all(p.vote_target_id for p in alive):
            settle(game, players)
        else:
            check_winner(game, players)
            game.save()
    elif game.phase == UndercoverGame.PHASE_RESULT:
        check_winner(game, players)
        game.save()

    return game


# ---------------------------------------------------------------------------
# 人机（见 party/bots.py）
# ---------------------------------------------------------------------------

def bot_step(room, bots):
    """
    让房间里的机器人往前走一步（由 party/bots.py 在轮询时顺手调）。

    谁是卧底的机器人很省事：轮到投票就随便投一个还活着的人。
    """
    game = UndercoverGame.objects.filter(room=room).first()
    if game is None:
        return
    if game.phase in (UndercoverGame.PHASE_RESULT, UndercoverGame.PHASE_OVER):
        return      # 等房主开下一轮 / 整局已经结束了

    players = _players(game)

    for member in bots:
        player = next((p for p in players if p.user_id == member.user_id), None)
        # 出局的、已经投过的，都不用管
        if player is None or not player.alive or player.vote_target_id:
            continue

        if not member.may_bot_act():
            return

        candidates = [
            p.user_id for p in players
            if p.alive and p.user_id != member.user_id
        ]
        if not candidates:
            continue

        try:
            cast_vote(game, member.user, random.choice(candidates))
        except ApiError:
            pass
        return
