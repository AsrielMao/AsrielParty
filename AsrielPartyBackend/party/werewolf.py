"""
狼人杀 · 规则与流程。

流程：夜晚 → 天亮宣布 →（警长竞选，可选）→ 发言 → 放逐投票 →［平票则 PK］→ 结算 → 夜晚…

规则判定全部在这里，小程序只负责展示和收集操作。

**可维护性约定**（以后加角色/版型时看这里）：
  - 角色定义、夜晚先后顺序、技能类型都在 constants.ROLES 里，这个文件不写死角色 id；
  - 夜晚按 `night.action` 分派，加一个**技能全新**的角色时，
    只要在 new_night_state()（怎么行动）和 settle_night()（怎么结算）各补一个分支；
  - 版型里 `sheriff: true` 才会有警长竞选，目前两个版型都是 false；
  - 各阶段时长来自版型的 timing，**只是提示**，到点不会自动推进。

已实现的技能：
  block  狼妃封锁（当晚受到的第一个技能反弹回去；弹过一次就永久失效）
  dream  觉醒摄梦（选梦语者 → 得知他是否行动过 → 第二晚起可让他出局）
  kill   狼人袭击（多人各选一个取多数；平票随机）
  guard  守卫守护（和袭击撞同一个人 = 平安夜）
  check  预言家查验（个人结果，立刻可见）
  grave  守墓人验尸（查上一个被放逐的人是好人还是狼）
  witch  女巫用药（解药救人 / 毒药毒人，各一次，不能救自己）
  猎人   死亡时开枪；但被女巫毒死、或被摄梦人带走时不能开枪

「行动过」的判定（觉醒摄梦人要读这个）见 acted_tonight()。
"""

import random

from django.utils import timezone

from .auth import ApiError
from .constants import (
    WEREWOLF_GAME_ID,
    find_variant,
    is_god,
    is_hidden_role,
    is_wolf,
    night_order,
    night_seconds,
    role_of,
    roles_of,
    timing_of,
    waits_for_pack,
)
from .models import WerewolfGame, WerewolfPlayer


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _players(game):
    return list(game.players.select_related('user').all())


def _alive(game):
    return [p for p in _players(game) if p.alive]


def _by_user(game, user_id):
    for p in _players(game):
        if p.user_id == user_id:
            return p
    return None


def _player_of(game, user):
    return _by_user(game, user.id)


def _seat_of(game, user_id):
    p = _by_user(game, user_id)
    return p.seat if p else None


def _nickname_of(game, user_id):
    p = _by_user(game, user_id)
    return p.user.nickname if p else '?'


def _variant_of(game):
    return find_variant(WEREWOLF_GAME_ID, game.variant_id)


def _reset_votes(game):
    """清空所有人的投票表态（每轮投票前调用）。"""
    for p in _players(game):
        if p.voted or p.vote_target_id:
            p.voted = False
            p.vote_target_id = None
            p.save(update_fields=['voted', 'vote_target'])


# ---------------------------------------------------------------------------
# 进程记录
#
# 牌桌中间那一栏显示的就是这些事件，**所有人看到的是同一份**。
# 所以这里只写台面上的东西（谁出局、票型、发言顺序），
# 身份和死因一律不进 —— 不然中间那栏就成了漏勺。
# ---------------------------------------------------------------------------

def _seat_name(game, user_id):
    """「3 号 小明」这种写法，进程记录里到处都要用。"""
    seat = _seat_of(game, user_id)
    nickname = _nickname_of(game, user_id) or '？'
    if seat is None:
        return nickname
    return '%d 号 %s' % (seat + 1, nickname)


def _log(game, kind, text, tone='info', **extra):
    """往进程记录里追加一条公开事件（注意：调用方还要自己 save）。"""
    log = list(game.event_log or [])
    event = {
        'id': len(log) + 1,
        'day': game.day or 0,
        'phase': game.phase,
        'kind': kind,
        'tone': tone,
        'text': text,
    }
    if extra:
        event.update(extra)
    log.append(event)
    game.event_log = log


def _ballots(game):
    """
    这一轮「谁投给了谁」。

    **只在公布票型的时候才用** —— 投票过程中任何人的票都不许被别人看到
    （见 public_state 里对 voted 的过滤）。
    """
    out = []
    for p in _voters(game):
        target = _by_user(game, p.vote_target_id) if p.vote_target_id else None
        out.append({
            'voterUserId': p.user_id,
            'voterSeat': p.seat,
            'voterNickname': p.user.nickname,
            'targetUserId': target.user_id if target else None,
            'targetSeat': target.seat if target else None,
            'targetNickname': target.user.nickname if target else '',
        })
    return out


def _log_vote(game, rows, ballots, out=None):
    """把这一轮的票型写进进程记录（公布之后才写，所以不怕被看见）。"""
    if out is not None:
        head = '%s 出局' % _seat_name(game, out.user_id)
    elif rows:
        head = '平票，本轮没人出局'
    else:
        head = '所有人都弃票，本轮没人出局'

    _log(
        game, 'vote',
        '第 %d 天 · 投票：%s' % (game.day, head),
        'vote',
        tally=rows,
        ballots=ballots,
    )


def _set_timer(game, seconds=None):
    """
    设置当前阶段的倒计时。
    **只是给玩家看的提示**：到点了也不会自动推进阶段。
    """
    if seconds is None:
        seconds = _phase_seconds(game)
    game.phase_started_at = timezone.now()
    game.phase_duration = max(0, int(seconds or 0))


def _phase_seconds(game):
    variant = _variant_of(game)
    phase = game.phase

    if phase == WerewolfGame.PHASE_NIGHT:
        step = current_step(game)
        if step:
            return night_seconds(variant, step['roleIds'][0] if step['roleIds'] else '')
        return timing_of(variant, 'night_default', 60)

    if phase in (WerewolfGame.PHASE_SPEECH, WerewolfGame.PHASE_PK_SPEECH):
        return timing_of(variant, 'speech', 120)

    if phase in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
        return timing_of(variant, 'vote', 60)

    return 0


# ---------------------------------------------------------------------------
# 身份的动态判定
# ---------------------------------------------------------------------------
# 隐狼是这套规则里唯一「身份会变」的角色：队友还活着时他在藏，
# 队友全出局后他就按普通狼人算。下面三个函数是所有判定的唯一入口。

def pack_alive(game, exclude_user_id=None):
    """还活着的「真狼」队友（隐狼不算 —— 他是不是狼要看情况）。"""
    return [
        p for p in _alive(game)
        if is_wolf(p.role_id)
        and not waits_for_pack(p.role_id)
        and p.user_id != exclude_user_id
    ]


def is_disguised(game, player):
    """
    隐狼现在还算不算「藏着的」。队友全出局后就变成普通狼人，伪装失效。
    对非隐狼永远返回 False。
    """
    if not is_hidden_role(player.role_id):
        return False
    return bool(pack_alive(game, exclude_user_id=player.user_id))


def attackers_of(game):
    """
    这一晚参与袭击的狼。
    隐狼在**还有狼队友活着**时不参与袭击（他只在队友全出局后才开始刀人）。
    """
    pack = pack_alive(game)
    out = list(pack)
    if not pack:
        # 队友全出局了：隐狼自己上（隐狼自己也出局的话这里就是空的）
        out.extend(p for p in _alive(game) if waits_for_pack(p.role_id))
    return out


def _check_result(game, target):
    """
    查验类技能看到的结果（预言家用的）。

    隐狼在伪装期间显示成好人；队友全出局后就按普通狼人显示。
    """
    if is_disguised(game, target):
        return 'good'
    return 'wolf' if is_wolf(target.role_id) else 'good'


def _grave_result(target):
    """
    守墓人看到的结果。

    注意和预言家不同：**隐狼被放逐后，守墓人当晚会明确得知他是狼人**，
    就算他还在伪装期间也一样 —— 这是守墓人的价值所在。
    """
    return 'wolf' if is_wolf(target.role_id) else 'good'


def _witch_used(night, user_id):
    """女巫这一晚有没有用药。不用药就不算「行动过」。"""
    for step in (night or {}).get('steps') or []:
        if step['action'] != 'witch':
            continue
        key = str(user_id)
        if key in (step.get('targets') or {}):
            return True
        if key in (step.get('acted') or []):
            return False
    return False


def _dream_target_of(game, user_id):
    """
    这一晚摄梦人挑好的梦语者（还没挑就是 None）。

    先看 `picked`（挑好了、还没确认「放不放逐」），再看 `targets`（已经确认的）——
    她挑完就该能读心，所以不能只等 `targets`。

    不去读 player.last_dream_target_id：那个是「不能连续两晚选同一个人」用的，
    拿它会让新的一夜显示成上一晚的目标。
    """
    night = game.night or {}
    for step in night.get('steps') or []:
        if step['action'] != 'dream':
            continue
        picked = (step.get('picked') or {}).get(str(user_id))
        if picked:
            return picked
        return (step.get('targets') or {}).get(str(user_id))
    return None


def _dream_actual_target(game, dreamer, picked_id):
    """
    摄梦人这一晚**实际**护住的是谁。

    平时就是她选的那个人；但如果那个人正好被狼妃封锁，而封锁这一晚还没被
    别的技能用掉，她的技能会弹回自己身上（见 settle_night 的 maybe_bounce）。

    规则上她**不知道自己选错了人**，只会读到「目标行动过」——
    所以这里返回她自己（摄梦人自己的角色本来就永远算「行动过」）。
    """
    if game.phase != WerewolfGame.PHASE_NIGHT:
        # 已经结算过了：last_dream_target_id 就是弹完之后的答案
        return dreamer.last_dream_target_id or picked_id

    # 还没结算：按封锁规则推演一下（真跑起来她是最后一步，提交即结算，
    # 这里是防御性分支 —— 万一以后有人把她的 order 调前了）
    night = game.night or {}
    blocked = night.get('blocked')
    if not blocked or picked_id != blocked:
        return picked_id

    # 排在她前面的技能（女巫的毒药）如果已经打在这个目标上，封锁就先用掉了
    for step in night.get('steps') or []:
        if step['action'] != 'witch':
            continue
        targets = step.get('targets') or {}
        for uid, skill in (step.get('skills') or {}).items():
            if skill == 'poison' and targets.get(uid) == blocked:
                return picked_id

    return dreamer.user_id


def acted_tonight(game, night, player):
    """
    这一晚这个人算不算「行动过」—— 觉醒摄梦人靠它读心。

      · 觉醒摄梦人   永远算行动过（她每晚必须选梦语者）
      · 守墓人       永远算行动过（哪怕白天没人被放逐）
      · 狼妃         算（她排在最前封锁）
      · 女巫         用了药才算
      · 隐狼         队友还活着时不算（他在藏）；队友全出局后算
      · 猎人 / 平民  不算（夜里本来就不动）
    """
    action = ((role_of(player.role_id) or {}).get('night') or {}).get('action', '')

    if action in ('dream', 'block', 'grave'):
        return True
    if action == 'witch':
        return _witch_used(night, player.user_id)
    if action == 'kill':
        return not is_disguised(game, player)
    return False


# ---------------------------------------------------------------------------
# 通用的多人决策
# ---------------------------------------------------------------------------

def _pick_collective(pairs):
    """
    多人共同决定的动作（狼刀、守护）：取被选最多的目标，平票随机取一个。
    pairs: [(行动者 userId, 目标 userId), ...]
    """
    if not pairs:
        return None

    tally = {}
    for _, target_id in pairs:
        tally[target_id] = tally.get(target_id, 0) + 1

    top = max(tally.values())
    winners = [target_id for target_id, count in tally.items() if count == top]
    return random.choice(winners)


def _step_pairs(step):
    """某个步骤里所有人的选择 [(actorId, targetId), ...]。"""
    return [(int(uid), tid) for uid, tid in (step.get('targets') or {}).items()]


def _sole_pair(step):
    """只有一个行动者的步骤，取他那一个选择。"""
    pairs = _step_pairs(step)
    return pairs[0] if pairs else (None, None)


def kill_target_of(game):
    """
    这一夜狼刀的目标。女巫要用它来决定救谁 —— 女巫排在狼人之后行动，
    所以她行动时这个结果已经能算出来了。
    """
    night = game.night or {}
    for step in night.get('steps') or []:
        if step['action'] == 'kill':
            return _pick_collective(_step_pairs(step))
    return None


def _apply_deaths(game, deaths, no_shoot_ids=()):
    """
    判这些人出局；返回「还等着开枪的猎人」的 userId（没有就是 None）。

    被女巫毒死、或被觉醒摄梦人带走的猎人**不能开枪**，
    所以要靠 no_shoot_ids 把这两种死因排除掉。
    """
    pending_shooter = None

    for user_id in deaths:
        victim = _by_user(game, user_id)
        if victim is None or not victim.alive:
            continue

        victim.alive = False
        victim.save(update_fields=['alive'])

        if victim.role_id == 'hunter' and user_id not in no_shoot_ids and not victim.shot_used:
            pending_shooter = victim.user_id

    return pending_shooter


# ---------------------------------------------------------------------------
# 开局
# ---------------------------------------------------------------------------

def start_game(room):
    """
    开局发牌：按版型的角色配置随机分配身份，然后进入第一个夜晚。
    调用方负责把 room.status 置为 playing。
    """
    variant = find_variant(WEREWOLF_GAME_ID, room.variant_id)
    if variant is None:
        raise ApiError('请先选择一个版型')

    members = list(room.members.select_related('user').order_by('seat'))
    if not members:
        raise ApiError('房间里没有人')

    # 先把身份池铺开（1 摄梦人 + 1 女巫 + ...），再洗牌
    pool = []
    for role in roles_of(variant):
        pool.extend([role['id']] * int(role.get('count', 1)))

    if len(pool) != len(members):
        raise ApiError(
            '版型配置和房间人数对不上：版型要 %d 个角色，房间里有 %d 个人'
            % (len(pool), len(members))
        )

    random.shuffle(pool)

    game = WerewolfGame.objects.create(
        room=room,
        variant_id=variant['id'],
        phase=WerewolfGame.PHASE_NIGHT,
        day=0,
        night={},
        last_night_deaths=[],
        speech_order=[],
        speech_index=0,
        pk_user_ids=[],
        round_result=None,
        winner='',
        win_reason='',
        last_lynched_user_id=None,
        pending_shooter_id=None,
    )

    WerewolfPlayer.objects.bulk_create([
        WerewolfPlayer(
            game=game,
            user=member.user,
            seat=member.seat,
            role_id=pool[index],
            alive=True,
        )
        for index, member in enumerate(members)
    ])

    return start_night(game)


# ---------------------------------------------------------------------------
# 夜晚
# ---------------------------------------------------------------------------

def _actors_for(game, role, alive):
    """
    这一步该谁睁眼。

    大多数角色就是「版型里有几个就几个」，但袭击步骤要特殊处理：
    隐狼在还有队友活着的时候**不参与袭击**（他在藏）。
    """
    action = role['night']['action']
    out = []

    for player in alive:
        if player.role_id != role['id']:
            continue
        if action == 'kill' and waits_for_pack(player.role_id):
            if pack_alive(game, exclude_user_id=player.user_id):
                continue
        out.append(player.user_id)

    return out


def new_night_state(game):
    """
    生成这一夜的行动步骤。

    哪些角色要行动、先后顺序，全部由 constants 里的定义推导出来：

        6 人新手场：预言家 → 守卫 → 狼人
        7 人幸存者：狼妃(封锁) → 狼人(袭击) → 女巫 → 守墓人 → 觉醒摄梦人

    同一个 action 的角色会被合并到同一步 —— 所以几只狼是一起睁眼的。
    """
    variant = _variant_of(game)
    alive = _alive(game)
    attackers = attackers_of(game)

    steps = []
    by_action = {}

    for role in night_order(variant):
        action = role['night']['action']

        if action == 'kill':
            # 袭击步骤由「今晚谁参与袭击」决定，而不是版型里写了哪些狼角色 ——
            # 狼妃的 action 是 block（她要先封锁），但她同样参与这一刀的商量；
            # 隐狼在队友活着时不参与。所以这里不能用 roleIds 过滤。
            if 'kill' in by_action:
                continue
            actor_ids = [p.user_id for p in attackers]
            role_ids = sorted({p.role_id for p in attackers})
            label = role['night']['label']
        else:
            actor_ids = _actors_for(game, role, alive)
            role_ids = [role['id']]
            label = role['night']['label']

        if not actor_ids:
            continue

        if action in by_action:
            step = by_action[action]
            step['actorIds'].extend(actor_ids)
            for rid in role_ids:
                if rid not in step['roleIds']:
                    step['roleIds'].append(rid)
            continue

        step = {
            # 这一步涉及的角色（可能多个，比如两只狼）
            'roleIds': role_ids,
            'action': action,
            'label': label,
            'actorIds': actor_ids,
            # 已经表过态的人（跳过也算），用来判断这一步做完没有
            'acted': [],
            # { 行动者 userId : 目标 userId }；跳过的人不在这里
            'targets': {},
            # 觉醒摄梦人专用：已经挑好、但还没确认「放不放逐」的目标。
            # 她要先看到读心结果再决定，所以「挑完」不算表态，先记在这儿。
            'picked': {},
            # 女巫这一步：{ userId: 'save' | 'poison' }，用来区分用没用过药
            'skills': {},
            # 摄梦人这一步：今晚要不要直接让梦语者出局
            'slay': False,
        }
        by_action[action] = step
        steps.append(step)

    return {
        'steps': steps,
        'index': 0,
        # 狼妃这一晚封锁了谁 / 是谁封的 / 有没有被弹到
        'blocked': None,
        'blockOwner': None,
        'blockBroken': False,
    }


def current_step(game):
    """当前该谁行动；夜晚结束或不是夜晚时返回 None。"""
    night = game.night or {}
    steps = night.get('steps') or []
    index = night.get('index', 0)
    if index < 0 or index >= len(steps):
        return None
    return steps[index]


def start_night(game):
    """进入夜晚：生成步骤、把上一夜的行动痕迹清掉。"""
    for p in _players(game):
        fields = []
        if p.acted or p.night_target_id:
            p.acted = False
            p.night_target_id = None
            fields.extend(['acted', 'night_target'])
        if fields:
            p.save(update_fields=fields)

    game.phase = WerewolfGame.PHASE_NIGHT
    game.night = new_night_state(game)
    game.last_night_deaths = []
    game.speech_order = []
    game.speech_index = 0
    game.pk_user_ids = []
    game.round_result = None
    game.pending_shooter_id = None
    _reset_votes(game)
    _log(game, 'night', '第 %d 夜' % ((game.day or 0) + 1), 'night')
    _set_timer(game)
    game.save()
    return game


def _mark_acted(game, step, me, target, skill=''):
    """把「我这一步表过态了」写进 night（跳过也算表态）。"""
    if me.user_id not in step['acted']:
        step['acted'].append(me.user_id)
    if target is not None:
        step['targets'][str(me.user_id)] = target.user_id
    if skill:
        step['skills'][str(me.user_id)] = skill

    me.acted = True
    me.night_target_id = target.user_id if target else None
    me.save()

    night = game.night or {}
    for item in night.get('steps') or []:
        if item['action'] == step['action']:
            item['acted'] = step['acted']
            item['targets'] = step['targets']
            item['skills'] = step.get('skills') or {}
            item['slay'] = step.get('slay', False)
            break
    game.night = night


def _pick_target(game, me, target_id, action_label):
    """通用的「选一个还在场上的人」校验。"""
    if not target_id:
        raise ApiError('请选择一名玩家')

    target = _by_user(game, target_id)
    if target is None or not target.alive:
        raise ApiError('只能选还在场上的玩家')
    return target


def act(game, user, target_id=None, skill=''):
    """
    提交一个动作。同一个入口扛下所有：

      - 夜晚行动（封锁 / 摄梦 / 袭击 / 守护 / 查验 / 用药 / 验尸）
      - 猎人出局后的开枪

    夜晚只能在自己被唤醒的那一步操作；这一步上的人都表态完就自动进下一步，
    所有步骤都完成就结算夜晚、进入天亮。

    `skill` 用来区分同一个角色的不同用法：
      witch: 'save' 解药 / 'poison' 毒药 / 空 = 这晚不用药
      dream: 'slay' 直接让梦语者出局 / 空 = 只摄梦
    """
    me = _player_of(game, user)
    if me is None:
        raise ApiError('你不在这一局里')

    # ---- 猎人开枪：不占夜晚步骤，轮到他就能选 ----
    if game.pending_shooter_id:
        if me.user_id != game.pending_shooter_id:
            raise ApiError('现在等 %s 号开枪' % _seat_of(game, game.pending_shooter_id))
        return _shoot(game, me, target_id)

    if game.phase != WerewolfGame.PHASE_NIGHT:
        raise ApiError('现在不是夜晚')

    step = current_step(game)
    if step is None:
        raise ApiError('夜晚已经结束了')

    if not me.alive:
        raise ApiError('你已经出局了')

    if me.user_id not in step['actorIds']:
        names = '、'.join(
            (role_of(rid) or {}).get('name', rid) for rid in step['roleIds']
        )
        raise ApiError('现在轮到「%s」行动，请等一下' % names)

    action = step['action']
    target = None

    # ---- 狼妃封锁：排在最前，见队友之前 ----
    if action == 'block':
        if me.block_broken_day is not None:
            # 已经弹过一次，封锁永久失效 —— 表个态跳过就行
            pass
        else:
            target = _pick_target(game, me, target_id, '封锁')
            night = game.night or {}
            night['blocked'] = target.user_id
            night['blockOwner'] = me.user_id
            game.night = night

    # ---- 觉醒摄梦人：分两步走
    #   1) 先定梦语者 —— 定完她当场就能读出对方今晚动没动；
    #   2) 再决定要不要直接让他出局。
    # 所以第一次提交**不算表态**，只把目标记在 picked 里等她确认。
    elif action == 'dream':
        picked_id = (step.get('picked') or {}).get(str(me.user_id))

        if picked_id is None:
            target = _pick_target(game, me, target_id, '摄梦')
            # 可以选自己（相当于自保：自己当晚免疫别人给的伤害）
            if me.last_dream_target_id == target.user_id:
                raise ApiError('不能连续两晚选同一个人')
            if skill == 'slay' and (game.day or 0) < 1:
                raise ApiError('第一晚还不能让梦语者出局')

            night = game.night or {}
            for item in night.get('steps') or []:
                if item['action'] == 'dream':
                    item.setdefault('picked', {})[str(me.user_id)] = target.user_id
                    break
            game.night = night
            game.save()
            return game

        # 第二次提交：确认（可以顺便让他出局）
        target = _by_user(game, picked_id)
        if target is None or not target.alive:
            raise ApiError('选的那个人已经不在场上了')
        if skill == 'slay':
            if (game.day or 0) < 1:
                raise ApiError('第一晚还不能让梦语者出局')
            step['slay'] = True

    # ---- 守墓人：查的是上一个被放逐的人，不用自己选 ----
    elif action == 'grave':
        lynched_id = game.last_lynched_user_id
        lynched = _by_user(game, lynched_id) if lynched_id else None
        if lynched is not None:
            checks = dict(me.checks or {})
            checks[str(lynched.user_id)] = _grave_result(lynched)
            me.checks = checks
        # 白天没人被放逐也照样算「行动过」，这里什么都不做即可

    # ---- 女巫用药 ----
    elif action == 'witch':
        if skill == 'save':
            if me.heal_used:
                raise ApiError('解药已经用过了')
            wolf_target = kill_target_of(game)
            if not wolf_target:
                raise ApiError('今晚没有人被袭击，不用救')
            if wolf_target == me.user_id:
                raise ApiError('这个版型里解药不能救自己')
            target = _by_user(game, wolf_target)
            me.heal_used = True
        elif skill == 'poison':
            if me.poison_used:
                raise ApiError('毒药已经用过了')
            target = _pick_target(game, me, target_id, '下毒')
            me.poison_used = True
        else:
            # 不用药。注意：不用药会被觉醒摄梦人读成「没行动过」
            skill = ''

    # ---- 其它夜间技能：都要选一个目标 ----
    else:
        target = _pick_target(game, me, target_id, '行动')

        if action in ('check', 'grave') and target.user_id == me.user_id:
            raise ApiError('不能查验自己')

        if action == 'guard':
            if target.user_id == me.user_id:
                raise ApiError('不能守自己')
            if me.last_guard_target_id == target.user_id:
                raise ApiError('不能连续两晚守同一个人')

        if action == 'kill' and is_wolf(me.role_id) and is_wolf(target.role_id):
            raise ApiError('不能袭击自己的同伴')

        # 查验类技能**立刻出结果**：提交完就要看到「他是好人还是狼人」，
        # 不用等到天亮。这只是写进他自己的记录，别人依旧看不到。
        if action == 'check':
            checks = dict(me.checks or {})
            checks[str(target.user_id)] = _check_result(game, target)
            me.checks = checks

    _mark_acted(game, step, me, target, skill)

    # 这一步的人（还在场上的）都表态完了 -> 下一步
    pending = [
        uid for uid in step['actorIds']
        if (actor := _by_user(game, uid)) is not None and actor.alive
    ]

    if all(uid in step['acted'] for uid in pending):
        night = game.night or {}
        night['index'] = night.get('index', 0) + 1
        game.night = night

        if night['index'] >= len(night['steps']):
            # 全部行动完 -> 结算，天亮
            game.save()
            return settle_night(game)

        _set_timer(game)

    game.save()
    return game


def settle_night(game):
    """
    夜晚结算。顺序很讲究，因为几个技能会互相影响 ——
    **这里严格按各角色实际的行动顺序处理**（狼妃 → 狼刀 → 守卫 → 女巫 → 摄梦人），
    因为「封锁只吃当晚遇到的第一个技能」，谁先处理谁先被弹：

      1. 狼妃的封锁 —— 决定谁被「反弹」（弹成功过一次就永久失效）
      2. 狼刀       —— 集体行为，不参与反弹
      3. 守卫守护   —— 和狼刀撞同一个人 = 平安夜
      4. 女巫       —— 解药救人 / 毒药毒人（毒药可能被弹回女巫自己身上）
      5. 觉醒摄梦人 —— 选出梦语者（可能因为封锁而变成摄梦人自己）
      6. 汇总伤害   —— 梦语者免疫「除摄梦人以外」的一切伤害
      7. 判死亡     —— 被毒死、被摄梦人带走的猎人不能开枪

    所以「女巫的毒药」和「摄梦人的梦语者」选了同一个人时，先弹的是毒药
    （弹回女巫自己），摄梦人的技能随后照常作用在那个目标身上。
    """
    night = game.night or {}
    steps = night.get('steps') or []

    collected = {}
    for step in steps:
        collected.setdefault(step['action'], []).extend(_step_pairs(step))

    def step_of(action):
        for item in steps:
            if item['action'] == action:
                return item
        return None

    # ---- 1. 狼妃封锁：谁被弹 ----
    blocked_id = night.get('blocked')
    block_owner_id = night.get('blockOwner')
    block_broken = False

    def maybe_bounce(actor_id, target_id):
        """
        如果目标是狼妃封锁的人，并且这晚还没弹过，就把技能弹回施法者。
        （封锁只吃「第一个」技能，所以弹过一次就作废。）
        """
        nonlocal block_broken
        if not blocked_id or block_broken or target_id is None:
            return target_id
        if target_id != blocked_id:
            return target_id
        block_broken = True
        return actor_id

    # ---- 2. 狼刀 ----
    wolf_target = _pick_collective(collected.get('kill'))

    # ---- 3. 守卫守护 ----
    guard_step = step_of('guard')
    guard_target = _pick_collective(collected.get('guard'))
    if guard_target:
        for actor_id, _ in collected.get('guard', []):
            guard = _by_user(game, actor_id)
            if guard is not None:
                guard.last_guard_target_id = guard_target
                guard.save(update_fields=['last_guard_target_id'])
    elif guard_step is not None:
        pass

    # ---- 4. 女巫（排在摄梦人前面：两个技能撞同一个人时，先弹的该是毒药）----
    witch_step = step_of('witch')
    witch_actor_id, witch_target_id = _sole_pair(witch_step) if witch_step else (None, None)
    witch_skill = ''
    if witch_step is not None and witch_actor_id is not None:
        witch_skill = (witch_step.get('skills') or {}).get(str(witch_actor_id), '')

    healed = False
    poisoned_ids = []
    if witch_skill == 'save' and wolf_target:
        healed = True
    elif witch_skill == 'poison' and witch_target_id is not None:
        # 毒药也会被封锁弹回自己身上
        poisoned_ids.append(maybe_bounce(witch_actor_id, witch_target_id))

    # ---- 5. 觉醒摄梦人 ----
    dream_step = step_of('dream')
    dream_actor_id, dream_target_id = _sole_pair(dream_step) if dream_step else (None, None)
    if dream_target_id is not None:
        dream_target_id = maybe_bounce(dream_actor_id, dream_target_id)
        dreamer = _by_user(game, dream_actor_id)
        if dreamer is not None:
            dreamer.last_dream_target_id = dream_target_id
            dreamer.save(update_fields=['last_dream_target_id'])

    # ---- 6. 汇总伤害 ----
    # 梦语者免疫「除觉醒摄梦人以外」的一切伤害
    protected = set()
    if dream_target_id is not None:
        protected.add(dream_target_id)
    if guard_target is not None:
        protected.add(guard_target)

    deaths = []

    if wolf_target and wolf_target not in protected and not healed:
        deaths.append(wolf_target)

    for user_id in poisoned_ids:
        if user_id is not None and user_id not in protected and user_id not in deaths:
            deaths.append(user_id)

    # 觉醒摄梦人让梦语者出局：无视一切保护（这是她自己的技能）。
    # 注意 dream_target_id 已经被封锁逻辑处理过 —— 如果被弹，这里就是摄梦人自己。
    if dream_step is not None and dream_step.get('slay') and dream_target_id is not None:
        if dream_target_id not in deaths:
            deaths.append(dream_target_id)

    # 被毒死、被摄梦人带走的猎人不能开枪
    no_shoot = set(poisoned_ids)
    if dream_step is not None and dream_step.get('slay'):
        no_shoot.add(dream_target_id)

    pending_shooter = _apply_deaths(game, deaths, no_shoot_ids=no_shoot)
    if pending_shooter:
        game.pending_shooter_id = pending_shooter

    # ---- 7. 狼妃技能失效的记录 ----
    if block_broken and block_owner_id:
        owner = _by_user(game, block_owner_id)
        if owner is not None and owner.block_broken_day is None:
            owner.block_broken_day = game.day or 0
            owner.save(update_fields=['block_broken_day'])

    night['blockBroken'] = block_broken
    game.night = night
    game.last_night_deaths = deaths
    game.day = (game.day or 0) + 1
    game.phase = WerewolfGame.PHASE_DAYBREAK
    game.round_result = None

    # 天亮的公开信息只有「谁出局」——**不说死因**：
    # 谁挨的刀、谁中的毒、谁被摄梦人带走，都是夜里的事。
    if deaths:
        _log(
            game, 'daybreak',
            '第 %d 天 · 昨夜出局：%s' % (
                game.day, '、'.join(_seat_name(game, uid) for uid in deaths)
            ),
            'death',
        )
    else:
        _log(game, 'daybreak', '第 %d 天 · 昨夜是平安夜' % game.day, 'calm')

    _set_timer(game)
    game.save()

    # 夜里可能就分出胜负了（比如狼刀掉唯一的平民）
    return check_winner(game)


def _shoot(game, hunter, target_id):
    """猎人开枪带走一个人。"""
    if hunter.shot_used:
        raise ApiError('已经开过枪了')

    if not target_id:
        # 不开枪（放弃）
        hunter.shot_used = True
        hunter.save(update_fields=['shot_used'])
        game.pending_shooter_id = None
        _log(game, 'shoot', '%s 放弃了开枪' % _seat_name(game, hunter.user_id), 'info')
        game.save()
        return game

    target = _by_user(game, target_id)
    if target is None or not target.alive:
        raise ApiError('只能带走还在场上的玩家')
    if target.user_id == hunter.user_id:
        raise ApiError('不能带走自己')

    hunter.shot_used = True
    hunter.save(update_fields=['shot_used'])

    # 枪也可能打到另一个猎人（版型允许的话），所以照样走 _apply_deaths
    pending = _apply_deaths(game, [target.user_id])
    game.pending_shooter_id = pending
    _log(
        game, 'shoot',
        '%s 开枪带走了 %s' % (
            _seat_name(game, hunter.user_id), _seat_name(game, target.user_id)
        ),
        'death',
    )
    game.save()

    return check_winner(game)


# ---------------------------------------------------------------------------
# 白天
# ---------------------------------------------------------------------------

def start_speech(game):
    """
    开始发言：随机挑一个活着的玩家起头，再随机顺时针 / 逆时针。
    （有警长时由警长决定顺序 —— 这版还没有警长，留好口子。）
    """
    alive = _alive(game)
    if len(alive) < 1:
        return check_winner(game)

    start = random.randrange(len(alive))
    clockwise = random.random() < 0.5
    total = len(alive)

    order = []
    for i in range(total):
        index = (start + i) % total if clockwise else (start - i) % total
        order.append(alive[index].user_id)

    game.speech_order = order
    game.speech_index = 0
    game.phase = WerewolfGame.PHASE_SPEECH
    _log(
        game, 'speech',
        '第 %d 天 · 发言顺序（%s）：%s' % (
            game.day,
            '座位从小到大' if clockwise else '座位从大到小',
            ' → '.join('%d' % ((_seat_of(game, uid) or 0) + 1) for uid in order),
        ),
        'info',
    )
    _set_timer(game)
    game.save()
    return game


def start_vote(game):
    """进入放逐投票。"""
    game.phase = WerewolfGame.PHASE_VOTE
    game.pk_user_ids = []
    _reset_votes(game)
    _set_timer(game)
    game.save()
    return game


def _voters(game):
    """这一轮谁能投票。PK 投票时 PK 玩家本人不投。"""
    voters = _alive(game)
    if game.phase == WerewolfGame.PHASE_PK_VOTE:
        pk = set(game.pk_user_ids or [])
        voters = [p for p in voters if p.user_id not in pk]
    return voters


def _all_voted(game):
    voters = _voters(game)
    return bool(voters) and all(p.voted for p in voters)


def cast_vote(game, user, target_id):
    """
    放逐投票 / PK 投票。**允许弃票**（target_id 为空即弃票），弃票也算「已表态」。
    """
    if game.phase not in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
        raise ApiError('现在不是投票时间')

    me = _player_of(game, user)
    if me is None:
        raise ApiError('你不在这一局里')
    if not me.alive:
        raise ApiError('你已经出局了，不能再投票')

    if game.phase == WerewolfGame.PHASE_PK_VOTE:
        pk = set(game.pk_user_ids or [])
        if me.user_id in pk:
            raise ApiError('PK 的玩家这一轮不投票')
        if target_id and target_id not in pk:
            raise ApiError('这一轮只能投给参与 PK 的玩家')

    target = None
    if target_id:
        target = _by_user(game, target_id)
        if target is None or not target.alive:
            raise ApiError('只能投给还在场上的玩家')
        # 允许投自己（票是自己的，想投给谁都行）

    me.vote_target_id = target.user_id if target else None
    me.voted = True
    me.save(update_fields=['voted', 'vote_target'])

    if _all_voted(game):
        return settle_vote(game)

    game.save()
    return game


def _tally(game):
    """计票（弃票不计），返回 (票数表, 最高票的 userId 列表)。"""
    tally = {}
    for p in _alive(game):
        if p.vote_target_id:
            tally[p.vote_target_id] = tally.get(p.vote_target_id, 0) + 1

    if not tally:
        return {}, []

    top = max(tally.values())
    return tally, [user_id for user_id, count in tally.items() if count == top]


def settle_vote(game):
    """统计这一轮投票：票最多的人出局；平票进入 PK；PK 再平票则无人出局。"""
    tally, tops = _tally(game)

    # 票数表
    rows = sorted(
        [
            {
                'userId': user_id,
                'seat': _seat_of(game, user_id),
                'nickname': _nickname_of(game, user_id),
                'count': count,
            }
            for user_id, count in tally.items()
        ],
        key=lambda row: -row['count'],
    )

    # 票型明细：**只在这一刻生成**。投票进行中后端不下发任何人的票
    # （见 public_state 里对 voted 的过滤），公布的时候才一次性给出来。
    ballots = _ballots(game)

    # 全部弃票
    if not tops:
        return _no_one_out(game, rows, '所有人都弃票，本轮没人出局', ballots)

    # 平票
    if len(tops) > 1:
        if game.phase == WerewolfGame.PHASE_PK_VOTE:
            return _no_one_out(game, rows, 'PK 投票还是平票，本轮没人出局', ballots)

        # 平票也要先公布票型：大家得知道谁投了谁，才好进入 PK 发言
        game.round_result = _tie_result(rows, ballots, tops, '平票，进入 PK 发言')
        _log_vote(game, rows, ballots)
        game.save()
        return start_pk_speech(game, tops)

    # 出局
    out = _by_user(game, tops[0])
    # 记下来，守墓人晚上要验他
    game.last_lynched_user_id = out.user_id

    pending_shooter = _apply_deaths(game, [out.user_id])
    if pending_shooter:
        game.pending_shooter_id = pending_shooter

    game.round_result = {
        'tie': False,
        'outUserId': out.user_id,
        'outSeat': out.seat,
        'outNickname': out.user.nickname,
        'outRoleId': out.role_id,
        'tally': rows,
        'ballots': ballots,
        'note': '出局，请留遗言',
    }
    _log_vote(game, rows, ballots, out=out)
    game.phase = WerewolfGame.PHASE_RESULT
    _set_timer(game)
    game.save()

    return check_winner(game)


def _tie_result(rows, ballots, pk_ids, note):
    """平票 / 无人出局的 round_result —— 票数和票型明细都是公开的。"""
    return {
        'tie': True,
        'outUserId': None,
        'outSeat': None,
        'outNickname': '',
        'outRoleId': '',
        'tally': rows,
        'ballots': ballots,
        'pkUserIds': list(pk_ids),
        'note': note,
    }


def _no_one_out(game, rows, note, ballots=None):
    ballots = ballots or []
    game.round_result = _tie_result(rows, ballots, [], note)
    _log_vote(game, rows, ballots)
    game.phase = WerewolfGame.PHASE_RESULT
    _set_timer(game)
    game.save()
    return check_winner(game)


def start_pk_speech(game, user_ids):
    """平票：这几个玩家依次做一轮 PK 发言。"""
    game.pk_user_ids = list(user_ids)
    game.speech_order = list(user_ids)
    game.speech_index = 0
    game.phase = WerewolfGame.PHASE_PK_SPEECH
    _set_timer(game)
    game.save()
    return game


def start_pk_vote(game):
    """PK 发言完，其余玩家再投一轮（只能投 PK 玩家，也可以弃票）。"""
    game.phase = WerewolfGame.PHASE_PK_VOTE
    _reset_votes(game)
    _set_timer(game)
    game.save()
    return game


# ---------------------------------------------------------------------------
# 胜负
# ---------------------------------------------------------------------------

def check_winner(game):
    """
    屠边规则：
      - 狼人全部出局 -> 好人胜
      - 神职全部出局（且开局有神）-> 狼人胜
      - 平民全部出局（且开局有民）-> 狼人胜

    「7 人最后幸存者」里只有 1 个平民，所以**平民一出局狼人就直接获胜** ——
    这是版型设计（核心玩法就是平民要藏好、神职必要时装平民挡刀），不是 bug。
    """
    if game.winner:
        return game

    alive = _alive(game)
    wolves = [p for p in alive if is_wolf(p.role_id)]
    gods = [p for p in alive if is_god(p.role_id)]
    villagers = [p for p in alive if not is_wolf(p.role_id) and not is_god(p.role_id)]

    variant = _variant_of(game)
    has_gods = any(is_god(r['id']) for r in roles_of(variant))
    has_villagers = any(r['id'] == 'villager' for r in roles_of(variant))

    if not wolves:
        game.winner = 'good'
        game.win_reason = '狼人全部出局，好人获胜'
    elif has_gods and not gods:
        game.winner = 'wolf'
        game.win_reason = '神职全部出局，狼人获胜'
    elif has_villagers and not villagers:
        game.winner = 'wolf'
        game.win_reason = '平民全部出局，狼人获胜'
    else:
        return game

    game.phase = WerewolfGame.PHASE_OVER
    game.pending_shooter_id = None
    game.phase_duration = 0
    game.phase_started_at = None
    _log(
        game, 'over',
        '%s —— %s' % (
            '好人阵营获胜' if game.winner == 'good' else '狼人阵营获胜',
            game.win_reason,
        ),
        'good' if game.winner == 'good' else 'wolf',
    )
    game.save()
    return game


# ---------------------------------------------------------------------------
# 阶段推进
# ---------------------------------------------------------------------------

def advance(game, user):
    """
    推进当前阶段。

    **由「当前该操作的人」决定，不需要房主**：

        天亮宣布  -> 谁点一下都能往下走（大家看完死讯就开始发言）
        发言      -> 只有当前发言人能点「我说完了」
        PK 发言   -> 同上，只有当前 PK 玩家能点
        结算      -> 谁点一下都能进入下一夜（但如果有猎人等着开枪，只能由他自己放弃）
        夜晚      -> 不用点：轮到的人行动完会自动走下一步
        投票      -> 不用点：所有人表态完（含弃票）会自动结算

    发言顺序里如果有人已经出局或退出，会自动跳过。
    """
    phase = game.phase
    me = _player_of(game, user)

    # 有猎人等着开枪时，先让他自己决定（开枪或放弃）
    if game.pending_shooter_id:
        if me is None or me.user_id != game.pending_shooter_id:
            raise ApiError('现在等 %s 号开枪' % _seat_of(game, game.pending_shooter_id))
        return _shoot(game, me, None)

    if phase == WerewolfGame.PHASE_NIGHT:
        step = current_step(game)
        if step is None:
            return settle_night(game)
        names = '、'.join(
            (role_of(rid) or {}).get('name', rid) for rid in step['roleIds']
        )
        raise ApiError('现在是夜晚，等「%s」行动完' % names)

    if phase in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
        raise ApiError('投票阶段不用推进，所有人投完会自动结算')

    if phase == WerewolfGame.PHASE_DAYBREAK:
        # 有警长竞选的版型先走竞选那一步（目前两个版型都没有）
        variant = _variant_of(game)
        if variant and variant.get('sheriff'):
            game.phase = WerewolfGame.PHASE_SHERIFF
            _set_timer(game)
            game.save()
            return game
        return start_speech(game)

    if phase == WerewolfGame.PHASE_SHERIFF:
        # 警长竞选的流程还没做，先直接跳过
        return start_speech(game)

    if phase in (WerewolfGame.PHASE_SPEECH, WerewolfGame.PHASE_PK_SPEECH):
        order = game.speech_order or []
        index = game.speech_index or 0

        if index < len(order):
            current_uid = order[index]
            if me is None or me.user_id != current_uid:
                raise ApiError(
                    '现在轮到 %s 号发言，等他点「说完了」' % _seat_of(game, current_uid)
                )

        # 往后找下一个还在场上的人
        index += 1
        while index < len(order):
            pending = _by_user(game, order[index])
            if pending is not None and pending.alive:
                break
            index += 1

        game.speech_index = index

        if index >= len(order):
            if phase == WerewolfGame.PHASE_PK_SPEECH:
                return start_pk_vote(game)
            return start_vote(game)

        _set_timer(game)
        game.save()
        return game

    if phase == WerewolfGame.PHASE_RESULT:
        return start_night(game)

    raise ApiError('现在没什么可以推进的')


def remove_player(game, user_id):
    """
    有人退出房间或被踢：直接判他出局。
    否则发言顺序和投票都会卡在他身上（「等所有人投完」永远等不到他）。
    """
    if game is None:
        return game

    player = _by_user(game, user_id)
    if player is None or not player.alive:
        return game

    player.alive = False
    player.left = True
    player.save(update_fields=['alive', 'left'])

    return check_winner(game)


# ---------------------------------------------------------------------------
# 下发（关键：按角色过滤）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 人机（见 party/bots.py）
# ---------------------------------------------------------------------------

def _bot_candidates(game, step, actor_id):
    """
    这一步机器人能选谁（随机顺序，让每次挑的不一样）。

    这里只是「候选」，最终还得过 act() 的校验 —— 挑错了就换下一个。
    """
    actor = _by_user(game, actor_id)
    alive = [p.user_id for p in _alive(game)]
    random.shuffle(alive)

    action = step['action'] if step else 'shoot'

    out = []
    for uid in alive:
        if uid == actor_id:
            continue
        other = _by_user(game, uid)
        if other is None or not other.alive:
            continue
        if action == 'dream' and uid == actor.last_dream_target_id:
            continue
        if action == 'guard' and uid == actor.last_guard_target_id:
            continue
        if action == 'kill' and is_wolf(other.role_id):
            continue
        out.append(uid)
    return out


def _bot_act(game, user, step, candidates):
    """机器人替自己做这一步：挨个试候选目标，成了就停。"""
    action = step['action'] if step else 'shoot'
    me = _by_user(game, user.id)

    if action == 'witch':
        # 偶尔救人、偶尔下毒，多数时候不用药
        if me is not None and not me.heal_used and random.random() < 0.35:
            try:
                return act(game, user, None, 'save')
            except ApiError:
                pass
        if me is not None and not me.poison_used and random.random() < 0.35:
            for target_id in candidates:
                try:
                    return act(game, user, target_id, 'poison')
                except ApiError:
                    continue
        try:
            return act(game, user, None, '')
        except ApiError:
            return None

    if action == 'grave':
        try:
            return act(game, user, None)
        except ApiError:
            return None

    if action == 'dream':
        # 摄梦人是两步：先定梦语者，再确认放不放逐
        picked = (step.get('picked') or {}).get(str(user.id)) if step else None
        if picked:
            # 第二次：确认。偶尔会选「让他出局」（第一晚后端会拒，那就退回「就这样」）
            if random.random() < 0.3:
                try:
                    return act(game, user, None, 'slay')
                except ApiError:
                    pass
            try:
                return act(game, user, None, '')
            except ApiError:
                return None

    for target_id in candidates:
        try:
            return act(game, user, target_id)
        except ApiError:
            continue
    return None


def _bot_vote_target(game, voter_id):
    """机器人随便投一个还活着的人（PK 投票时只能投参与 PK 的）。"""
    alive = [p.user_id for p in _alive(game) if p.user_id != voter_id]
    if game.phase == WerewolfGame.PHASE_PK_VOTE:
        pk = set(game.pk_user_ids or [])
        alive = [uid for uid in alive if uid in pk]
    return random.choice(alive) if alive else None


def bot_step(room, bots):
    """让房间里的机器人往前走一步（由 party/bots.py 在轮询时顺手调）。"""
    game = WerewolfGame.objects.filter(room=room).first()
    if game is None or game.phase == WerewolfGame.PHASE_OVER:
        return

    by_id = {m.user_id: m for m in bots}

    # ---- 猎人等着开枪：让他随便带走一个 ----
    if game.pending_shooter_id:
        member = by_id.get(game.pending_shooter_id)
        if member is not None and member.may_bot_act():
            candidates = _bot_candidates(game, None, member.user_id)
            if candidates:
                _bot_act(game, member.user, None, candidates)
            else:
                # 场上没别人可带了（比如就剩他一个）：直接放弃开枪，
                # 不然 pending_shooter 会一直挂在那儿，把整局卡住
                try:
                    _shoot(game, _by_user(game, member.user_id), None)
                except ApiError:
                    pass
        return

    # ---- 夜晚：轮到哪个机器人就让他行动 ----
    if game.phase == WerewolfGame.PHASE_NIGHT:
        step = current_step(game)
        if step is None:
            return
        acted = step.get('acted') or []
        for uid in step['actorIds']:
            member = by_id.get(uid)
            if member is None:
                continue
            # 已经表过态的跳过 —— 像「两只狼一起刀人」这种一步多个行动者，
            # 不多看这一眼就会一直让第一只狼重复选，第二只永远轮不到
            if uid in acted:
                continue
            if not member.may_bot_act():
                return
            _bot_act(game, member.user, step, _bot_candidates(game, step, uid))
            return
        return

    # ---- 发言：轮到机器人就「说完了」 ----
    if game.phase in (WerewolfGame.PHASE_SPEECH, WerewolfGame.PHASE_PK_SPEECH):
        order = game.speech_order or []
        index = game.speech_index or 0
        if index < len(order):
            member = by_id.get(order[index])
            if member is not None and member.may_bot_act():
                advance(game, member.user)
        return

    # ---- 投票：机器人投一票（一次只让一个人投，慢慢来） ----
    if game.phase in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
        # PK 投票时，参与 PK 的那两个人**自己不投** —— 必须把他们跳过。
        # 否则循环会一直停在他们身上：他们永远投不了票（后端会拒），
        # 也就永远不会变成「已投票」，后面的机器人就永远轮不到。
        pk = set(game.pk_user_ids or []) if game.phase == WerewolfGame.PHASE_PK_VOTE else set()

        for member in bots:
            player = _by_user(game, member.user_id)
            if player is None or not player.alive or player.voted:
                continue
            if member.user_id in pk:
                continue
            if not member.may_bot_act():
                return

            target = _bot_vote_target(game, member.user_id)
            if target is None:
                continue

            try:
                cast_vote(game, member.user, target)
            except ApiError:
                # 这一个投不出去就试下一个，别卡死在一棵树上
                continue
            return
        return


def _public_round_result(game, result):
    """
    下发给客户端的本轮结算。

    「谁出局了」是公开的，但**出局者的身份不公开** —— 身份只在
    phase === 'over' 时全公开。数据库里照样存着 outRoleId（结束后回看用），
    这里只是下发前把它摘掉（和 public_state 里 players 行的规则保持一致）。
    """
    if not result:
        return result
    if game.phase == WerewolfGame.PHASE_OVER:
        return result
    row = dict(result)
    row.pop('outRoleId', None)
    return row


def public_state(game, me):
    """
    给某个客户端看的状态。

    身份信息的可见规则：
      - 自己的身份，自己永远看得到；
      - 狼人能看到同伴是谁（不然没法商量）；
      - 查验记录只有本人看得到；
      - 别人一律只公布「编号 + 存活状态」，不公布身份 —— 这是狼人杀的底线。
      - 狼妃的封锁目标只有她自己看得到（连被封的人也不知道）。
    """
    if game is None:
        return None

    players = _players(game)
    me_player = next((p for p in players if p.user_id == me.id), None)
    me_role = role_of(me_player.role_id) if me_player else None
    me_is_wolf = bool(me_player and is_wolf(me_player.role_id))
    me_action = ((me_role or {}).get('night') or {}).get('action', '')
    over = game.phase == WerewolfGame.PHASE_OVER

    rows = []
    for p in players:
        role = role_of(p.role_id)
        row = {
            'userId': p.user_id,
            'seat': p.seat,
            'nickname': p.user.nickname,
            'alive': p.alive,
            'left': p.left,
            'isMe': p.user_id == me.id,
            # **只能看到自己投没投**。投票进行中，谁投了谁是个秘密 ——
            # 全部投完由后端自动结算，结算时票型一次性公开（round_result.ballots）。
            'voted': bool(p.user_id == me.id and p.voted),
            'sheriff': p.sheriff,
            # 人机（后端自动驱动的那种），前端会在名字旁边标出来
            'isBot': bool(p.user.is_bot),
        }
        # 只有这三种情况才带上身份
        if p.user_id == me.id or (me_is_wolf and is_wolf(p.role_id)) or over:
            row['roleId'] = p.role_id
            row['roleName'] = role['name'] if role else ''
            row['camp'] = role['camp'] if role else ''
        rows.append(row)

    state = {
        'phase': game.phase,
        'day': game.day,
        'aliveCount': len([p for p in players if p.alive]),
        'playerCount': len(players),
        'players': rows,

        'myRoleId': me_player.role_id if me_player else '',
        'myRoleName': me_role['name'] if me_role else '',
        'myRoleEmoji': me_role['emoji'] if me_role else '',
        'myCamp': me_role['camp'] if me_role else '',
        'myAlive': bool(me_player and me_player.alive),
        'mySeat': me_player.seat if me_player else None,
        # 我的一次性技能还剩什么
        'mySkills': {
            'healUsed': bool(me_player and me_player.heal_used),
            'poisonUsed': bool(me_player and me_player.poison_used),
            'shotUsed': bool(me_player and me_player.shot_used),
        },

        # 昨夜死讯：只给编号
        'lastNightDeaths': [
            {'userId': uid, 'seat': _seat_of(game, uid), 'nickname': _nickname_of(game, uid)}
            for uid in (game.last_night_deaths or [])
        ],
        'peacefulNight': game.day > 0 and not (game.last_night_deaths or []),

        # 发言
        'speechIndex': game.speech_index or 0,
        'speechOrder': [
            {'userId': uid, 'seat': _seat_of(game, uid), 'nickname': _nickname_of(game, uid)}
            for uid in (game.speech_order or [])
        ],
        'pkUserIds': game.pk_user_ids or [],

        'roundResult': _public_round_result(game, game.round_result),
        'winner': game.winner or None,
        'winReason': game.win_reason or '',

        # 牌桌中间那一栏的进程记录。公开事件，所有人看到的都是同一份。
        'gameLog': list(game.event_log or []),

        # 猎人等着开枪
        'pendingShooterUserId': game.pending_shooter_id,
        'pendingShooterSeat': (
            _seat_of(game, game.pending_shooter_id) if game.pending_shooter_id else None
        ),
        'canShoot': bool(
            me_player and game.pending_shooter_id == me_player.user_id and not me_player.shot_used
        ),

        # 倒计时（提示用；客户端请以 serverNow 为准，别用本机时间）
        'serverNow': int(timezone.now().timestamp() * 1000),
        'phaseStartedAt': (
            int(game.phase_started_at.timestamp() * 1000) if game.phase_started_at else 0
        ),
        'phaseDuration': game.phase_duration or 0,
    }

    # ---- 隐狼：告诉他自己现在还在不在「藏」 ----
    if me_player and is_hidden_role(me_player.role_id):
        state['myDisguised'] = is_disguised(game, me_player)

    # ---- 狼妃：技能失效后，隔一个夜晚告知 ----
    if me_player and me_action == 'block':
        broken_day = me_player.block_broken_day
        # 「失效后的第二个夜晚」才告诉他：也就是当前天数 >= 失效那天 + 2
        notified = bool(
            broken_day is not None
            and (game.day or 0) >= broken_day + 2
            and not me_player.block_notified
        )
        if notified:
            me_player.block_notified = True
            me_player.save(update_fields=['block_notified'])
        state['myBlock'] = {
            'broken': broken_day is not None,
            'notified': bool(broken_day is not None and (game.day or 0) >= broken_day + 2),
            'knownBroken': bool(me_player.block_notified or notified),
        }

    # 当前发言人
    order = game.speech_order or []
    index = game.speech_index or 0
    if game.phase in (
        WerewolfGame.PHASE_SPEECH,
        WerewolfGame.PHASE_PK_SPEECH,
    ) and index < len(order):
        state['currentSpeakerSeat'] = _seat_of(game, order[index])
        state['currentSpeakerUserId'] = order[index]
    else:
        state['currentSpeakerSeat'] = None
        state['currentSpeakerUserId'] = None

    # 夜晚：只暴露「现在轮到谁」，不暴露别人选了什么
    if game.phase == WerewolfGame.PHASE_NIGHT:
        step = current_step(game)
        if step:
            names = '、'.join(
                (role_of(rid) or {}).get('name', rid) for rid in step['roleIds']
            )
            hints = [
                ((role_of(rid) or {}).get('night') or {}).get('hint', '')
                for rid in step['roleIds']
            ]
            state['night'] = {
                'stepRoleIds': step['roleIds'],
                'stepRoleName': names,
                'stepAction': step['action'],
                'stepLabel': step['label'],
                'stepHint': hints[0] if hints else '',
                'isMyTurn': bool(me_player and me_player.user_id in step['actorIds']),
                'actedCount': len(step.get('acted') or []),
                'actorCount': len(step['actorIds']),
                'myTargetSeat': (
                    _seat_of(game, step['targets'].get(str(me_player.user_id)))
                    if me_player and str(me_player.user_id) in (step.get('targets') or {})
                    else None
                ),
            }

            # 狼人之间互相能看到同伴选的人（要商量），别人看不到
            if me_is_wolf and step['action'] == 'kill':
                state['night']['wolfChoices'] = [
                    {
                        'userId': int(uid),
                        'seat': _seat_of(game, int(uid)),
                        'targetSeat': _seat_of(game, target_id),
                    }
                    for uid, target_id in (step.get('targets') or {}).items()
                    if is_wolf((_by_user(game, int(uid)) or me_player).role_id)
                ]

            # 女巫行动时能看到今晚谁被袭击了（不然没法决定救不救）
            if me_action == 'witch' and step['action'] == 'witch':
                wolf_target = kill_target_of(game)
                state['night']['wolfTargetSeat'] = _seat_of(game, wolf_target)
                state['night']['wolfTargetUserId'] = wolf_target

            # 觉醒摄梦人：分两步 —— 先定梦语者（这时就能读心），再决定放不放逐
            if me_action == 'dream' and step['action'] == 'dream':
                uid = str(me_player.user_id)
                picked = bool((step.get('picked') or {}).get(uid))
                confirmed = uid in (step.get('targets') or {})

                state['night']['dreamPicked'] = picked
                state['night']['dreamConfirmed'] = confirmed
                # 「让他出局」只在「梦语者已定、还没确认」时给，而且要第二晚起
                state['night']['canSlay'] = bool(
                    picked and not confirmed and (game.day or 0) >= 1
                )
                state['night']['lastTargetSeat'] = _seat_of(game, me_player.last_dream_target_id)
                state['night']['lastTargetUserId'] = me_player.last_dream_target_id

            # 狼妃：今晚被封的人是谁（只有她自己看得到）
            if me_action == 'block' and step['action'] == 'block':
                night = game.night or {}
                state['night']['blockedSeat'] = _seat_of(game, night.get('blocked'))
                state['night']['blockedUserId'] = night.get('blocked')
                state['night']['canBlock'] = me_player.block_broken_day is None

    # ---- 查验记录（预言家 / 守墓人，只有本人看得到）----
    if me_player and me_player.checks:
        state['myChecks'] = sorted(
            [
                {
                    'userId': int(uid),
                    'seat': _seat_of(game, int(uid)),
                    'nickname': _nickname_of(game, int(uid)),
                    'result': result,
                }
                for uid, result in (me_player.checks or {}).items()
            ],
            key=lambda row: row['seat'] if row['seat'] is not None else 0,
        )

    # ---- 觉醒摄梦人：她选的那个梦语者，今晚到底动没动 ----
    # 她排在**最后**行动，所以选完就能立刻读到结果，不用再睁一次眼。
    # 天亮之后 game.night 里还留着这一夜的步骤，那时照样看得到。
    if me_player and me_action == 'dream':
        night = game.night or {}
        picked_id = _dream_target_of(game, me_player.user_id)
        if picked_id is not None:
            # 显示的是「她以为的那个人」；读心结果按**实际护住的人**算 ——
            # 技能被狼妃弹回自己时，她会读到「行动过」（规则里她不知情）。
            actual_id = _dream_actual_target(game, me_player, picked_id)
            shown = _by_user(game, picked_id)
            actual = _by_user(game, actual_id)
            if shown is not None and actual is not None:
                state['myDream'] = {
                    'targetUserId': picked_id,
                    'targetSeat': shown.seat,
                    'targetNickname': shown.user.nickname,
                    'targetActed': acted_tonight(game, night, actual),
                }

    return state
