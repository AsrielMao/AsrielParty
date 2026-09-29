"""
与小程序端保持一致的常量 + 角色/版型配置。

改动这里时要同步改 AsrielParty/utils/games.js 与 config/index.js，
否则前后端会对不上（座位数、版型、人数校验都依赖这些）。

小程序端只负责「展示」，真正的规则判定全在后端（party/werewolf.py）。
"""

# 房间号字符集：去掉了容易看错的 0 O 1 I L
ROOM_CODE_CHARS = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
ROOM_CODE_LENGTH = 6

# 房间默认座位数。**没有版型**的游戏一律用这个。
DEFAULT_SEATS = 10

# wx.joinVoIPChat 单个房间的人数硬上限（微信的限制）
VOICE_MAX_PLAYERS = 10

# 谁是卧底
UNDERCOVER_GAME_ID = 'undercover'
# 狼人杀
WEREWOLF_GAME_ID = 'werewolf'
# 你画我猜
DRAW_GUESS_GAME_ID = 'draw-guess'

# 各游戏的 id 与最低人数（要和 AsrielParty/utils/games.js 里一致）
GAMES = [
    {'id': 'werewolf', 'name': '狼人杀', 'min_players': 6, 'max_players': 12},
    {'id': 'undercover', 'name': '谁是卧底', 'min_players': 4, 'max_players': 12},
    {'id': 'draw-guess', 'name': '你画我猜', 'min_players': 3, 'max_players': 12},
    {'id': 'truth-dare', 'name': '真心话大冒险', 'min_players': 2, 'max_players': 12},
    {'id': 'turtle-soup', 'name': '海龟汤', 'min_players': 2, 'max_players': 12},
]


# ---------------------------------------------------------------------------
# 角色表
# ---------------------------------------------------------------------------
# 全局定义一次，版型只按 id 引用（见下面的 VARIANTS）。
#
# 加一个新角色 / 改夜晚顺序**不需要动任何逻辑代码**：
#   1. 往 ROLES 里加一项；
#   2. 在某个版型的 roles 里加一行 {'id': ..., 'count': n}。
# 夜晚的唤醒顺序会自动按 night.order 排出来（见 night_order()）；
# 同一个 action 的角色会被合并成一步（所以几只狼是一起睁眼的）。
#
# night.action 决定这一夜怎么结算，party/werewolf.py 里按它分派：
#   block  封锁       —— 狼妃专用，排在最前（见到队友之前）
#   dream  摄梦       —— 选一名「梦语者」；第二晚起还能直接让他出局。
#                        排在**最后**：她选完就能立刻读出结果，不用二次睁眼
#   kill   集体袭击   —— 多人各选一个，取票数最多的；平票随机取一个
#   guard  守护       —— 和 kill 撞同一个人 = 平安夜
#   check  个人查验   —— 各人记各人的结果，不合并（预言家）
#   grave  守墓人验尸 —— 查上一个被放逐出局的人是好人还是狼人
#   witch  女巫用药   —— 解药救人 / 毒药毒人，各一次
#
# 「7 人最后幸存者」的实际唤醒顺序（由下面的 night.order 自动排出来）：
#   狼妃封锁 → 狼人夜袭 → 女巫 → 守墓人 → 觉醒摄梦人
#
# hide: True 的角色（隐狼）**只在队友还活着时**被查验会显示成好人；
# 队友全出局后他就按普通狼人算（见 werewolf.is_disguised()）。
ROLES = {
    'wolf': {
        'id': 'wolf',
        'name': '狼人',
        'emoji': '🐺',
        'camp': 'wolf',
        'night': {
            'action': 'kill',
            'order': 30,
            'label': '刀人',
            'hint': '和同伴一起选一个人',
        },
    },
    'hidden_wolf': {
        'id': 'hidden_wolf',
        'name': '隐狼',
        'emoji': '🌑',
        'camp': 'wolf',
        # 队友还活着时查验显示成好人；队友全出局后按普通狼人算
        'hide': True,
        # 队友还活着时不参与袭击（见 werewolf.attackers_of()）
        'waits_for_pack': True,
        'night': {
            'action': 'kill',
            'order': 30,
            'label': '刀人',
            'hint': '队友还活着时你不参与袭击；队友全出局后你才开始刀人',
        },
    },
    'wolf_queen': {
        'id': 'wolf_queen',
        'name': '狼妃',
        'emoji': '👸',
        'camp': 'wolf',
        'night': {
            # 排在最前：先封锁，再去和队友见面
            'action': 'block',
            'order': 5,
            'label': '封锁',
            'hint': '在所有人行动前封锁一名玩家：他今晚受到的第一个技能会反弹回去。'
                    '可以连续两晚封锁同一个人；只要弹成功过一次，封锁就永久失效',
        },
    },

    'seer': {
        'id': 'seer',
        'name': '预言家',
        'emoji': '🔮',
        'camp': 'good',
        'night': {
            'action': 'check',
            'order': 10,
            'label': '查验',
            'hint': '选一个人，看看他是好人还是狼人',
        },
    },
    'dreamweaver': {
        'id': 'dreamweaver',
        'name': '觉醒摄梦人',
        'emoji': '💤',
        'camp': 'good',
        'night': {
            # 排在最后（order 最大）：她行动时所有人都表过态了，
            # 所以选完梦语者就能立刻读出他今晚动没动，不用二次睁眼。
            'action': 'dream',
            'order': 45,
            'label': '摄梦',
            'hint': '最后行动。选一名「梦语者」：他今晚免疫除你以外的一切伤害，'
                    '选完你立刻就知道他今晚有没有行动过；第二晚起还能直接让他出局。'
                    '不能连续两晚选同一个人',
        },
    },
    'guard': {
        'id': 'guard',
        'name': '守卫',
        'emoji': '🛡️',
        'camp': 'good',
        'night': {
            'action': 'guard',
            'order': 20,
            'label': '守护',
            'hint': '选一个人保护起来，不能连续两晚守同一个人',
        },
    },
    'witch': {
        'id': 'witch',
        'name': '女巫',
        'emoji': '🧪',
        'camp': 'good',
        'night': {
            # 排在狼人之后，这样女巫能看到今晚谁被袭击了，再决定救不救
            'action': 'witch',
            'order': 35,
            'label': '用药',
            'hint': '一瓶解药（救今晚被袭击的人，不能救自己）+ 一瓶毒药，每晚只能用其中一瓶',
        },
    },
    'gravedigger': {
        'id': 'gravedigger',
        'name': '守墓人',
        'emoji': '⚰️',
        'camp': 'good',
        'night': {
            'action': 'grave',
            'order': 40,
            'label': '验尸',
            'hint': '查看上一个被放逐出局的人是好人还是狼人。你永远被视为「行动过」',
        },
    },
    'hunter': {
        'id': 'hunter',
        'name': '猎人',
        'emoji': '🔫',
        'camp': 'good',
        'death': {'action': 'shoot', 'label': '开枪'},
    },
    'villager': {
        'id': 'villager',
        'name': '平民',
        'emoji': '👤',
        'camp': 'good',
    },
    'idiot': {
        'id': 'idiot',
        'name': '白痴',
        'emoji': '🤪',
        'camp': 'good',
        'vote': {'immune': True},
    },
}

# 「神职」= 好人阵营里带技能的角色（除平民外的好人）。屠神判定要用。
GOD_ROLE_IDS = ('seer', 'dreamweaver', 'guard', 'witch', 'gravedigger', 'hunter', 'idiot')


# ---------------------------------------------------------------------------
# 版型
# ---------------------------------------------------------------------------
# 有版型的游戏（目前只有狼人杀）：座位数、角色配置、阶段时长都由版型决定。
VARIANTS = {
    WEREWOLF_GAME_ID: [
        {
            'id': 'newbie-6',
            'name': '6 人新手场',
            'seats': 6,
            'desc': '六个人就能开，节奏快，适合第一次玩',
            # 版型只写「要哪些角色、几个」，具体定义在上面 ROLES 里
            'roles': [
                {'id': 'seer', 'count': 1},
                {'id': 'guard', 'count': 1},
                {'id': 'villager', 'count': 2},
                {'id': 'wolf', 'count': 2},
            ],
            # 白天要不要先来一轮「警长竞选」（目前两个版型都没有，留好口子）
            'sheriff': False,
            # 各阶段倒计时（秒）。**只作提示**，时间到了不会强制推进。
            'timing': {
                'speech': 120,        # 每人发言
                'last_words': 60,     # 遗言
                'vote': 60,           # 投票
                'night_default': 60,  # 夜晚行动默认秒数
                'night_by_role': {'wolf': 180},
            },
        },
        {
            'id': 'survivor-7',
            'name': '7 人最后幸存者',
            'seats': 7,
            'desc': '好人：觉醒摄梦人 / 女巫 / 猎人 / 守墓人 / 平民 —— 狼人：隐狼 / 狼妃',
            # 屠边：本版型只有 1 个平民，平民一出局狼人就赢
            #
            # ---- 本版型的补充说明（和小程序 utils/games.js 里的文案要对上）----
            # 1. 夜晚唤醒顺序：狼妃封锁 → 狼人夜袭 → 女巫 → 守墓人 → 觉醒摄梦人。
            #    摄梦人**最后**行动是她被动排到最后的结果：她选完梦语者就能立刻读出
            #    对方今晚动没动，不必让她中途二次睁眼（由 night.order 自动排出来，
            #    见上面 dreamweaver 的 order=45）。
            # 2. 狼妃可以连续两晚封锁同一个人（和网易狼人杀不同），
            #    但封锁**只要弹成功过一次就永久失效**（见 werewolf.settle_night 的
            #    maybe_bounce / block_broken）。
            # 3. 封锁只吃「当晚按行动顺序遇到的第一个技能」：如果女巫的毒药和摄梦人的
            #    梦语者指向同一个人，先弹的是毒药（弹回女巫自己），之后摄梦人的技能
            #    正常作用在这个目标身上 —— 所以结算里女巫要排在摄梦人之前处理。
            #    狼刀是集体行为，不参与反弹。
            'roles': [
                {'id': 'dreamweaver', 'count': 1},
                {'id': 'witch', 'count': 1},
                {'id': 'hunter', 'count': 1},
                {'id': 'gravedigger', 'count': 1},
                {'id': 'villager', 'count': 1},
                {'id': 'hidden_wolf', 'count': 1},
                {'id': 'wolf_queen', 'count': 1},
            ],
            'sheriff': False,
            'timing': {
                'speech': 120,
                'last_words': 60,
                'vote': 60,
                'night_default': 60,
                'night_by_role': {
                    'dreamweaver': 120,
                    'wolf': 180,
                    'hidden_wolf': 180,
                    'wolf_queen': 120,
                    'witch': 120,
                    'gravedigger': 60,
                },
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------

def game_by_id(game_id):
    for g in GAMES:
        if g['id'] == game_id:
            return g
    return None


def variants_of(game_id):
    """这个游戏的版型列表，没有就返回空数组。"""
    return VARIANTS.get(game_id, [])


def find_variant(game_id, variant_id):
    """
    按 id 找版型；不传 id 时返回第一个（默认版型）。
    返回 None 表示这个游戏没有版型。
    """
    items = variants_of(game_id)
    if not items:
        return None
    if not variant_id:
        return items[0]

    for variant in items:
        if variant['id'] == variant_id:
            return variant
    return None


def roles_of(variant):
    """
    把版型的 roles（{id, count}）展开成带完整定义的角色列表。
    找不到定义的角色会被跳过（免得配错了整个房间开不起来）。
    """
    if not variant:
        return []

    out = []
    for item in variant.get('roles', []):
        base = ROLES.get(item.get('id'))
        if base is None:
            continue
        role = dict(base)
        role['count'] = int(item.get('count', 1))
        out.append(role)
    return out


def night_order(variant):
    """
    这一夜里被依次唤醒的角色，按 night.order 从小到大。
    谁排第几完全由 ROLES 里的 order 决定，版型不用写顺序。
    """
    items = [r for r in roles_of(variant) if r.get('night')]
    items.sort(key=lambda r: r['night']['order'])
    return items


def timing_of(variant, key, default=0):
    timing = (variant or {}).get('timing') or {}
    return timing.get(key, default)


def night_seconds(variant, role_id):
    """这个角色夜晚行动的倒计时秒数（只作提示）。"""
    timing = (variant or {}).get('timing') or {}
    by_role = timing.get('night_by_role') or {}
    return by_role.get(role_id, timing.get('night_default', 60))


def role_of(role_id):
    return ROLES.get(role_id)


def is_wolf(role_id):
    role = ROLES.get(role_id)
    return bool(role and role.get('camp') == 'wolf')


def is_hidden_role(role_id):
    """这个角色**天生**带隐藏特性（隐狼）。是否真的还在隐藏，要看队友活没活着。"""
    role = ROLES.get(role_id)
    return bool(role and role.get('hide'))


def waits_for_pack(role_id):
    """队友还活着时就不参与袭击的角色（隐狼）。"""
    role = ROLES.get(role_id)
    return bool(role and role.get('waits_for_pack'))


def is_god(role_id):
    return role_id in GOD_ROLE_IDS
