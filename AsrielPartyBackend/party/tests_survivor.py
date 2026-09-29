"""
「7 人最后幸存者」版型的测试。

    .venv/Scripts/python.exe manage.py test party

版型构成：觉醒摄梦人 / 女巫 / 猎人 / 守墓人 / 平民 + 隐狼 / 狼妃
夜晚顺序：狼妃(封锁) → 狼(袭击) → 女巫 → 守墓人 → 觉醒摄梦人
（摄梦人排在**最后**：她选完梦语者就能立刻读出结果，不用二次睁眼）

覆盖：发牌、隐狼的动态身份与不参与袭击、摄梦人的梦语者保护与读心、
女巫不能自救、狼妃封锁的技能反弹与失效、守墓人验尸、猎人按死因能否开枪、
以及「1 个平民出局 = 狼人直接获胜」这条版型核心。
"""

from django.test import TestCase

from party.models import WerewolfGame
from party.tests_werewolf import WerewolfMixin

VARIANT_SEVEN = 'survivor-7'
SEVEN_ROLES = sorted([
    'dreamweaver', 'witch', 'hunter', 'gravedigger', 'villager',
    'hidden_wolf', 'wolf_queen',
])
WOLF_ROLES = ('hidden_wolf', 'wolf_queen')
GOOD_ROLES = ('dreamweaver', 'witch', 'hunter', 'gravedigger', 'villager')


class SurvivorSevenMixin(WerewolfMixin):
    def seat_seven(self):
        """开一局 7 人最后幸存者，返回 (room_id, {userId: token})。"""
        return self.open_room(VARIANT_SEVEN, 7, 's7')

    # ---------- 查询 ----------

    def by_role(self, room_id):
        """
        { 角色 id : userId }。

        测试里一律按**角色名**指定人，不要靠 `alive_ids` 的顺序挑 ——
        那种写法会在改动版型或玩家之后悄悄换成别人。
        """
        return {p.role_id: p.user_id for p in self.game_of(room_id).players.all()}

    def one_of(self, room_id, role_id):
        """这个角色的 userId（版型里每种只有 1 个）。"""
        players = self.players_by_role(room_id, role_id)
        self.assertEqual(len(players), 1, '%s 应该有 1 个' % role_id)
        return players[0].user_id

    def alive_of(self, room_id, user_id):
        game = self.game_of(room_id)
        players = [p for p in game.players.all() if p.user_id == user_id]
        self.assertTrue(players, '这个人应该在局里')
        return players[0].alive

    def current_step(self, room_id):
        """当前夜晚步骤；不在夜晚或已结束返回 None。"""
        game = self.game_of(room_id)
        if game.phase != WerewolfGame.PHASE_NIGHT:
            return None
        night = game.night or {}
        steps = night.get('steps') or []
        index = night.get('index', 0)
        if index >= len(steps):
            return None
        return steps[index]

    # ---------- 走完一整夜 ----------

    def auto_target(self, room_id, actor_id=None, avoid=()):
        """
        给没显式指定的那一步随便挑一个好人。

        `avoid` 用来避开这一晚已经有安排的人 —— 否则「封锁」可能恰好封到
        梦语者，把反弹误触发。
        """
        roles = self.role_by_user(room_id)
        pool = [
            u for u in self.alive_ids(room_id)
            if roles.get(u) in GOOD_ROLES
            and u != actor_id
            and u not in avoid
        ]
        return pool[0] if pool else None

    def dream_target(self, room_id, actor_id, avoid=()):
        """
        给摄梦人自动挑一个合法目标：不能是昨晚选过的人
        （「不能连续两晚选同一个人」）。手动指定目标时不用走这里。
        """
        game = self.game_of(room_id)
        me = [p for p in game.players.all() if p.user_id == actor_id][0]
        skip = set(avoid)
        if me.last_dream_target_id is not None:
            skip.add(me.last_dream_target_id)
        return self.auto_target(room_id, actor_id, skip)

    def run_seven_night(self, room_id, tokens, block=None, dream=None, wolf=None,
                        witch_skill='', witch_target=None, dream_skill=''):
        """
        把这一夜走完。没指定的选择会自动挑一个好人顶上。

        block         狼妃封锁谁
        dream         觉醒摄梦人选谁当梦语者
        dream_skill   'slay' 表示让梦语者出局
        wolf          狼袭击谁
        witch_skill   'save' / 'poison' / ''（不用药）
        witch_target  女巫毒谁（skill='poison' 时）
        """
        guard = 0
        while True:
            step = self.current_step(room_id)
            if step is None:
                return

            action = step['action']
            # 自动挑目标时要绕开这一晚已经有安排的人：否则「封锁」可能恰好封到
            # 梦语者或被毒的人，把反弹误触发（狼妃只吃第一个技能）。
            taken = {
                x for x in (block, dream, wolf, witch_target if witch_skill else None)
                if x is not None
            }

            for uid in step['actorIds']:
                if action == 'block':
                    target = block if block is not None else self.auto_target(room_id, uid, taken)
                    skill = ''
                elif action == 'dream':
                    target = dream if dream is not None else self.dream_target(room_id, uid, taken)
                    skill = dream_skill
                elif action == 'kill':
                    target = wolf if wolf is not None else self.auto_target(room_id, uid, taken)
                    skill = ''
                elif action == 'witch':
                    target, skill = witch_target, witch_skill
                else:                      # 守墓人不用选人
                    target, skill = None, ''

                res = self.act_with(room_id, tokens[uid], target, skill)
                self.assertEqual(res.status_code, 200, res.content)

            guard += 1
            self.assertLess(guard, 12, '夜晚没有正常结束')

    def walk_to_dream_step(self, room_id, tokens):
        """
        把夜晚一路走到「觉醒摄梦人行动」那一步 —— 她排在最**后**，
        所以前面还有封锁 / 夜袭 / 女巫 / 守墓人四步。

        返回 (走过的 action 列表, 各角色 userId 表)。
        """
        r = self.by_role(room_id)
        # 选两个一定还活着的人当靶子（结算发生在摄梦人行动之后，这里他们都还在场）
        block_target = r['hunter']
        wolf_target = r['gravedigger']

        order = []
        guard = 0
        while True:
            step = self.current_step(room_id)
            self.assertIsNotNone(step, '还没轮到摄梦人，夜晚就结束了')
            if step['action'] == 'dream':
                break

            order.append(step['action'])
            for uid in step['actorIds']:
                if step['action'] == 'block':
                    self.act_with(room_id, tokens[uid], block_target)
                elif step['action'] == 'kill':
                    self.act_with(room_id, tokens[uid], wolf_target)
                elif step['action'] == 'witch':
                    self.act_with(room_id, tokens[uid], None, '')
                else:
                    self.act_with(room_id, tokens[uid], None)

            guard += 1
            self.assertLess(guard, 8, '没走到摄梦人那一步')

        return order, r

    def pass_seven_night(self, room_id, tokens, **overrides):
        """
        走完第一夜，并且**不让任何人出局**。

        做法是让狼刀的目标正好是当晚的梦语者 —— 他对「除觉醒摄梦人以外」的
        一切伤害免疫，所以不用浪费女巫的解药，白天也不用处理猎人开枪。
        后面的剧本（第二夜）要用到几乎所有角色，第一夜死谁都可能把断言搞乱：
        平民一出局狼人就直接获胜，猎人一出局就要停下来等开枪。

        梦语者挑女巫：第二夜的剧本里她只会被「封锁」或「用药」碰到，
        不会当梦语者，所以不会撞上「不能连续两晚选同一个人」。
        用 overrides 换掉 dream 时狼刀目标不会跟着换，注意别把要留着的人刀了。
        """
        r = self.by_role(room_id)
        target = r['witch']
        choices = {'dream': target, 'wolf': target}
        choices.update(overrides)
        self.run_seven_night(room_id, tokens, **choices)

    def deal_to_second_night(self, room_id, tokens, lynch_role='hidden_wolf'):
        """
        从天亮宣布一路走到第二夜开始：发言完 → 投掉一个人 → 进下一夜。

        默认放逐**隐狼** —— 第二夜各家剧本都要用好人，投掉谁都会捣乱；
        狼队还剩狼妃，也不会立刻判出胜负。要看别的结果就传 lynch_role。
        """
        r = self.by_role(room_id)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        target = r[lynch_role]
        fallback_role = 'wolf_queen' if lynch_role != 'wolf_queen' else 'dreamweaver'
        fallback = r[fallback_role]
        self.vote_all(room_id, tokens, lambda uid: target if uid != target else fallback)
        self.advance(room_id, list(tokens.values())[0])


class SurvivorSevenDealTests(SurvivorSevenMixin, TestCase):
    def test_deal_composition(self):
        """发牌构成：摄梦人 / 女巫 / 猎人 / 守墓人 / 平民 + 隐狼 / 狼妃。"""
        room_id, tokens = self.seat_seven()
        roles = sorted(p.role_id for p in self.game_of(room_id).players.all())
        self.assertEqual(roles, SEVEN_ROLES)
        self.assertEqual(len(tokens), 7)

    def test_first_step_is_wolf_queen_block(self):
        """狼妃排在最前：先封锁，再去和队友见面。"""
        room_id, tokens = self.seat_seven()
        step = self.current_step(room_id)
        self.assertEqual(step['action'], 'block')
        self.assertEqual(step['roleIds'], ['wolf_queen'])
        self.assertEqual(step['actorIds'], [self.one_of(room_id, 'wolf_queen')])

    def test_hidden_wolf_does_not_join_the_kill(self):
        """隐狼在队友还活着时不参与袭击 —— 袭击步骤里只有狼妃一个人。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        step = self.current_step(room_id)
        self.assertEqual(step['action'], 'block')
        self.act_with(room_id, tokens[r['wolf_queen']], r['hunter'])

        # 狼妃之后轮到夜袭（摄梦人排在最后）
        step = self.current_step(room_id)
        self.assertEqual(step['action'], 'kill')
        self.assertEqual(step['actorIds'], [r['wolf_queen']], '隐狼这一晚不该参与袭击')
        self.assertNotIn(r['hidden_wolf'], step['actorIds'])

    def test_hidden_wolf_joins_after_pack_dies(self):
        """队友全部出局后，隐狼开始参与袭击，并且不再伪装。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        from party import werewolf as ww

        game = self.game_of(room_id)
        queen = [p for p in game.players.all() if p.user_id == r['wolf_queen']][0]
        queen.alive = False
        queen.save(update_fields=['alive'])

        game = self.game_of(room_id)
        hidden = [p for p in game.players.all() if p.user_id == r['hidden_wolf']][0]

        self.assertFalse(ww.is_disguised(game, hidden), '队友没了就不该再伪装')
        self.assertEqual(
            {p.user_id for p in ww.attackers_of(game)}, {r['hidden_wolf']},
            '这时该轮到隐狼自己刀人',
        )
        self.assertEqual(ww._check_result(game, hidden), 'wolf')


class SurvivorSevenDreamTests(SurvivorSevenMixin, TestCase):
    """觉醒摄梦人：梦语者的保护 + 第二晚起的击杀。"""

    def test_dream_target_immune_to_kill(self):
        """梦语者当晚免疫狼刀。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(room_id, tokens, dream=r['gravedigger'], wolf=r['gravedigger'])

        self.assertEqual(self.game_of(room_id).last_night_deaths, [], '梦语者应该免疫狼刀')

    def test_dream_target_immune_to_poison(self):
        """梦语者也免疫女巫的毒药。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            dream=r['gravedigger'], wolf=r['hunter'],
            witch_skill='poison', witch_target=r['gravedigger'],
        )

        deaths = self.game_of(room_id).last_night_deaths
        self.assertNotIn(r['gravedigger'], deaths, '梦语者应该免疫毒药')

    def test_cannot_pick_same_target_twice(self):
        """不能连续两晚选同一个梦语者。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.pass_seven_night(room_id, tokens, dream=r['hunter'])
        self.deal_to_second_night(room_id, tokens)

        # 走到摄梦人那一步
        step = self.current_step(room_id)
        while step is not None and step['action'] != 'dream':
            for uid in step['actorIds']:
                self.act_with(room_id, tokens[uid], r['villager'])
            step = self.current_step(room_id)

        self.assertIsNotNone(step, '应该轮到摄梦人')
        res = self.act_with(room_id, tokens[r['dreamweaver']], r['hunter'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('连续', res.json()['detail'])

    def test_cannot_slay_on_first_night(self):
        """第一晚还不能让梦语者出局。"""
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)

        self.assertEqual(order, ['block', 'kill', 'witch', 'grave'], '摄梦人该排在最后')

        res = self.act_with(room_id, tokens[r['dreamweaver']], r['hunter'], 'slay')
        self.assertEqual(res.status_code, 400)
        self.assertIn('第一晚', res.json()['detail'])

    def test_night_order_matches_the_variant(self):
        """夜晚唤醒顺序：狼妃封锁 → 狼人夜袭 → 女巫 → 守墓人 → 觉醒摄梦人。"""
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)

        self.assertEqual(order, ['block', 'kill', 'witch', 'grave'])
        self.assertEqual(self.current_step(room_id)['action'], 'dream')

        # 走完最后一步（摄梦人是两步：先定梦语者，再确认），整夜正常结束
        self.act_with(room_id, tokens[r['dreamweaver']], r['villager'])
        self.act_with(room_id, tokens[r['dreamweaver']], None)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_DAYBREAK)

    def test_dream_is_two_steps(self):
        """
        摄梦人一次行动分两步：**先定梦语者（这时就能读心），再决定要不要放逐他**。

        所以她选完目标不该直接把这一步做完 —— 得等她看过读心结果再点确认。
        """
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)
        dreamer = r['dreamweaver']

        # 第一步：定梦语者
        res = self.act_with(room_id, tokens[dreamer], r['gravedigger'])
        self.assertEqual(res.status_code, 200, res.content)

        # 还停在这一步（没确认就不算表态），但读心结果已经有了
        self.assertEqual(self.current_step(room_id)['action'], 'dream', '选完不该直接结束这一步')

        state = self.state_of(room_id, dreamer, tokens)
        self.assertTrue(state['night']['dreamPicked'], '应该记下她选的梦语者')
        self.assertFalse(state['night']['dreamConfirmed'], '还没确认')
        self.assertFalse(state['night']['canSlay'], '第一晚还不能让他出局')

        self.assertEqual(state['myDream']['targetUserId'], r['gravedigger'])
        self.assertTrue(state['myDream']['targetActed'], '守墓人永远算行动过')

        # 第二步：确认，这一夜这才算走完
        res = self.act_with(room_id, tokens[dreamer], None)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_DAYBREAK)

    def test_dream_second_step_can_slay_from_second_night(self):
        """第二晚起，第二步可以顺手让梦语者出局。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        target = r['gravedigger']

        # 先把前面几步走完，轮到摄梦人
        guard = 0
        step = self.current_step(room_id)
        while step is not None and step['action'] != 'dream':
            for uid in step['actorIds']:
                if step['action'] == 'block':
                    self.act_with(room_id, tokens[uid], r['hunter'])
                elif step['action'] == 'kill':
                    self.act_with(room_id, tokens[uid], r['villager'])
                elif step['action'] == 'witch':
                    self.act_with(room_id, tokens[uid], None, '')
                else:
                    self.act_with(room_id, tokens[uid], None)
            guard += 1
            self.assertLess(guard, 8, '没走到摄梦人那一步')
            step = self.current_step(room_id)

        self.assertIsNotNone(step, '第二晚该轮到摄梦人')

        # 第一步：定梦语者（这时该能选「让他出局」了）
        self.act_with(room_id, tokens[r['dreamweaver']], target)
        state = self.state_of(room_id, r['dreamweaver'], tokens)
        self.assertTrue(state['night']['canSlay'], '第二晚起可以让他出局')

        # 第二步：让他出局
        self.act_with(room_id, tokens[r['dreamweaver']], None, 'slay')
        self.assertFalse(self.alive_of(room_id, target), '摄梦人应该能带走梦语者')

    def test_dream_result_is_readable_right_after_picking(self):
        """摄梦人最后行动：她一选完，当场就能读出梦语者今晚动没动。"""
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)

        # 守墓人永远算「行动过」
        self.act_with(room_id, tokens[r['dreamweaver']], r['gravedigger'])
        state = self.state_of(room_id, r['dreamweaver'], tokens)
        dream = state.get('myDream')

        self.assertIsNotNone(dream, '选完就该立刻能看到读心结果')
        self.assertEqual(dream['targetUserId'], r['gravedigger'])
        self.assertTrue(dream['targetActed'], '守墓人永远算行动过')

    def test_dream_reads_villager_as_idle(self):
        """平民夜里本来就不动，摄梦人当场就能读出来。"""
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)

        self.act_with(room_id, tokens[r['dreamweaver']], r['villager'])
        state = self.state_of(room_id, r['dreamweaver'], tokens)
        dream = state.get('myDream')

        self.assertIsNotNone(dream)
        self.assertEqual(dream['targetUserId'], r['villager'])
        self.assertFalse(dream['targetActed'], '平民夜里不行动')

    def test_dream_does_not_leak_last_night_target(self):
        """新的一夜还没选人时，不该把上一晚的读心结果当成这一晚的。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        state = self.state_of(room_id, r['dreamweaver'], tokens)
        self.assertIsNone(state.get('myDream'), '这一晚还没选梦语者，不该有读心结果')

    def test_bounced_dream_reads_as_acted(self):
        """
        摄梦人选的梦语者正好是被狼妃封锁的人：技能弹回她自己身上。
        按规则她**察觉不到**，只会读到「行动过」。
        """
        from party import werewolf as ww
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        bait = r['villager']    # 平民夜里本来就不动，正好用来区分「被弹」和「没被弹」

        self.act_with(room_id, tokens[r['wolf_queen']], bait)             # 封锁平民
        self.act_with(room_id, tokens[r['wolf_queen']], r['hunter'])      # 夜袭别人
        self.act_with(room_id, tokens[r['witch']], None, '')              # 女巫不用药
        self.act_with(room_id, tokens[r['gravedigger']], None)            # 守墓人验尸

        res = self.act_with(room_id, tokens[r['dreamweaver']], bait)
        self.assertEqual(res.status_code, 200, res.content)

        # 选完就能读心（这时还没确认放不放逐）
        state = self.state_of(room_id, r['dreamweaver'], tokens)
        dream = state['myDream']
        self.assertEqual(dream['targetUserId'], bait, '她以为梦语者还是自己选的那个人')
        self.assertTrue(dream['targetActed'], '被弹回自己后只会读到「行动过」')

        # 确认，让这一夜结算
        res = self.act_with(room_id, tokens[r['dreamweaver']], None)
        self.assertEqual(res.status_code, 200, res.content)

        # 实际护住的是她自己
        game = self.game_of(room_id)
        dreamer = [p for p in game.players.all() if p.user_id == r['dreamweaver']][0]
        self.assertEqual(dreamer.last_dream_target_id, r['dreamweaver'])

        # 没被封锁时读心结果照常（平民 = 没行动过）
        self.assertFalse(ww.acted_tonight(game, game.night or {}, [p for p in game.players.all() if p.user_id == bait][0]))

    def test_dream_actual_target_previews_the_bounce(self):
        """结算前推演：目标被封锁时，实际护住的是摄梦人自己。"""
        from party import werewolf as ww
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        bait = r['villager']

        self.act_with(room_id, tokens[r['wolf_queen']], bait)    # 封锁平民，还没结算

        game = self.game_of(room_id)
        dreamer = [p for p in game.players.all() if p.user_id == r['dreamweaver']][0]

        self.assertEqual(ww._dream_actual_target(game, dreamer, bait), r['dreamweaver'])
        self.assertEqual(ww._dream_actual_target(game, dreamer, r['hunter']), r['hunter'])

    def test_dreamer_can_pick_itself(self):
        """觉醒摄梦人可以把自己定为梦语者（等于自保：当晚免疫别人给的伤害）。"""
        room_id, tokens = self.seat_seven()
        order, r = self.walk_to_dream_step(room_id, tokens)
        dreamer = r['dreamweaver']

        res = self.act_with(room_id, tokens[dreamer], dreamer)
        self.assertEqual(res.status_code, 200, res.content)

        state = self.state_of(room_id, dreamer, tokens)
        self.assertTrue(state['night']['dreamPicked'], '应该记下她自己')
        self.assertEqual(state['myDream']['targetUserId'], dreamer, '梦语者可以就是她自己')
        self.assertTrue(state['myDream']['targetActed'], '她自己当然算行动过')

        # 确认，这一夜正常结束
        self.act_with(room_id, tokens[dreamer], None)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_DAYBREAK)

    def test_self_dream_is_immune_to_the_kill(self):
        """把自己定为梦语者之后，狼刀砍不动她。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            dream=r['dreamweaver'], wolf=r['dreamweaver'],
        )

        self.assertTrue(
            self.alive_of(room_id, r['dreamweaver']),
            '梦语者免疫狼刀，自己也不例外',
        )
        self.assertEqual(self.game_of(room_id).last_night_deaths, [])

    def test_can_slay_from_second_night(self):
        """第二晚起，摄梦人可以让梦语者出局（无视梦语者自带的保护）。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        self.run_seven_night(
            room_id, tokens,
            block=r['witch'], dream=r['gravedigger'], dream_skill='slay', wolf=r['villager'],
        )

        self.assertFalse(self.alive_of(room_id, r['gravedigger']), '摄梦人应该能带走梦语者')


class SurvivorSevenReadTests(SurvivorSevenMixin, TestCase):
    """觉醒摄梦人的「读心」：判断梦语者当晚有没有行动过。"""

    def acted(self, room_id, role_id):
        from party import werewolf as ww

        game = self.game_of(room_id)
        target = [p for p in game.players.all() if p.role_id == role_id][0]
        return ww.acted_tonight(game, game.night or {}, target)

    def test_gravedigger_always_counts_as_acted(self):
        """守墓人永远算「行动过」，哪怕白天没人被放逐。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.assertTrue(self.acted(room_id, 'gravedigger'))

    def test_dreamweaver_always_counts_as_acted(self):
        """摄梦人自己永远算行动过（他每晚必须选人）。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.assertTrue(self.acted(room_id, 'dreamweaver'))

    def test_wolf_queen_always_counts_as_acted(self):
        """狼妃排在最前封锁，也算行动过。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.assertTrue(self.acted(room_id, 'wolf_queen'))

    def test_hunter_and_villager_read_as_idle(self):
        """猎人和平民夜里本来就不动。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.assertFalse(self.acted(room_id, 'hunter'))
        self.assertFalse(self.acted(room_id, 'villager'))

    def test_hidden_wolf_reads_as_idle_while_pack_alive(self):
        """隐狼在队友活着时读出来是「没行动过」。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.assertFalse(self.acted(room_id, 'hidden_wolf'))

    def test_witch_counts_only_when_using_a_potion(self):
        """女巫不用药就算「没行动过」；用了药才算。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)          # 默认不用药
        self.assertFalse(self.acted(room_id, 'witch'), '不用药应该读成没行动')

        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        self.run_seven_night(
            room_id, tokens,
            dream=r['gravedigger'], wolf=r['hunter'], witch_skill='save',
        )
        self.assertTrue(self.acted(room_id, 'witch'), '用了药才算行动过')


class SurvivorSevenWitchTests(SurvivorSevenMixin, TestCase):
    def test_cannot_heal_self(self):
        """解药不能救自己。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        # 狼妃封锁一个无关的人，再去袭击女巫
        self.act_with(room_id, tokens[r['wolf_queen']], r['hunter'])
        self.act_with(room_id, tokens[r['dreamweaver']], r['hunter'])
        self.act_with(room_id, tokens[r['wolf_queen']], r['witch'])

        res = self.act_with(room_id, tokens[r['witch']], None, 'save')
        self.assertEqual(res.status_code, 400)
        self.assertIn('不能救自己', res.json()['detail'])

    def test_heal_saves_the_victim(self):
        """解药把被袭击的人救回来。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            dream=r['gravedigger'], wolf=r['hunter'], witch_skill='save',
        )

        self.assertEqual(self.game_of(room_id).last_night_deaths, [], '解药应该救回被袭击的人')
        witch = [p for p in self.game_of(room_id).players.all() if p.user_id == r['witch']][0]
        self.assertTrue(witch.heal_used)


class SurvivorSevenBlockTests(SurvivorSevenMixin, TestCase):
    """狼妃的封锁：被封锁的人，当晚受到的第一个技能会反弹回去。"""

    def test_poison_bounces_back_to_witch(self):
        """女巫毒被封锁的人 -> 毒死在女巫自己身上。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            block=r['hunter'], dream=r['villager'], wolf=r['gravedigger'],
            witch_skill='poison', witch_target=r['hunter'],
        )

        deaths = self.game_of(room_id).last_night_deaths
        self.assertIn(r['witch'], deaths, '毒药应该弹回女巫自己身上')
        self.assertNotIn(r['hunter'], deaths, '被封锁的人不该被毒死')
        self.assertFalse(self.alive_of(room_id, r['witch']))

    def test_dream_bounces_back_to_dreamweaver(self):
        """摄梦人选了被封锁的人 -> 实际变成摄梦自己，于是他免疫当晚伤害。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            block=r['hunter'], dream=r['hunter'], wolf=r['dreamweaver'],
        )

        self.assertTrue(self.alive_of(room_id, r['dreamweaver']),
                        '反噬后摄梦人成了梦语者，应该免疫狼刀')
        self.assertEqual(self.game_of(room_id).last_night_deaths, [])

    def test_slay_after_bounce_kills_dreamweaver(self):
        """反弹后摄梦人若选「让他出局」，出局的其实是摄梦人自己；封锁随之失效。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        self.run_seven_night(
            room_id, tokens,
            block=r['hunter'], dream=r['hunter'], dream_skill='slay',
        )

        self.assertFalse(self.alive_of(room_id, r['dreamweaver']),
                         '反弹后出局的应该是摄梦人自己')

        game = self.game_of(room_id)
        queen = [p for p in game.players.all() if p.user_id == r['wolf_queen']][0]
        self.assertIsNotNone(queen.block_broken_day, '弹过一次后封锁应该失效')

    def test_block_has_no_effect_after_it_broke(self):
        """封锁失效后，再封锁也不会反弹。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)

        r = self.by_role(room_id)
        game = self.game_of(room_id)
        queen = [p for p in game.players.all() if p.user_id == r['wolf_queen']][0]
        queen.block_broken_day = 1
        queen.save(update_fields=['block_broken_day'])

        self.deal_to_second_night(room_id, tokens)

        self.run_seven_night(
            room_id, tokens,
            block=r['hunter'], dream=r['villager'], wolf=r['gravedigger'],
            witch_skill='poison', witch_target=r['hunter'],
        )

        deaths = self.game_of(room_id).last_night_deaths
        self.assertIn(r['hunter'], deaths, '封锁失效后毒药应该正常生效')
        self.assertNotIn(r['witch'], deaths)

    def test_poison_bounces_first_then_dream_lands(self):
        """
        毒药和摄梦人的梦语者选了同一个人：先弹的是**毒药**（弹回女巫自己），
        之后摄梦人的技能照常作用在这个目标身上。
        """
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        target = r['hunter']

        self.run_seven_night(
            room_id, tokens,
            block=target, dream=target, wolf=r['gravedigger'],
            witch_skill='poison', witch_target=target,
        )

        game = self.game_of(room_id)
        deaths = game.last_night_deaths

        self.assertIn(r['witch'], deaths, '毒药该被弹回女巫自己')
        self.assertNotIn(target, deaths, '梦语者免疫除摄梦人以外的一切伤害')
        self.assertTrue(self.alive_of(room_id, target))

        dreamer = [p for p in game.players.all() if p.user_id == r['dreamweaver']][0]
        self.assertEqual(
            dreamer.last_dream_target_id, target,
            '毒药已经把封锁用掉了，摄梦人的技能该正常作用在目标身上',
        )

        queen = [p for p in game.players.all() if p.user_id == r['wolf_queen']][0]
        self.assertIsNotNone(queen.block_broken_day, '弹过一次后封锁就永久失效')

    def test_wolf_kill_does_not_bounce(self):
        """狼刀是集体行为，不参与反弹：刀到被封锁的人照样刀死，封锁也不算用掉。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        target = r['hunter']

        self.run_seven_night(
            room_id, tokens,
            block=target, dream=r['villager'], wolf=target,
        )

        game = self.game_of(room_id)
        self.assertIn(target, game.last_night_deaths, '狼刀不该被封锁弹走')

        queen = [p for p in game.players.all() if p.user_id == r['wolf_queen']][0]
        self.assertIsNone(queen.block_broken_day, '没弹过技能，封锁不该失效')

    def test_can_block_the_same_target_two_nights_running(self):
        """本版型的狼妃**可以**连续两晚封锁同一个人（网易狼人杀不允许）。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)
        target = r['villager']

        self.pass_seven_night(room_id, tokens, block=target)
        self.deal_to_second_night(room_id, tokens)

        step = self.current_step(room_id)
        self.assertEqual(step['action'], 'block')

        res = self.act_with(room_id, tokens[r['wolf_queen']], target)
        self.assertEqual(res.status_code, 200, res.content)

        night = self.game_of(room_id).night or {}
        self.assertEqual(night.get('blocked'), target, '第二晚照样能封同一个人')


class SurvivorSevenGraveTests(SurvivorSevenMixin, TestCase):
    def test_gravedigger_sees_lynched_side(self):
        """守墓人能查出被放逐的人是好人还是狼。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        grave = r['gravedigger']
        if not self.alive_of(room_id, grave):
            return    # 第一夜恰好把守墓人刀掉了

        game = self.game_of(room_id)
        lynched = game.last_lynched_user_id
        self.assertIsNotNone(lynched)

        self.run_seven_night(room_id, tokens)

        state = self.state_of(room_id, grave, tokens)
        checks = state.get('myChecks') or []
        row = [c for c in checks if c['userId'] == lynched]
        self.assertTrue(row, '守墓人应该拿到验尸结果: %s' % checks)

        expected = 'wolf' if self.role_by_user(room_id)[lynched] in WOLF_ROLES else 'good'
        self.assertEqual(row[0]['result'], expected)

    def test_gravedigger_sees_hidden_wolf_as_wolf(self):
        """隐狼被放逐后，守墓人当晚会明确得知他是狼人（伪装对守墓人无效）。"""
        room_id, tokens = self.seat_seven()
        from party import werewolf as ww

        game = self.game_of(room_id)
        hidden = [p for p in game.players.all() if p.role_id == 'hidden_wolf'][0]

        # 预言家视角：隐狼显示好人
        self.assertEqual(ww._check_result(game, hidden), 'good')
        # 守墓人视角：隐狼显示狼人
        self.assertEqual(ww._grave_result(hidden), 'wolf')


class SurvivorSevenHunterTests(SurvivorSevenMixin, TestCase):
    def test_hunter_can_shoot_after_being_lynched(self):
        """猎人被放逐后可以开枪。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        r = self.by_role(room_id)
        hunter = r['hunter']
        self.vote_all(
            room_id, tokens,
            lambda uid: hunter if uid != hunter else r['villager'],
        )

        game = self.game_of(room_id)
        self.assertEqual(game.pending_shooter_id, hunter, '被放逐的猎人应该能开枪')

        ammo = r['hidden_wolf']
        res = self.act(room_id, tokens[hunter], ammo)
        self.assertEqual(res.status_code, 200, res.content)

        game.refresh_from_db()
        self.assertIsNone(game.pending_shooter_id)
        self.assertFalse(self.alive_of(room_id, ammo))

    def test_hunter_can_shoot_after_being_killed_at_night(self):
        """猎人被狼袭击出局后也能开枪。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(room_id, tokens, dream=r['villager'], wolf=r['hunter'])

        game = self.game_of(room_id)
        self.assertIn(r['hunter'], game.last_night_deaths)
        self.assertEqual(game.pending_shooter_id, r['hunter'])

    def test_poisoned_hunter_cannot_shoot(self):
        """被女巫毒死的猎人不能开枪。"""
        room_id, tokens = self.seat_seven()
        r = self.by_role(room_id)

        self.run_seven_night(
            room_id, tokens,
            dream=r['villager'], wolf=r['gravedigger'],
            witch_skill='poison', witch_target=r['hunter'],
        )

        game = self.game_of(room_id)
        self.assertIn(r['hunter'], game.last_night_deaths)
        self.assertIsNone(game.pending_shooter_id, '被毒死的猎人不能开枪')

    def test_slain_hunter_cannot_shoot(self):
        """被觉醒摄梦人带走的猎人不能开枪。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.deal_to_second_night(room_id, tokens)

        r = self.by_role(room_id)
        self.run_seven_night(
            room_id, tokens,
            block=r['witch'], dream=r['hunter'], dream_skill='slay', wolf=r['villager'],
        )

        game = self.game_of(room_id)
        self.assertIn(r['hunter'], game.last_night_deaths)
        self.assertIsNone(game.pending_shooter_id, '被摄梦人带走的猎人不能开枪')


class SurvivorSevenWinTests(SurvivorSevenMixin, TestCase):
    def test_villager_out_means_wolves_win(self):
        """这个版型只有 1 个平民 —— 平民出局狼人直接获胜。"""
        room_id, tokens = self.seat_seven()
        from party import werewolf as ww

        game = self.game_of(room_id)
        villager = [p for p in game.players.all() if p.role_id == 'villager'][0]
        villager.alive = False
        villager.save(update_fields=['alive'])

        game = self.game_of(room_id)
        ww.check_winner(game)
        game.refresh_from_db()

        self.assertEqual(game.winner, 'wolf')
        self.assertIn('平民', game.win_reason)

    def test_good_wins_when_both_wolves_out(self):
        """两只狼都出局 -> 好人获胜。"""
        room_id, tokens = self.seat_seven()
        from party import werewolf as ww

        game = self.game_of(room_id)
        for p in game.players.all():
            if p.role_id in WOLF_ROLES:
                p.alive = False
                p.save(update_fields=['alive'])

        game = self.game_of(room_id)
        ww.check_winner(game)
        game.refresh_from_db()

        self.assertEqual(game.winner, 'good')
        self.assertIn('狼人', game.win_reason)

    def test_others_cannot_advance_while_hunter_pending(self):
        """猎人还没开枪时，别人点推进会被拦下。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        r = self.by_role(room_id)
        hunter = r['hunter']
        self.vote_all(
            room_id, tokens,
            lambda uid: hunter if uid != hunter else r['villager'],
        )

        game = self.game_of(room_id)
        if not game.pending_shooter_id:
            return    # 猎人已经出局过了，这条测试不适用

        other = r['witch'] if hunter != r['witch'] else r['gravedigger']
        res = self.advance(room_id, tokens[other])
        self.assertEqual(res.status_code, 400)
        self.assertIn('开枪', res.json()['detail'])


class SurvivorSevenVoteTests(SurvivorSevenMixin, TestCase):
    """放逐投票的一些边界。"""

    def to_vote(self, room_id, tokens):
        """从第一夜走到放逐投票。"""
        self.pass_seven_night(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_VOTE)

    def test_can_vote_for_self(self):
        """放逐投票可以投给自己（票是自己的，想投给谁都行）。"""
        room_id, tokens = self.seat_seven()
        self.to_vote(room_id, tokens)

        r = self.by_role(room_id)
        me = r['villager']
        res = self.post(
            '/api/rooms/werewolf/vote',
            {'roomId': room_id, 'targetUserId': me},
            token=tokens[me],
        )
        self.assertEqual(res.status_code, 200, res.content)

        player = [p for p in self.game_of(room_id).players.all() if p.user_id == me][0]
        self.assertEqual(player.vote_target_id, me, '票应该能投给自己')

    def test_votes_are_secret_until_settled(self):
        """投票进行中，别人看不出我投没投 —— 只有自己知道自己投了。"""
        room_id, tokens = self.seat_seven()
        self.to_vote(room_id, tokens)

        r = self.by_role(room_id)
        # pass_seven_night 保证第一夜没人出局，所以 7 个人都在
        alive = [uid for uid in r.values() if self.alive_of(room_id, uid)]
        me, other = alive[0], alive[1]

        res = self.post(
            '/api/rooms/werewolf/vote',
            {'roomId': room_id, 'targetUserId': other},
            token=tokens[me],
        )
        self.assertEqual(res.status_code, 200, res.content)

        # 自己投没投，自己知道
        my_row = [p for p in self.state_of(room_id, me, tokens)['players'] if p['isMe']][0]
        self.assertTrue(my_row['voted'])

        # 但别人看到的永远是「没投」—— 投票期间后端不下发任何人的投票状态
        seen = self.state_of(room_id, other, tokens)
        self.assertFalse(
            [p for p in seen['players'] if p['userId'] == me][0]['voted'],
            '投票没结束时不该泄露别人的投票状态',
        )
        self.assertTrue(
            all(not p['voted'] for p in seen['players']),
            '别人的投票状态一律看不到',
        )

    def test_vote_ballots_are_published_when_settled(self):
        """票型公布的时候，要看得到「谁投给了谁」。"""
        room_id, tokens = self.seat_seven()
        self.to_vote(room_id, tokens)

        r = self.by_role(room_id)
        alive = [uid for uid in r.values() if self.alive_of(room_id, uid)]
        target = alive[0]

        # target 弃票，其他人全投他
        for uid in alive[1:]:
            res = self.post(
                '/api/rooms/werewolf/vote',
                {'roomId': room_id, 'targetUserId': target},
                token=tokens[uid],
            )
            self.assertEqual(res.status_code, 200, res.content)

        # 弃票也算表态 —— 不投的话这一轮不会结算
        res = self.post(
            '/api/rooms/werewolf/vote',
            {'roomId': room_id, 'targetUserId': None},
            token=tokens[target],
        )
        self.assertEqual(res.status_code, 200, res.content)

        game = self.game_of(room_id)
        self.assertIn(game.phase, (WerewolfGame.PHASE_RESULT, WerewolfGame.PHASE_OVER))

        result = game.round_result
        self.assertTrue(result, '结算后应该有 round_result')

        ballots = {b['voterUserId']: b for b in result['ballots']}
        self.assertEqual(len(ballots), len(alive), '每个能投票的人都要有一条记录（弃票也算）')
        self.assertEqual(ballots[alive[1]]['targetUserId'], target, '要能看出谁投给了谁')
        self.assertEqual(ballots[alive[1]]['targetNickname'], result['outNickname'])
        self.assertIsNone(ballots[target]['targetUserId'], '弃票的人 target 是空的')

        counts = {row['userId']: row['count'] for row in result['tally']}
        self.assertEqual(counts[target], len(alive) - 1)

    def test_game_log_records_public_events(self):
        """进程记录要有夜晚 / 天亮，而且**身份不能进去**。"""
        room_id, tokens = self.seat_seven()
        self.pass_seven_night(room_id, tokens)

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        log = state['gameLog']
        self.assertTrue(log, '应该有进程记录')

        kinds = [e['kind'] for e in log]
        self.assertIn('night', kinds, '记下了第几夜')
        self.assertIn('daybreak', kinds, '记下了天亮的死讯')

        for event in log:
            self.assertNotIn('roleId', event)
            self.assertNotIn('outRoleId', event)

    def test_vote_log_carries_the_ballots(self):
        """投票结算那次，进程记录里也要带上「谁投了谁」。"""
        room_id, tokens = self.seat_seven()
        self.to_vote(room_id, tokens)

        r = self.by_role(room_id)
        alive = [uid for uid in r.values() if self.alive_of(room_id, uid)]
        target = alive[0]

        for uid in alive[1:]:
            self.post(
                '/api/rooms/werewolf/vote',
                {'roomId': room_id, 'targetUserId': target},
                token=tokens[uid],
            )
        # 弃票也算表态
        self.post(
            '/api/rooms/werewolf/vote',
            {'roomId': room_id, 'targetUserId': None},
            token=tokens[target],
        )

        log = self.state_of(room_id, alive[1], tokens)['gameLog']
        votes = [e for e in log if e['kind'] == 'vote']
        self.assertTrue(votes, '投票结算应该写进进程记录')

        last = votes[-1]
        self.assertTrue(last.get('ballots'), '记录里要带谁投了谁')
        self.assertEqual(len(last['ballots']), len(alive), '每个投票人都要有记录')
