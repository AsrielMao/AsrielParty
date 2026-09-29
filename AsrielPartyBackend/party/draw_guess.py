"""
你画我猜 · 规则与流程。

流程：**选词 → 作画与猜词 → 本轮结算 → 下一轮 …… → 整局结束**。
一局 = 房间里每人轮流当一次画手（开局时按座位顺序排好）。

和另外两个游戏最大的不同：**这里的倒计时是真的**。
Django 里没有后台定时任务，所以「时间到」由客户端每次轮询时顺手调 `tick()` 推进 ——
只要还有一个人开着页面，这局就不会卡住。

实时同步走的是「增量笔画」：画手在本地实时画，攒一会儿批量提交；
其他人按 `sinceSeq` 拉增量，客户端自己往画布上追加。见 models.DrawStroke。
"""

import random

from django.utils import timezone

from . import draw_words
from .auth import ApiError
from .constants import DRAW_GUESS_GAME_ID  # noqa: F401  (视图按它分派，这里引一下便于对照)
from .models import DrawGame, DrawStroke

# ---- 各阶段时长（秒）----
PICK_SECONDS = 15      # 画手挑词
DRAW_SECONDS = 90      # 作画 + 猜词
RESULT_SECONDS = 20    # 本轮结算展示（到点自动下一轮）

# ---- 计分 ----
SCORE_GUESS = 10            # 猜对一个人拿多少分
SCORE_DRAWER_PER_SOLVER = 5  # 画手每被一个人猜对拿多少分

# ---- 防滥用 / 防撑爆 ----
MAX_GUESSES = 24           # 保留给客户端看的猜测流水条数
MAX_STROKES_PER_POST = 40  # 一次提交最多几笔
MAX_POINTS_PER_STROKE = 240  # 单笔最多几个点
MAX_GUESS_LENGTH = 20      # 猜词最大长度

DEFAULT_COLOR = '#f3f0ff'
DEFAULT_WIDTH = 6

PICK_OPTION_COUNT = 3

# ---- 人机（见 party/bots.py）----
BOT_GUESS_INTERVAL = 4.0     # 机器人猜词慢一点，不然一句话刷满屏
BOT_GUESS_BASE = 0.2         # 一上来就猜对的概率
BOT_GUESS_MAX = 0.8          # 时间快到时猜对的概率（免得总是干等满 90 秒）
BOT_STROKE_COLORS = ['#f3f0ff', '#ffc861', '#35d6a4', '#7c5cff']


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _members(game):
    """房间成员，按座位排序。"""
    return list(game.room.members.select_related('user').order_by('seat'))


def _member_of(game, user_id):
    for m in _members(game):
        if m.user_id == user_id:
            return m
    return None


def _nickname_of(game, user_id):
    m = _member_of(game, user_id)
    return m.user.nickname if m else '已退出'


def _seat_of(game, user_id):
    m = _member_of(game, user_id)
    return m.seat if m else None


def score_of(game, user_id):
    return int((game.scores or {}).get(str(user_id), 0))


def _add_score(game, user_id, delta):
    scores = dict(game.scores or {})
    key = str(user_id)
    scores[key] = int(scores.get(key, 0)) + delta
    game.scores = scores


def winner_user_ids(game):
    """分数最高的人（可能并列）。一分都没有就不给冠军。"""
    scores = game.scores or {}
    if not scores:
        return []
    top = max(int(v) for v in scores.values())
    if top <= 0:
        return []
    return sorted(int(uid) for uid, v in scores.items() if int(v) == top)


def _set_timer(game, seconds):
    """设置当前阶段的倒计时（调用方负责 save）。"""
    game.phase_started_at = timezone.now()
    game.phase_duration = max(0, int(seconds or 0))


def remain_seconds(game):
    """当前阶段还剩几秒。0 表示到了或压根没在计时。"""
    if not game.phase_started_at or not game.phase_duration:
        return 0
    passed = (timezone.now() - game.phase_started_at).total_seconds()
    return max(0, int(game.phase_duration - passed))


def _used_words(game):
    """这一局已经出过的词 —— 下一轮抽候选词时要避开，免得同一局里重复出题。"""
    return list(game.used_words or [])


# ---------------------------------------------------------------------------
# 开局 / 每一轮
# ---------------------------------------------------------------------------

def start_game(room):
    """
    开局发牌：按座位顺序排好当画手的顺序，然后进入第一轮的选词阶段。
    调用方负责把 room.status 置为 playing。
    """
    members = list(room.members.select_related('user').order_by('seat'))
    if len(members) < 2:
        raise ApiError('你画我猜至少要 2 个人')

    order = [m.user_id for m in members]

    game = DrawGame.objects.create(
        room=room,
        phase=DrawGame.PHASE_PICKING,
        round_no=1,
        total_rounds=len(order),
        # 第一个画手直接定下来，剩下的排队
        drawer_order=order[1:],
        drawer_user_id=order[0],
        scores={str(uid): 0 for uid in order},
    )

    return _start_round(game)


def _start_round(game):
    """开始新一轮：抽候选词、进入选词阶段。"""
    # 上一轮的画要清掉（客户端靠 round 变化发现该清屏）
    game.strokes.all().delete()
    game.stroke_seq = 0

    game.word = ''
    game.word_category = ''
    game.word_options = [w['word'] for w in draw_words.pick_options(
        PICK_OPTION_COUNT, exclude=_used_words(game)
    )]
    game.solved_user_ids = []
    game.guesses = []
    game.round_result = None
    game.phase = DrawGame.PHASE_PICKING
    _set_timer(game, PICK_SECONDS)
    game.save()
    return game


def _begin_next_round(game):
    """把画手队列往前推一个，进入下一轮；队列空了就整局结束。"""
    order = list(game.drawer_order or [])
    if not order:
        game.phase = DrawGame.PHASE_OVER
        _set_timer(game, 0)
        game.save()
        return game

    game.drawer_user_id = order[0]
    game.drawer_order = order[1:]
    game.round_no = (game.round_no or 1) + 1
    return _start_round(game)


def pick_word(game, user, index):
    """画手从候选词里挑一个。"""
    if game.phase != DrawGame.PHASE_PICKING:
        raise ApiError('现在不是选词的时候')
    if not user or user.id != game.drawer_user_id:
        raise ApiError('这一轮不是你画')

    options = list(game.word_options or [])
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise ApiError('选一个候选词')

    if index < 0 or index >= len(options):
        raise ApiError('选一个候选词')

    word = options[index]
    game.word = word
    game.word_category = draw_words.category_of(word)
    # 记下来，这一局后面抽候选词时避开它
    used = _used_words(game)
    if word not in used:
        used.append(word)
    game.used_words = used
    game.phase = DrawGame.PHASE_DRAWING
    _set_timer(game, DRAW_SECONDS)
    game.save()
    return game


# ---------------------------------------------------------------------------
# 画笔
# ---------------------------------------------------------------------------

def _clean_points(raw):
    out = []
    for pt in (raw or [])[:MAX_POINTS_PER_STROKE]:
        try:
            x = float(pt[0])
            y = float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        # 归一化到 0~1，不同屏幕尺寸才能还原成同一张画
        out.append([
            round(min(1.0, max(0.0, x)), 4),
            round(min(1.0, max(0.0, y)), 4),
        ])
    return out


def _clean_color(raw):
    color = str(raw or '')
    if len(color) == 7 and color.startswith('#'):
        if all(ch in '0123456789abcdefABCDEF' for ch in color[1:]):
            return color.lower()
    return DEFAULT_COLOR


def _clean_width(raw):
    try:
        width = int(raw)
    except (TypeError, ValueError):
        width = DEFAULT_WIDTH
    return max(2, min(24, width))


def add_strokes(game, user, strokes):
    """
    画手提交一批笔画。一次可以带好几笔 —— 客户端攒一小会儿再发，
    这样一发请求就能补上几百毫秒的进度。
    """
    if game.phase != DrawGame.PHASE_DRAWING:
        raise ApiError('现在不能画')
    if not user or user.id != game.drawer_user_id:
        raise ApiError('这一轮不是你画')

    seq = game.stroke_seq or 0
    rows = []

    for item in (strokes or [])[:MAX_STROKES_PER_POST]:
        points = _clean_points((item or {}).get('points'))
        if len(points) < 2:
            continue
        seq += 1
        rows.append(DrawStroke(
            game=game,
            seq=seq,
            points=points,
            color=_clean_color((item or {}).get('color')),
            width=_clean_width((item or {}).get('width')),
        ))

    if not rows:
        raise ApiError('没有收到有效的笔画')

    DrawStroke.objects.bulk_create(rows)
    game.stroke_seq = seq
    game.save(update_fields=['stroke_seq'])
    return game


def strokes_since(game, since_seq):
    """从 since_seq 之后画的所有笔（客户端轮询增量用）。"""
    try:
        since_seq = int(since_seq)
    except (TypeError, ValueError):
        since_seq = 0

    rows = game.strokes.filter(seq__gt=since_seq).order_by('seq')
    return [
        {'seq': r.seq, 'points': r.points, 'color': r.color, 'width': r.width}
        for r in rows
    ]


def clear_canvas(game, user):
    """
    画手把这一轮的画擦掉重来。

    实现上不是真删干净：会留下**一条点数空的「笔画」**当清屏信号（序号继续往下走），
    客户端收到 `points` 为空就知道该把画布清空 —— 这样不用额外加字段，
    也不会和增量同步的序号对不上。
    """
    if game.phase != DrawGame.PHASE_DRAWING:
        raise ApiError('现在不能擦')
    if not user or user.id != game.drawer_user_id:
        raise ApiError('这一轮不是你画')

    game.strokes.all().delete()
    seq = (game.stroke_seq or 0) + 1
    DrawStroke.objects.create(game=game, seq=seq, points=[])
    game.stroke_seq = seq
    game.save(update_fields=['stroke_seq'])
    return game


# ---------------------------------------------------------------------------
# 猜词
# ---------------------------------------------------------------------------

def _normalize(text):
    """猜词比对用：去掉所有空白、转小写。"""
    return ''.join(str(text or '').split()).lower()


def is_correct(text, word):
    return bool(word) and _normalize(text) == _normalize(word)


def submit_guess(game, user, text):
    """
    提交一个猜测。

    猜对了就计分（猜的人 +10，画手 +5），并且立刻推进「还差几个人没猜出来」；
    没猜中就把这句话公开给所有人看（这也是乐趣的一部分）。
    """
    if game.phase != DrawGame.PHASE_DRAWING:
        raise ApiError('现在不是猜词的时候')
    if not user:
        raise ApiError('你不在这个房间里')
    if user.id == game.drawer_user_id:
        raise ApiError('画手不能猜')

    solved = list(game.solved_user_ids or [])
    if user.id in solved:
        raise ApiError('你已经猜对了')

    text = str(text or '').strip()[:MAX_GUESS_LENGTH]
    if not text:
        raise ApiError('先写点什么再猜')

    correct = is_correct(text, game.word)

    guesses = list(game.guesses or [])
    guesses.append({
        'userId': user.id,
        'nickname': _nickname_of(game, user.id),
        'seat': _seat_of(game, user.id),
        # 猜对了就把内容藏起来，不然等于把答案告诉其他人
        'text': '' if correct else text,
        'correct': correct,
    })
    game.guesses = guesses[-MAX_GUESSES:]

    if correct:
        _add_score(game, user.id, SCORE_GUESS)
        _add_score(game, game.drawer_user_id, SCORE_DRAWER_PER_SOLVER)
        solved.append(user.id)
        game.solved_user_ids = solved
        game.save()

        # 除了画手以外的人全猜出来了 -> 直接结束这一轮，不用干等
        if _everyone_solved(game):
            return settle_round(game)
        return game

    game.save()
    return game


def _guessers(game):
    """这一轮该猜的人（除画手外的所有成员）。"""
    return [m.user_id for m in _members(game) if m.user_id != game.drawer_user_id]


def _everyone_solved(game):
    guessers = _guessers(game)
    solved = set(game.solved_user_ids or [])
    return bool(guessers) and all(uid in solved for uid in guessers)


# ---------------------------------------------------------------------------
# 结算 / 推进
# ---------------------------------------------------------------------------

def settle_round(game, timeout=False):
    """本轮结束：公布答案、记下这一轮的得分情况。"""
    if game.phase not in (DrawGame.PHASE_PICKING, DrawGame.PHASE_DRAWING):
        return game

    solved = list(game.solved_user_ids or [])
    gain = []
    for m in _members(game):
        if m.user_id in solved:
            gain.append({
                'userId': m.user_id,
                'nickname': m.user.nickname,
                # 猜对的 +10（画手那 +5 是每人头算的，不列在这里免得看不懂）
                'gain': SCORE_GUESS,
            })
    if solved:
        gain.append({
            'userId': game.drawer_user_id,
            'nickname': _nickname_of(game, game.drawer_user_id),
            'gain': SCORE_DRAWER_PER_SOLVER * len(solved),
        })

    game.round_result = {
        'word': game.word,
        'category': game.word_category,
        # 时间到了自动结算，还是全员猜出来了
        'timeout': bool(timeout),
        'solvedUserIds': solved,
        'gain': sorted(gain, key=lambda row: -row['gain']),
    }
    game.phase = DrawGame.PHASE_RESULT
    _set_timer(game, RESULT_SECONDS)
    game.save()
    return game


def advance(game, user=None):
    """本轮结算看完，进入下一轮；最后一轮之后整局结束。"""
    if game.phase == DrawGame.PHASE_OVER:
        raise ApiError('这局已经结束了')
    if game.phase != DrawGame.PHASE_RESULT:
        raise ApiError('等这一轮结束再说')
    return _begin_next_round(game)


class _FakeUser(object):
    """tick 里替画手自动选词时用 —— pick_word 只用得到 .id。"""

    def __init__(self, user_id):
        self.id = user_id


def tick(game):
    """
    每次轮询时顺手调一下：到点了就自动往下走。

    这是「轮询驱动」的推进 —— 不需要后台定时任务，代价是必须有人开着页面。
    对几个朋友的线下局来说，这一点永远成立。
    """
    if game is None or game.phase == DrawGame.PHASE_OVER:
        return game
    if remain_seconds(game) > 0:
        return game

    if game.phase == DrawGame.PHASE_PICKING:
        # 画手忘了选词：直接替他选第一个，别把大家晾着
        return pick_word(game, _FakeUser(game.drawer_user_id), 0)

    if game.phase == DrawGame.PHASE_DRAWING:
        return settle_round(game, timeout=True)

    if game.phase == DrawGame.PHASE_RESULT:
        return _begin_next_round(game)

    return game


def remove_player(game, user_id):
    """
    有人退出房间：把他从画手队列里摘掉；如果他正好是这一轮的画手，
    这一轮就直接收掉，免得整桌人等他一个人。
    """
    if game is None:
        return game

    order = list(game.drawer_order or [])
    if user_id in order:
        order.remove(user_id)
        game.drawer_order = order
        # 总轮数跟着减（但不能比已经进行到的轮次还少）
        game.total_rounds = max(game.round_no or 1, (game.total_rounds or 1) - 1)

    if game.drawer_user_id == user_id and game.phase in (
        DrawGame.PHASE_PICKING, DrawGame.PHASE_DRAWING
    ):
        game.save()
        return settle_round(game, timeout=True)

    game.save()
    return game


# ---------------------------------------------------------------------------
# 人机（见 party/bots.py）
# ---------------------------------------------------------------------------

def _random_wrong_word(answer):
    """随便挑一个不是答案的词（机器人猜错时用）。"""
    pool = [w['word'] for w in draw_words.WORDS if w['word'] != answer]
    return random.choice(pool) if pool else '不知道'


def _bot_draw(game, bot_user):
    """机器人画手随手涂一个小图形 —— 它没法真画出个东西，就是让画板有内容。"""
    strokes = []
    for _ in range(random.randint(2, 4)):
        x = random.uniform(0.15, 0.75)
        y = random.uniform(0.15, 0.75)
        strokes.append({
            'points': [
                [round(x, 4), round(y, 4)],
                [
                    round(min(0.95, max(0.05, x + random.uniform(-0.18, 0.18))), 4),
                    round(min(0.95, max(0.05, y + random.uniform(-0.18, 0.18))), 4),
                ],
            ],
            'color': random.choice(BOT_STROKE_COLORS),
            'width': random.choice([4, 8]),
        })

    try:
        add_strokes(game, bot_user, strokes)
    except ApiError:
        pass


def _bot_guess(game, bot_user):
    """
    机器人猜一次。

    猜对的概率随这一轮的时间往后涨 —— 前期能装一会儿，
    后期又不会让大家干晾到 90 秒。
    """
    duration = max(1, game.phase_duration or 1)
    passed = 1 - remain_seconds(game) / duration
    passed = max(0.0, min(1.0, passed))
    chance = BOT_GUESS_BASE + (BOT_GUESS_MAX - BOT_GUESS_BASE) * passed

    text = game.word if random.random() < chance else _random_wrong_word(game.word)

    try:
        submit_guess(game, bot_user, text)
    except ApiError:
        pass


def bot_step(room, bots):
    """
    让房间里的机器人往前走一步（由 party/bots.py 在轮询时顺手调）。

    机器人当画手：挑个词，然后随手涂几笔；
    机器人当猜手：按节奏猜，时间越靠后越容易猜对。
    """
    game = DrawGame.objects.filter(room=room).first()
    if game is None or game.phase == DrawGame.PHASE_OVER:
        return

    by_id = {m.user_id: m for m in bots}
    drawer_id = game.drawer_user_id

    # ---- 选词 ----
    if game.phase == DrawGame.PHASE_PICKING:
        member = by_id.get(drawer_id)
        if member is not None and member.may_bot_act():
            options = list(game.word_options or [])
            if options:
                pick_word(game, member.user, random.randrange(len(options)))
        return

    if game.phase != DrawGame.PHASE_DRAWING:
        return

    # ---- 机器人画手：涂几笔 ----
    drawer_bot = by_id.get(drawer_id)
    if drawer_bot is not None:
        if drawer_bot.may_bot_act():
            _bot_draw(game, drawer_bot.user)
        return

    # ---- 机器人猜手：一次 tick 只让一个人出手，免得刷屏 ----
    solved = set(game.solved_user_ids or [])
    for member in bots:
        uid = member.user_id
        if uid == drawer_id or uid in solved:
            continue
        if not member.may_bot_act(BOT_GUESS_INTERVAL):
            continue
        _bot_guess(game, member.user)
        return


# ---------------------------------------------------------------------------
# 下发
# ---------------------------------------------------------------------------

def public_state(game, me):
    """
    给某个客户端看的对局状态。

    题目只给「画手 / 已经猜对的人 / 本轮公布之后」，其他人只能看到字数和分类 ——
    不然这游戏就没得玩了。
    """
    if game is None:
        return None

    phase = game.phase
    members = _members(game)
    drawer_id = game.drawer_user_id
    is_drawer = bool(me and drawer_id == me.id)
    solved = list(game.solved_user_ids or [])
    my_solved = bool(me and me.id in solved)

    # 谁看得到答案
    revealed = is_drawer or my_solved or phase in (DrawGame.PHASE_RESULT, DrawGame.PHASE_OVER)

    rows = []
    for m in members:
        rows.append({
            'userId': m.user_id,
            'seat': m.seat,
            'nickname': m.user.nickname,
            'score': score_of(game, m.user_id),
            'isDrawer': m.user_id == drawer_id,
            'solved': m.user_id in solved,
            'isMe': bool(me and m.user_id == me.id),
            # 人机（后端自动驱动的那种），前端会在名字旁边标出来
            'isBot': bool(m.user.is_bot),
        })

    scores = sorted(rows, key=lambda r: (-r['score'], r['seat']))

    state = {
        'phase': phase,
        'round': game.round_no,
        'totalRounds': game.total_rounds,

        'drawerUserId': drawer_id,
        'drawerSeat': _seat_of(game, drawer_id) if drawer_id else None,
        'drawerNickname': _nickname_of(game, drawer_id) if drawer_id else '',
        'isDrawer': is_drawer,

        # 题目：画手一直看得到；猜对的人当轮就能看到；公布之后所有人都能看到
        'myWord': game.word if revealed else '',
        'wordRevealed': bool(revealed and game.word),
        'wordLength': len(game.word) if game.word else 0,
        'wordCategory': game.word_category or '',
        # 候选词只有画手在选词阶段拿得到
        'wordOptions': list(game.word_options or []) if is_drawer and phase == DrawGame.PHASE_PICKING else [],

        'solvedUserIds': solved,
        'solvedCount': len(solved),
        'guesserCount': max(0, len(members) - 1),
        'mySolved': my_solved,
        'myScore': score_of(game, me.id) if me else 0,

        'guesses': list(game.guesses or []),
        'scores': scores,
        'roundResult': game.round_result,
        'winnerUserIds': winner_user_ids(game) if phase == DrawGame.PHASE_OVER else [],

        'canPick': bool(is_drawer and phase == DrawGame.PHASE_PICKING),
        'canDraw': bool(is_drawer and phase == DrawGame.PHASE_DRAWING),
        'canGuess': bool(
            me and not is_drawer and phase == DrawGame.PHASE_DRAWING and not my_solved
        ),
        'canAdvance': bool(phase == DrawGame.PHASE_RESULT),

        'strokeSeq': game.stroke_seq or 0,
        'serverNow': int(timezone.now().timestamp() * 1000),
        'phaseStartedAt': (
            int(game.phase_started_at.timestamp() * 1000) if game.phase_started_at else 0
        ),
        'phaseDuration': game.phase_duration or 0,
        'remainSeconds': remain_seconds(game),
    }
    return state
