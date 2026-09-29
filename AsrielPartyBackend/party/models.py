"""
数据模型。

这里没有用 Django 自带的 auth.User：小程序用户本质上就是「一个 openid」，
没有密码、没有后台登录需求，用一套轻量模型更直接。
（如果你想在 admin 后台里管数据，可以照常注册这些模型，不影响。）
"""

import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

# 同一个机器人两次行动之间至少隔这么多秒（见下面 RoomMember.may_bot_act）
BOT_ACT_INTERVAL = 2.5


def gen_token():
    """小程序侧的登录态令牌。"""
    return secrets.token_hex(24)


class User(models.Model):
    """小程序用户。唯一标识是 openid，规则见 docs/backend-api.md。"""

    openid = models.CharField(max_length=64, unique=True, db_index=True)
    # 只有绑定了微信开放平台才会返回 unionid，先留一列备用
    unionid = models.CharField(max_length=64, blank=True, default='')
    nickname = models.CharField(max_length=32, default='微信用户')
    avatar = models.CharField(max_length=512, blank=True, default='')
    token = models.CharField(max_length=64, unique=True, db_index=True, default=gen_token)
    # 是不是人机。人机由后端自动驱动（见 party/bots.py），
    # 用来让「一个人 + 一台手机」也能把需要好几个人的游戏跑起来。
    is_bot = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ap_user'

    def __str__(self):
        return '%s(%s)' % (self.nickname, self.openid)


class Room(models.Model):
    STATUS_WAITING = 'waiting'
    STATUS_PLAYING = 'playing'

    code = models.CharField(max_length=8, unique=True, db_index=True)
    title = models.CharField(max_length=64, blank=True, default='')
    game_id = models.CharField(max_length=32)
    # 版型 id（狼人杀用；不传就用该游戏的默认版型）。座位数由版型决定。
    variant_id = models.CharField(max_length=32, blank=True, default='')
    max_players = models.PositiveSmallIntegerField(default=10)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_rooms')
    status = models.CharField(max_length=16, default=STATUS_WAITING)
    # 谁是卧底：开局前由房主设定的卧底人数
    undercover_count = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ap_room'
        ordering = ['-updated_at']

    def __str__(self):
        return '%s %s' % (self.code, self.title or '-')


class RoomMember(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    seat = models.PositiveSmallIntegerField()
    ready = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    # 人机下一次可以行动的时间：控制它的节奏，免得一次轮询就把一整轮操作全跑完
    bot_next_act_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ap_room_member'
        ordering = ['seat']
        constraints = [
            # 一个座位只能坐一个人；同一个人也不会在一个房间里出现两次
            models.UniqueConstraint(fields=['room', 'seat'], name='uniq_room_seat'),
            models.UniqueConstraint(fields=['room', 'user'], name='uniq_room_user'),
        ]

    def may_bot_act(self, interval=None):
        """
        这个人机现在能动手吗？（真人永远返回 False）

        每放行一次就把「下次能行动的时间」往后推，所以同一个机器人不会被一次
        轮询连着驱动好几步 —— 看起来才像有人在慢慢想。
        """
        if not self.user.is_bot:
            return False

        now = timezone.now()
        if self.bot_next_act_at and self.bot_next_act_at > now:
            return False

        self.bot_next_act_at = now + timedelta(seconds=interval or BOT_ACT_INTERVAL)
        self.save(update_fields=['bot_next_act_at'])
        return True

    def __str__(self):
        return '%s @%s' % (self.user.nickname, self.seat)


class UndercoverGame(models.Model):
    """谁是卧底的一局。"""

    PHASE_REVEAL = 'reveal'   # 刚发牌，还没人投
    PHASE_VOTING = 'voting'   # 有人在投了
    PHASE_RESULT = 'result'   # 本轮已结算
    PHASE_OVER = 'over'       # 整局结束

    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name='undercover')
    phase = models.CharField(max_length=16, default=PHASE_REVEAL)
    round_no = models.PositiveSmallIntegerField(default=1)
    undercover_count = models.PositiveSmallIntegerField(default=1)
    civilian_word = models.CharField(max_length=32)
    undercover_word = models.CharField(max_length=32)
    # 抽中的词库序号，方便复盘时定位是哪一组词
    word_index = models.IntegerField(default=0)
    # 'civilian' / 'undercover' / ''
    winner = models.CharField(max_length=16, blank=True, default='')
    round_result = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'ap_undercover_game'

    def __str__(self):
        return 'round %s %s' % (self.round_no, self.phase)


class UndercoverPlayer(models.Model):
    ROLE_CIVILIAN = 'civilian'
    ROLE_UNDERCOVER = 'undercover'

    game = models.ForeignKey(UndercoverGame, on_delete=models.CASCADE, related_name='players')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.PositiveSmallIntegerField()
    role = models.CharField(max_length=16)
    # 这名玩家看到的词
    word = models.CharField(max_length=32)
    alive = models.BooleanField(default=True)
    # 中途退出房间（而不是被投出去）
    left = models.BooleanField(default=False)
    vote_target = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        db_table = 'ap_undercover_player'
        ordering = ['seat']

    def __str__(self):
        return '%s %s' % (self.user.nickname, self.role)


class UndercoverWordPair(models.Model):
    """
    谁是卧底的词库。
    用 `python manage.py seed_words` 从 party/words.py 导入（可重复执行，不会重复插入）。
    """

    word_a = models.CharField(max_length=32)
    word_b = models.CharField(max_length=32)

    class Meta:
        db_table = 'ap_undercover_word'

    def __str__(self):
        return '%s / %s' % (self.word_a, self.word_b)


class WerewolfGame(models.Model):
    """
    狼人杀的一局。

    流程是「夜晚 -> 白天」交替，具体阶段见下面的 PHASE_*；
    规则判定全在 party/werewolf.py，这里只存状态。
    """

    PHASE_NIGHT = 'night'          # 夜晚：按角色顺序依次唤醒
    PHASE_DAYBREAK = 'daybreak'    # 天亮了：宣布昨夜情况
    PHASE_SHERIFF = 'sheriff'      # 警长竞选（预留，6 人场跳过）
    PHASE_SPEECH = 'speech'        # 发言
    PHASE_VOTE = 'vote'            # 放逐投票
    PHASE_PK_SPEECH = 'pk_speech'  # 平票后的 PK 发言
    PHASE_PK_VOTE = 'pk_vote'      # 平票后的再投票
    PHASE_RESULT = 'result'        # 本轮结算展示
    PHASE_OVER = 'over'            # 整局结束

    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name='werewolf')
    # 开局时用的版型：存一份在局上，房间之后改版型不影响进行中的这局
    variant_id = models.CharField(max_length=32, blank=True, default='')

    phase = models.CharField(max_length=16, default=PHASE_NIGHT)
    # 第几天。0 = 还没天亮过（第一个夜晚）
    day = models.PositiveSmallIntegerField(default=0)

    # 夜晚状态，结构见 party/werewolf.py 的 new_night_state()
    night = models.JSONField(default=dict, blank=True)
    # 本白天要宣布的死亡玩家 id（只公布编号，不公布身份）
    last_night_deaths = models.JSONField(default=list, blank=True)

    # 发言顺序（user id 列表）与当前轮到第几个
    speech_order = models.JSONField(default=list, blank=True)
    speech_index = models.PositiveSmallIntegerField(default=0)
    # 需要 PK 的玩家（user id 列表）
    pk_user_ids = models.JSONField(default=list, blank=True)

    # 本轮结果 { tie, outUserId, outSeat, outNickname, tally: [...], ballots: [...] }
    round_result = models.JSONField(null=True, blank=True)

    # 进程记录：开局到现在的**公开**事件（谁出局、票型、发言顺序…）。
    # 前端中间那一栏直接显示。**身份一律不进这里** —— 只写 userId / 座位号。
    event_log = models.JSONField(default=list, blank=True)

    # 'good' / 'wolf' / ''
    winner = models.CharField(max_length=16, blank=True, default='')
    # 结束原因，给玩家看的一句话
    win_reason = models.CharField(max_length=64, blank=True, default='')

    # 上一次被放逐出局的人（守墓人要验他是不是狼）
    last_lynched_user_id = models.IntegerField(null=True, blank=True)
    # 还在等谁开枪（猎人出局后，游戏会停在这个人身上等他选目标）
    pending_shooter_id = models.IntegerField(null=True, blank=True)

    # 当前阶段的倒计时 —— **只作提示**，到点不会自动推进阶段
    phase_started_at = models.DateTimeField(null=True, blank=True)
    phase_duration = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'ap_werewolf_game'

    def __str__(self):
        return 'day %s %s' % (self.day, self.phase)


class WerewolfPlayer(models.Model):
    """这一局里的某个玩家。"""

    game = models.ForeignKey(WerewolfGame, on_delete=models.CASCADE, related_name='players')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.PositiveSmallIntegerField()
    # 对应 constants.ROLES 里的 id
    role_id = models.CharField(max_length=32)
    alive = models.BooleanField(default=True)
    # 中途退出房间
    left = models.BooleanField(default=False)
    # 是否警长（预留，6 人场用不到）
    sheriff = models.BooleanField(default=False)

    # ---- 当前夜晚（每夜重置）----
    # 这一夜是否已经行动过
    acted = models.BooleanField(default=False)
    # 这一夜选的目标
    night_target = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    # 守卫不能连续两晚守同一个人，记一下上一夜守的是谁
    last_guard_target_id = models.IntegerField(null=True, blank=True)
    # 觉醒摄梦人同样不能连续两晚选同一个人
    last_dream_target_id = models.IntegerField(null=True, blank=True)

    # 查验记录 { userId: 'wolf' | 'good' }（预言家、守墓人都写这里）
    checks = models.JSONField(default=dict, blank=True)

    # ---- 一次性技能（女巫的药、猎人的枪、狼妃的封锁）----
    heal_used = models.BooleanField(default=False)
    poison_used = models.BooleanField(default=False)
    shot_used = models.BooleanField(default=False)
    # 狼妃的封锁：一旦成功反弹过一次技能就永久失效。
    # 记录失效发生在「第几天夜里」，狼妃要到那之后的第二个夜晚才会被告知。
    block_broken_day = models.IntegerField(null=True, blank=True)
    block_notified = models.BooleanField(default=False)

    # ---- 当前投票轮（每轮重置）----
    # 是否已经表态（明确弃票也算表态）
    voted = models.BooleanField(default=False)
    vote_target = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        db_table = 'ap_werewolf_player'
        ordering = ['seat']
        constraints = [
            models.UniqueConstraint(fields=['game', 'user'], name='uniq_ww_game_user'),
        ]

    def __str__(self):
        return '%s %s' % (self.user.nickname, self.role_id)


class DrawGame(models.Model):
    """
    你画我猜的一局。

    一局 = 房间里每人轮流当一次画手；每轮：
    画手从候选词里选一个 → 作画、其他人打字猜 → 时间到或全员猜对就结算。

    规则判定全在 party/draw_guess.py，这里只存状态。
    和另外两个游戏不一样：**这里的倒计时是真的** —— 到点了会在下一次
    轮询时自动结算（见 draw_guess.tick），不会把游戏卡住。
    """

    PHASE_PICKING = 'picking'   # 画手挑词（其他人干等）
    PHASE_DRAWING = 'drawing'   # 画手作画，其他人猜
    PHASE_RESULT = 'result'     # 本轮结算：公布答案与得分
    PHASE_OVER = 'over'         # 整局结束

    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name='draw')

    phase = models.CharField(max_length=16, default=PHASE_PICKING)
    # 第几轮 / 一共几轮（= 开局时的房间人数，每人当一次画手）
    round_no = models.PositiveSmallIntegerField(default=1)
    total_rounds = models.PositiveSmallIntegerField(default=0)

    # 当画手的顺序（user id 列表），开局时按座位顺序定下来
    drawer_order = models.JSONField(default=list, blank=True)
    drawer_user_id = models.IntegerField(null=True, blank=True)

    # 本轮答案 / 给画手的候选词 / 词类（词类会当作提示给猜的人看）
    word = models.CharField(max_length=32, blank=True, default='')
    word_options = models.JSONField(default=list, blank=True)
    word_category = models.CharField(max_length=16, blank=True, default='')

    # 累计得分 { '用户id': 分数 }
    scores = models.JSONField(default=dict, blank=True)
    # 本轮已经猜对的人（猜对一次就够，不重复计分）
    solved_user_ids = models.JSONField(default=list, blank=True)
    # 本次对局的猜测流水（只留最近若干条，公开给所有人看）
    guesses = models.JSONField(default=list, blank=True)
    # 本轮结算 { word, timeout, gain: [{ userId, nickname, gain }] }
    round_result = models.JSONField(null=True, blank=True)

    # 当前阶段的倒计时（picking / drawing 到点会自动推进）
    phase_started_at = models.DateTimeField(null=True, blank=True)
    phase_duration = models.PositiveIntegerField(default=0)

    # 已经画了多少笔：客户端拿它做增量同步（见 DrawStroke）
    stroke_seq = models.PositiveIntegerField(default=0)

    # 这一局出过的答案词：下一轮抽候选词时避开，免得同一局里重复出题
    used_words = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'ap_draw_game'

    def __str__(self):
        return 'round %s %s' % (self.round_no, self.phase)


class DrawStroke(models.Model):
    """
    一条笔画（画手从落笔到抬笔之间的轨迹）。

    坐标是**归一化**的（0~1），不同尺寸的屏幕都能还原成同样的画；
    客户端按 seq 增量拉取（`GET /api/rooms/draw/poll`），画手那边积攒一会儿
    再批量提交，这样一次轮询就能把好几笔带过去。
    """

    game = models.ForeignKey(DrawGame, on_delete=models.CASCADE, related_name='strokes')
    seq = models.PositiveIntegerField()
    # [[x, y], ...]，x / y 都是 0~1
    points = models.JSONField(default=list)
    color = models.CharField(max_length=16, default='#f3f0ff')
    width = models.PositiveSmallIntegerField(default=6)

    class Meta:
        db_table = 'ap_draw_stroke'
        ordering = ['seq']
        constraints = [
            models.UniqueConstraint(fields=['game', 'seq'], name='uniq_draw_game_seq'),
        ]

    def __str__(self):
        return '#%s (%s points)' % (self.seq, len(self.points or []))
