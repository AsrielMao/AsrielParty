"""
狼人杀 6 人新手场的集成测试（7 人最后幸存者见 party/tests_survivor.py）。

    .venv/Scripts/python.exe manage.py test party

版型：预言家 / 守卫 / 2 平民 / 2 狼
覆盖：开局发牌 → 夜晚按角色依次行动与结算（同守同救 = 平安夜）→ 天亮宣布
→ 发言顺序 → 放逐投票 → 平票 PK → 屠边胜负，
以及「别人的身份不会被下发」这条底线。

这里也放着两个版型共用的 `WerewolfMixin`（请求、开房、投票等辅助）。
"""

import json

from django.test import Client, TestCase

from party.constants import WEREWOLF_GAME_ID
from party.models import WerewolfGame


class WerewolfMixin:
    """两个版型的测试共用的辅助。"""

    def setUp(self):
        self.client = Client()

    # ---------- 请求 ----------

    def post(self, path, data=None, token=None):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        return self.client.post(
            path,
            data=json.dumps(data or {}),
            content_type='application/json',
            **headers
        )

    def get(self, path, token=None, **params):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        return self.client.get(path, params, **headers)

    # ---------- 建局 ----------

    def login(self, code, nickname):
        res = self.post('/api/auth/login', {'code': code, 'nickname': nickname})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        return body['token'], body['user']

    def open_room(self, variant_id, seats, prefix):
        """开一个房间并坐满，开局，返回 (room_id, {userId: token})。"""
        owner_token, owner = self.login(prefix + 'owner', '房主')
        room = self.post(
            '/api/rooms/create',
            {'gameId': WEREWOLF_GAME_ID, 'variantId': variant_id, 'title': '测试局'},
            token=owner_token,
        ).json()
        self.assertEqual(room['maxPlayers'], seats, room)
        self.assertEqual(room['variantId'], variant_id)

        tokens = {owner['id']: owner_token}
        for i in range(seats - 1):
            token, user = self.login('%sp%d' % (prefix, i), 'P%s' % i)
            self.post('/api/rooms/join', {'roomId': room['id']}, token=token)
            tokens[user['id']] = token

        res = self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
        self.assertEqual(res.status_code, 200, res.content)
        return room['id'], tokens

    def seat_six(self):
        """开一局 6 人新手场。"""
        return self.open_room('newbie-6', 6, 'six')

    # ---------- 查询 ----------

    def game_of(self, room_id):
        return WerewolfGame.objects.get(room_id=room_id)

    def players_by_role(self, room_id, role_id):
        game = self.game_of(room_id)
        return [p for p in game.players.all() if p.role_id == role_id]

    def alive_ids(self, room_id):
        game = self.game_of(room_id)
        return [p.user_id for p in game.players.all() if p.alive]

    def role_by_user(self, room_id):
        return {p.user_id: p.role_id for p in self.game_of(room_id).players.all()}

    def state_of(self, room_id, user_id, tokens):
        detail = self.get('/api/rooms/detail', token=tokens[user_id], roomId=room_id).json()
        return detail['gameState']

    # ---------- 动作 ----------

    def act(self, room_id, token, target_user_id=None):
        return self.post(
            '/api/rooms/werewolf/act',
            {'roomId': room_id, 'targetUserId': target_user_id},
            token=token,
        )

    def act_with(self, room_id, token, target_user_id=None, skill=''):
        return self.post(
            '/api/rooms/werewolf/act',
            {'roomId': room_id, 'targetUserId': target_user_id, 'skill': skill},
            token=token,
        )

    def vote(self, room_id, token, target_user_id=None):
        return self.post(
            '/api/rooms/werewolf/vote',
            {'roomId': room_id, 'targetUserId': target_user_id},
            token=token,
        )

    def advance(self, room_id, token):
        return self.post('/api/rooms/werewolf/advance', {'roomId': room_id}, token=token)

    def finish_speech(self, room_id, tokens):
        """把当前发言轮走完（谁在发言就由谁推进）。"""
        guard = 0
        while self.game_of(room_id).phase in ('speech', 'pk_speech'):
            state = self.state_of(room_id, list(tokens.keys())[0], tokens)
            speaker = state.get('currentSpeakerUserId')
            self.assertIsNotNone(speaker, '应该有当前发言人')
            res = self.advance(room_id, tokens[speaker])
            self.assertEqual(res.status_code, 200, res.content)
            guard += 1
            self.assertLess(guard, 30, '发言轮没有正常结束')

    def vote_all(self, room_id, tokens, target_of):
        """按 target_of(userId) -> 目标 userId（None 表示弃票）把票投完。"""
        alive = self.alive_ids(room_id)
        for user_id in alive:
            res = self.vote(room_id, tokens[user_id], target_of(user_id))
            if self.game_of(room_id).phase not in ('vote', 'pk_vote'):
                # 全员投完已经自动结算了，剩下的人没票可投
                return
            self.assertEqual(res.status_code, 200, res.content)


class WerewolfMixin6(WerewolfMixin):
    """6 人新手场额外的辅助。"""

    def run_night(self, room_id, tokens, wolf_target, guard_target=None, seer_target=None):
        """
        按 预言家 → 守卫 → 狼人 的顺序把这一夜走完。
        guard_target 为 None 时守一个和狼刀不同的人（用来制造出局）。
        """
        alive = self.alive_ids(room_id)

        for seer in self.players_by_role(room_id, 'seer'):
            if not seer.alive:
                continue
            target = seer_target or [u for u in alive if u != seer.user_id][0]
            res = self.act(room_id, tokens[seer.user_id], target)
            self.assertEqual(res.status_code, 200, res.content)

        guards = [g for g in self.players_by_role(room_id, 'guard') if g.alive]
        if guards:
            guard = guards[0]
            others = [u for u in alive if u != guard.user_id]
            if guard_target is None:
                # 挑一个不是狼刀目标的人来守（制造出局）
                candidates = [u for u in others if u != wolf_target]
                guard_target = candidates[0] if candidates else None
            if guard_target is not None:
                res = self.act(room_id, tokens[guard.user_id], guard_target)
                self.assertEqual(res.status_code, 200, res.content)

        for wolf in self.players_by_role(room_id, 'wolf'):
            if not wolf.alive:
                continue
            res = self.act(room_id, tokens[wolf.user_id], wolf_target)
            self.assertEqual(res.status_code, 200, res.content)

    def two_nights_to_day(self, room_id, tokens):
        """走完第一个夜晚，天亮（刀一个、守另一个，保证有人出局）。"""
        alive = self.alive_ids(room_id)
        wolves = {p.user_id for p in self.players_by_role(room_id, 'wolf')}
        guard = self.players_by_role(room_id, 'guard')[0]
        candidates = [u for u in alive if u not in wolves and u != guard.user_id]
        self.run_night(room_id, tokens, wolf_target=candidates[0], guard_target=candidates[1])


class WerewolfDealTests(WerewolfMixin6, TestCase):
    def test_deal_and_enter_first_night(self):
        """开局：按版型发身份，然后直接进入第一个夜晚，第一个行动的是预言家。"""
        room_id, tokens = self.seat_six()

        game = self.game_of(room_id)
        self.assertEqual(game.phase, WerewolfGame.PHASE_NIGHT)
        self.assertEqual(game.day, 0)

        roles = sorted(p.role_id for p in game.players.all())
        self.assertEqual(roles, sorted(['seer', 'guard', 'villager', 'villager', 'wolf', 'wolf']))

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertEqual(state['phase'], 'night')
        self.assertEqual(state['night']['stepRoleIds'], ['seer'])
        self.assertEqual(state['night']['actorCount'], 1)

    def test_own_role_visible_others_hidden(self):
        """自己能看身份；别人（非狼同伴）看不到；狼人能看到同伴。"""
        room_id, tokens = self.seat_six()
        game = self.game_of(room_id)

        for user_id, token in tokens.items():
            state = self.state_of(room_id, user_id, tokens)
            self.assertTrue(state['myRoleId'], '自己的身份应该能看到')

            mine = [p for p in game.players.all() if p.user_id == user_id][0]
            am_wolf = mine.role_id == 'wolf'

            for row in state['players']:
                if row['userId'] == user_id:
                    self.assertEqual(row['roleId'], mine.role_id)
                    continue
                if am_wolf and [p for p in game.players.all() if p.user_id == row['userId']][0].role_id == 'wolf':
                    # 狼同伴互相可见
                    self.assertEqual(row.get('roleId'), 'wolf')
                    continue
                self.assertNotIn('roleId', row, '别人的身份不该下发')


class WerewolfNightTests(WerewolfMixin6, TestCase):
    def test_guard_saves_victim_peaceful_night(self):
        """守卫守的人和狼刀的人是同一个 -> 平安夜。"""
        room_id, tokens = self.seat_six()
        alive = self.alive_ids(room_id)

        # 挑一个不是守卫也不是狼的目标
        guard = self.players_by_role(room_id, 'guard')[0]
        wolves = {p.user_id for p in self.players_by_role(room_id, 'wolf')}
        target = [u for u in alive if u != guard.user_id and u not in wolves][0]

        self.run_night(room_id, tokens, wolf_target=target, guard_target=target)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, WerewolfGame.PHASE_DAYBREAK)
        self.assertEqual(game.day, 1)
        self.assertEqual(game.last_night_deaths, [])

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertTrue(state['peacefulNight'])
        self.assertEqual(state['lastNightDeaths'], [])

    def test_wolf_kills_when_not_guarded(self):
        """狼刀的人和守卫守的人不同 -> 被刀的人出局，并且只公布编号不公布身份。"""
        room_id, tokens = self.seat_six()
        alive = self.alive_ids(room_id)

        guard = self.players_by_role(room_id, 'guard')[0]
        wolves = {p.user_id for p in self.players_by_role(room_id, 'wolf')}
        candidates = [u for u in alive if u not in wolves and u != guard.user_id]
        victim = candidates[0]
        guarded = candidates[1] if len(candidates) > 1 else None

        self.run_night(room_id, tokens, wolf_target=victim, guard_target=guarded)

        game = self.game_of(room_id)
        self.assertEqual(game.last_night_deaths, [victim])

        victim_row = [p for p in game.players.all() if p.user_id == victim][0]
        self.assertFalse(victim_row.alive)

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertEqual(len(state['lastNightDeaths']), 1)
        self.assertEqual(state['lastNightDeaths'][0]['userId'], victim)
        # 死讯里不能带身份
        self.assertNotIn('roleId', state['lastNightDeaths'][0])

    def test_act_only_on_your_turn(self):
        """不是自己角色的回合，调 act 会被拒绝。"""
        room_id, tokens = self.seat_six()
        wolves = self.players_by_role(room_id, 'wolf')
        alive = self.alive_ids(room_id)

        # 第一个行动的是预言家，狼人此时不该能动
        res = self.act(room_id, tokens[wolves[0].user_id], alive[0])
        self.assertEqual(res.status_code, 400)
        self.assertIn('轮到', res.json()['detail'])

    def test_seer_sees_check_result(self):
        """预言家查验后能看到结果，别人看不到。"""
        room_id, tokens = self.seat_six()
        seer = self.players_by_role(room_id, 'seer')[0]
        wolves = {p.user_id for p in self.players_by_role(room_id, 'wolf')}
        target = sorted(wolves)[0]

        res = self.act(room_id, tokens[seer.user_id], target)
        self.assertEqual(res.status_code, 200, res.content)

        seer_state = self.state_of(room_id, seer.user_id, tokens)
        checks = seer_state.get('myChecks') or []
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]['userId'], target)
        self.assertEqual(checks[0]['result'], 'wolf')

        # 别人拿不到查验记录
        other = [u for u in tokens if u != seer.user_id][0]
        self.assertNotIn('myChecks', self.state_of(room_id, other, tokens))


class WerewolfDayTests(WerewolfMixin6, TestCase):
    def test_speech_then_vote_out(self):
        """天亮 -> 发言（随机顺序）-> 投票 -> 票最多的人出局。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)

        # 天亮宣布 -> 谁点一下都能往下走
        res = self.advance(room_id, list(tokens.values())[0])
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_SPEECH)

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertEqual(len(state['speechOrder']), len(self.alive_ids(room_id)))

        self.finish_speech(room_id, tokens)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_VOTE)

        # 所有人投同一个人（不是自己）
        alive = self.alive_ids(room_id)
        target = alive[0]
        self.vote_all(room_id, tokens, lambda uid: target if uid != target else alive[1])

        game = self.game_of(room_id)
        self.assertIn(game.phase, (WerewolfGame.PHASE_RESULT, WerewolfGame.PHASE_OVER))
        self.assertEqual(game.round_result['outUserId'], target)
        self.assertFalse([p for p in game.players.all() if p.user_id == target][0].alive)

    def test_lynched_role_kept_secret_until_over(self):
        """被放逐的是谁公开，但**他的身份不公开** —— 身份只在结束后全公开。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        alive = self.alive_ids(room_id)
        target = alive[0]
        self.vote_all(room_id, tokens, lambda uid: target if uid != target else alive[1])

        game = self.game_of(room_id)
        self.assertEqual(game.round_result['outUserId'], target)
        # 库里照旧记着身份（结束回看用）
        self.assertIn('outRoleId', game.round_result)

        if game.phase == WerewolfGame.PHASE_OVER:
            return    # 这局已经结束，身份本来就该公开

        for user_id in tokens:
            state = self.state_of(room_id, user_id, tokens)
            self.assertEqual(state['roundResult']['outUserId'], target)
            self.assertNotIn(
                'outRoleId', state['roundResult'],
                '白天出局者的身份不能下发（连出局者自己也不用在这里公布）'
            )

    def test_all_abstain_no_one_out(self):
        """全员弃票 -> 本轮没人出局。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        self.vote_all(room_id, tokens, lambda uid: None)

        game = self.game_of(room_id)
        self.assertTrue(game.round_result['tie'])
        self.assertIsNone(game.round_result['outUserId'])

    def test_tie_vote_goes_pk(self):
        """平票 -> 进入 PK 发言。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        alive = self.alive_ids(room_id)
        a, b = alive[0], alive[1]
        third = alive[2]

        # 前两人各投 a / b 形成 1:1，其余人弃票 —— 但弃票不算票，会变成 1:1 平票
        def target_of(uid):
            if uid == a:
                return b
            if uid == b:
                return a
            return None

        self.vote_all(room_id, tokens, target_of)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, WerewolfGame.PHASE_PK_SPEECH)
        self.assertEqual(sorted(game.pk_user_ids), sorted([a, b]))
        self.assertNotIn(third, game.pk_user_ids)

    def test_pk_tie_means_nobody_out(self):
        """PK 投票再次平票 -> 无人出局，回到结算并可以进入下一夜。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        alive = self.alive_ids(room_id)
        a, b = alive[0], alive[1]

        def first_round(uid):
            if uid == a:
                return b
            if uid == b:
                return a
            return None

        self.vote_all(room_id, tokens, first_round)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_PK_SPEECH)

        self.finish_speech(room_id, tokens)
        self.assertEqual(self.game_of(room_id).phase, WerewolfGame.PHASE_PK_VOTE)

        # PK 的两个人不能投；其余人分成 1:1
        voters = [u for u in self.alive_ids(room_id) if u not in (a, b)]
        self.assertGreaterEqual(len(voters), 2)
        self.vote(room_id, tokens[voters[0]], a)
        self.vote(room_id, tokens[voters[1]], b)
        for uid in voters[2:]:
            self.vote(room_id, tokens[uid], None)

        game = self.game_of(room_id)
        self.assertIn(game.phase, (WerewolfGame.PHASE_RESULT, WerewolfGame.PHASE_OVER))
        self.assertTrue(game.round_result['tie'])
        self.assertIsNone(game.round_result['outUserId'])

    def test_pk_voter_cannot_be_pk_player(self):
        """PK 玩家本人不能参与 PK 投票。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)
        self.advance(room_id, list(tokens.values())[0])
        self.finish_speech(room_id, tokens)

        alive = self.alive_ids(room_id)
        a, b = alive[0], alive[1]

        def first_round(uid):
            return b if uid == a else (a if uid == b else None)

        self.vote_all(room_id, tokens, first_round)
        self.finish_speech(room_id, tokens)

        res = self.vote(room_id, tokens[a], b)
        self.assertEqual(res.status_code, 400)
        self.assertIn('PK', res.json()['detail'])


class WerewolfWinTests(WerewolfMixin6, TestCase):
    def test_good_wins_when_all_wolves_out(self):
        """把狼全部投出去 -> 好人获胜。"""
        room_id, tokens = self.seat_six()
        self.two_nights_to_day(room_id, tokens)

        # 直接判狼出局，验证胜负判定
        game = self.game_of(room_id)
        for p in game.players.all():
            if p.role_id == 'wolf':
                p.alive = False
                p.save(update_fields=['alive'])

        from party import werewolf as ww

        ww.check_winner(game)
        game.refresh_from_db()

        self.assertEqual(game.winner, 'good')
        self.assertEqual(game.phase, WerewolfGame.PHASE_OVER)
        self.assertIn('狼人', game.win_reason)

    def test_wolf_wins_by_killing_all_villagers(self):
        """平民全出局 -> 狼人获胜（屠民）。"""
        room_id, tokens = self.seat_six()
        game = self.game_of(room_id)

        for p in game.players.all():
            if p.role_id == 'villager':
                p.alive = False
                p.save(update_fields=['alive'])

        from party import werewolf as ww

        ww.check_winner(game)
        game.refresh_from_db()

        self.assertEqual(game.winner, 'wolf')
        self.assertIn('平民', game.win_reason)

    def test_winner_visible_to_everyone(self):
        """结束之后所有人的身份都会公开。"""
        room_id, tokens = self.seat_six()
        game = self.game_of(room_id)
        game.players.update(alive=False)  # 全灭（不会触发胜负，只是先造个状态）
        game.phase = WerewolfGame.PHASE_OVER
        game.winner = 'good'
        game.win_reason = '狼人全部出局，好人获胜'
        game.save()

        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertEqual(state['winner'], 'good')
        for row in state['players']:
            self.assertTrue(row.get('roleId'), '游戏结束后应该公开所有身份')
