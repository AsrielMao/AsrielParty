"""
人机的集成测试。

    .venv/Scripts/python.exe manage.py test party

覆盖：加/减人机、谁能改、人机真的能把三个游戏往前推（一个人 + 一台手机的场景）。

驱动人机的方式和线上一致：调 `bots.tick(room)`（线上是客户端轮询时顺手调的）。
测试里为了不等那 2.5 秒的节奏冷却，每次都先把 `bot_next_act_at` 清掉。
"""

import json

from django.test import Client, TestCase

from party import bots
from party import draw_guess as draw
from party import undercover as uc
from party import werewolf as ww
from party.models import (
    DrawGame,
    Room,
    RoomMember,
    UndercoverGame,
    UndercoverWordPair,
    WerewolfGame,
)


class BotMixin:
    """建房间 / 发请求 / 推人机的公共辅助。"""

    def setUp(self):
        self.client = Client()
        # 谁是卧底开局要词库，测试库里是空的 —— 自己塞两组进去
        if UndercoverWordPair.objects.count() == 0:
            UndercoverWordPair.objects.create(word_a='可乐', word_b='雪碧')
            UndercoverWordPair.objects.create(word_a='苹果', word_b='梨')

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

    def login(self, code, nickname):
        res = self.post('/api/auth/login', {'code': code, 'nickname': nickname})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        return body['token'], body['user']

    # ---------- 房间 ----------

    def create_room(self, game_id, variant_id='', prefix='bot'):
        """建一个房间，返回 (room_id, 房主token, 房主userId)。"""
        token, owner = self.login(prefix + 'owner', '房主')
        data = {'gameId': game_id, 'title': '测试局'}
        if variant_id:
            data['variantId'] = variant_id

        res = self.post('/api/rooms/create', data, token=token)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()['id'], token, owner['id']

    def set_bots(self, room_id, token, count):
        return self.post('/api/rooms/bots', {'roomId': room_id, 'count': count}, token=token)

    def start_game(self, room_id, token):
        res = self.post('/api/rooms/start', {'roomId': room_id}, token=token)
        self.assertEqual(res.status_code, 200, res.content)
        return res

    # ---------- 驱动机器人 ----------

    def drive(self, room_id, rounds=1):
        """
        推人机往前走。

        每次都把冷却清掉，免得测试里要真的等 2.5 秒；
        线上是客户端轮询时顺手调 `bots.tick()`。
        """
        for _ in range(rounds):
            RoomMember.objects.filter(room_id=room_id, user__is_bot=True).update(
                bot_next_act_at=None
            )
            bots.tick(Room.objects.get(id=room_id))

    def bot_ids(self, room_id):
        return set(
            RoomMember.objects.filter(room_id=room_id, user__is_bot=True)
            .values_list('user_id', flat=True)
        )


class BotRoomTests(BotMixin, TestCase):
    """加人机 / 减人机 / 谁有权改。"""

    def test_add_bots_and_they_take_seats(self):
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')

        res = self.set_bots(room_id, token, 5)
        self.assertEqual(res.status_code, 200, res.content)

        room = res.json()
        self.assertEqual(len(room['members']), 6, '房主 + 5 个人机')
        self.assertEqual(len([m for m in room['members'] if m['isBot']]), 5)

        seats = sorted(m['seat'] for m in room['members'])
        self.assertEqual(seats, list(range(6)), '座位不能重复')

    def test_bot_count_is_clamped_to_seats(self):
        """6 座版型最多 6 个人，多要的会被截断。"""
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')
        res = self.set_bots(room_id, token, 99)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(len(res.json()['members']), 6)

    def test_reduce_and_clear_bots(self):
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')
        self.set_bots(room_id, token, 5)
        self.assertEqual(Room.objects.get(id=room_id).members.count(), 6)

        self.set_bots(room_id, token, 2)
        self.assertEqual(Room.objects.get(id=room_id).members.count(), 3)
        self.assertEqual(len(self.bot_ids(room_id)), 2)

        self.set_bots(room_id, token, 0)
        self.assertEqual(Room.objects.get(id=room_id).members.count(), 1)
        self.assertEqual(self.bot_ids(room_id), set())

    def test_bots_make_a_small_game_startable(self):
        """一个人也能凑够开局人数。"""
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')

        res = self.post('/api/rooms/start', {'roomId': room_id}, token=token)
        self.assertEqual(res.status_code, 400, '一个人开不了 6 人局')

        self.set_bots(room_id, token, 5)
        self.start_game(room_id, token)
        self.assertEqual(WerewolfGame.objects.filter(room_id=room_id).count(), 1)

    def test_only_owner_can_set_bots(self):
        room_id, token, owner_id = self.create_room('undercover')
        other_token, _ = self.login('other', '别人')
        self.post('/api/rooms/join', {'roomId': room_id}, token=other_token)

        res = self.set_bots(room_id, other_token, 2)
        self.assertEqual(res.status_code, 400)
        self.assertIn('房主', res.json()['detail'])

    def test_cannot_change_bots_while_playing(self):
        room_id, token, owner_id = self.create_room('undercover')
        self.set_bots(room_id, token, 3)
        self.start_game(room_id, token)

        res = self.set_bots(room_id, token, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn('结束本局', res.json()['detail'])

    def test_removing_bots_does_not_break_a_running_game(self):
        """游戏进行中把人机踢掉（等价于「结束本局」以外的情况）不会把对局搞崩。"""
        room_id, token, owner_id = self.create_room('undercover')
        self.set_bots(room_id, token, 3)
        self.start_game(room_id, token)

        # 直接调底层：模拟人机退出房间时的收尾
        game = UndercoverGame.objects.get(room_id=room_id)
        for bot_id in self.bot_ids(room_id):
            uc.remove_player(game, bot_id)

        game.refresh_from_db()
        self.assertEqual(UndercoverGame.objects.filter(room_id=room_id).count(), 1)


class BotDrawTests(BotMixin, TestCase):
    """人机在「你画我猜」里会选词、会画、会猜。"""

    def test_drawer_bot_picks_a_word(self):
        """轮到人机当画手时，它会自己挑词进入作画阶段。"""
        room_id, token, owner_id = self.create_room('draw-guess')
        self.set_bots(room_id, token, 2)
        self.start_game(room_id, token)

        # 开局时按座位排：房主坐 0 号，所以第一个画手是房主；
        # 这里直接把画手换成一个人机，模拟「轮到人机画」的情况
        game = DrawGame.objects.get(room_id=room_id)
        bot_id = sorted(self.bot_ids(room_id))[0]
        game.drawer_user_id = bot_id
        game.save(update_fields=['drawer_user_id'])

        self.drive(room_id, rounds=2)

        game.refresh_from_db()
        self.assertEqual(game.phase, DrawGame.PHASE_DRAWING, '人机画手应该自己选好词了')
        self.assertTrue(game.word)

    def test_bot_drawer_paints_strokes(self):
        """人机画手会往画板上涂东西。"""
        room_id, token, owner_id = self.create_room('draw-guess')
        self.set_bots(room_id, token, 2)
        self.start_game(room_id, token)

        game = DrawGame.objects.get(room_id=room_id)
        game.drawer_user_id = sorted(self.bot_ids(room_id))[0]
        game.phase = DrawGame.PHASE_DRAWING
        game.word = '西瓜'
        game.save(update_fields=['drawer_user_id', 'phase', 'word'])

        self.drive(room_id, rounds=3)

        game.refresh_from_db()
        self.assertGreater(game.stroke_seq, 0, '人机应该画了几笔')

        stroke = game.strokes.first()
        self.assertGreaterEqual(len(stroke.points), 2)
        for x, y in stroke.points:
            self.assertTrue(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, '坐标要归一化')

    def test_bot_guessers_guess(self):
        """人机猜手会打字猜词（猜错也算，会进流水）。"""
        room_id, token, owner_id = self.create_room('draw-guess')
        self.set_bots(room_id, token, 2)
        self.start_game(room_id, token)

        # 房主是画手：先选个词，把阶段推进到 drawing
        game = DrawGame.objects.get(room_id=room_id)
        self.assertEqual(game.drawer_user_id, owner_id, '房主坐 0 号，第一轮由他画')
        res = self.post(
            '/api/rooms/draw/pick', {'roomId': room_id, 'wordIndex': 0}, token=token
        )
        self.assertEqual(res.status_code, 200, res.content)

        self.drive(room_id, rounds=20)

        game = DrawGame.objects.get(room_id=room_id)
        self.assertTrue(game.guesses, '人机应该猜过词了')
        # 猜的人得是人机，不是房主
        self.assertIn(game.guesses[0]['userId'], self.bot_ids(room_id))

    def test_bots_can_finish_a_round(self):
        """房主画 + 人机猜：驱动够多次之后这一轮能结束（不用干等到 90 秒）。"""
        room_id, token, owner_id = self.create_room('draw-guess')
        self.set_bots(room_id, token, 2)
        self.start_game(room_id, token)
        self.post('/api/rooms/draw/pick', {'roomId': room_id, 'wordIndex': 0}, token=token)

        self.drive(room_id, rounds=60)

        game = DrawGame.objects.get(room_id=room_id)
        self.assertIn(
            game.phase,
            (DrawGame.PHASE_RESULT, DrawGame.PHASE_OVER),
            '人机应该把这一轮猜完了',
        )
        self.assertEqual(len(game.solved_user_ids), 2, '两个人机都猜对了')


class BotWerewolfTests(BotMixin, TestCase):
    """人机在狼人杀里会行动、会投票。"""

    def _owner_takes_its_turn(self, room_id, token, owner_id):
        """测试里没人真的点手机：轮到房主时随便替他走一步。"""
        game = WerewolfGame.objects.get(room_id=room_id)
        step = ww.current_step(game)
        if not step or owner_id not in step['actorIds']:
            return False
        if owner_id in (step.get('acted') or []):
            return False        # 这一夜他已经表过态了

        for player in game.players.all():
            if not player.alive or player.user_id == owner_id:
                continue
            res = self.post(
                '/api/rooms/werewolf/act',
                {'roomId': room_id, 'targetUserId': player.user_id},
                token=token,
            )
            if res.status_code == 200:
                return True
        return False

    def test_bots_finish_the_night(self):
        """一个人 + 五个人机：第一夜能自动走完（人机自己行动，房主那步替他做）。"""
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')
        self.set_bots(room_id, token, 5)
        self.start_game(room_id, token)

        for _ in range(30):
            self.drive(room_id, rounds=1)
            game = WerewolfGame.objects.get(room_id=room_id)

            if game.phase == WerewolfGame.PHASE_NIGHT:
                self._owner_takes_its_turn(room_id, token, owner_id)
            else:
                break

        game = WerewolfGame.objects.get(room_id=room_id)
        self.assertNotEqual(game.day, 0, '夜晚应该结算过、天亮了')

    def test_bot_votes_in_the_day(self):
        """白天投票时人机会自己投。"""
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')
        self.set_bots(room_id, token, 5)
        self.start_game(room_id, token)

        # 先把第一夜走完
        for _ in range(30):
            self.drive(room_id, rounds=1)
            game = WerewolfGame.objects.get(room_id=room_id)
            if game.phase == WerewolfGame.PHASE_NIGHT:
                self._owner_takes_its_turn(room_id, token, owner_id)
            else:
                break

        # 天亮 -> 开始发言（人机会自己一个个说完）
        self.post('/api/rooms/werewolf/advance', {'roomId': room_id}, token=token)

        for _ in range(30):
            game = WerewolfGame.objects.get(room_id=room_id)
            if game.phase in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
                break
            self.drive(room_id, rounds=1)
            game.refresh_from_db()
            if game.phase in (
                WerewolfGame.PHASE_DAYBREAK,
                WerewolfGame.PHASE_RESULT,
            ):
                self.post('/api/rooms/werewolf/advance', {'roomId': room_id}, token=token)

        game = WerewolfGame.objects.get(room_id=room_id)
        if game.phase not in (WerewolfGame.PHASE_VOTE, WerewolfGame.PHASE_PK_VOTE):
            return      # 夜里就分出胜负了，这条不适用

        self.drive(room_id, rounds=8)

        game.refresh_from_db()
        voted = [p for p in game.players.all() if p.voted]
        bot_ids = self.bot_ids(room_id)
        self.assertTrue(
            any(p.user_id in bot_ids for p in voted),
            '白天投票时人机应该自己投过票',
        )


    def test_bots_finish_pk_vote(self):
        """
        PK 投票：参选的那两个人自己不投票 —— 人机不能卡在他们身上。

        之前这里会卡死：循环遇到一个「机器人 PK 玩家」，它永远投不了票（后端会拒），
        也永远不会变成「已投票」，于是每次都停在它身上，后面的机器人轮不到。
        """
        room_id, token, owner_id = self.create_room('werewolf', 'newbie-6')
        self.set_bots(room_id, token, 5)
        self.start_game(room_id, token)

        game = WerewolfGame.objects.get(room_id=room_id)
        bot_ids = sorted(self.bot_ids(room_id))
        self.assertGreaterEqual(len(bot_ids), 3)

        # 直接把局面推到「PK 投票」，并且让两个机器人成为 PK 玩家
        pk_ids = bot_ids[:2]
        for player in game.players.all():
            player.voted = False
            player.vote_target = None
            player.save(update_fields=['voted', 'vote_target'])

        game.phase = WerewolfGame.PHASE_PK_VOTE
        game.pk_user_ids = pk_ids
        game.save(update_fields=['phase', 'pk_user_ids'])

        self.drive(room_id, rounds=12)

        game.refresh_from_db()
        pk_set = set(pk_ids)
        bot_voters = [
            p for p in game.players.all()
            if p.alive and p.user_id not in pk_set and p.user_id in self.bot_ids(room_id)
        ]

        self.assertTrue(bot_voters, '应该有人机需要投票')
        self.assertTrue(
            all(p.voted for p in bot_voters),
            'PK 投票时，非 PK 的人机都该把票投出去（卡住的那个 bug 就是卡在这里）',
        )

        # PK 玩家自己这一轮不投票，别把他们算进「已投票」
        for pid in pk_ids:
            player = [p for p in game.players.all() if p.user_id == pid][0]
            self.assertFalse(player.voted, 'PK 玩家这一轮不投票')


class BotUndercoverTests(BotMixin, TestCase):
    """人机在谁是卧底里会投票。"""

    def test_bots_vote_and_round_settles(self):
        room_id, token, owner_id = self.create_room('undercover')
        self.set_bots(room_id, token, 3)
        self.start_game(room_id, token)

        game = UndercoverGame.objects.get(room_id=room_id)
        bot_ids = self.bot_ids(room_id)
        self.assertTrue(bot_ids)

        # 开始几轮驱动：人机会先把票投了
        self.drive(room_id, rounds=8)

        game.refresh_from_db()
        voted = [p for p in game.players.all() if p.vote_target_id]
        self.assertTrue(
            any(p.user_id in bot_ids for p in voted),
            '人机应该投过票了',
        )

        # 房主是唯一还没投的人 -> 他投完这一轮就该结算
        me = game.players.filter(user_id=owner_id).first()
        still_voting = game.phase in (
            UndercoverGame.PHASE_REVEAL, UndercoverGame.PHASE_VOTING
        )
        if still_voting:
            self.assertTrue(me.alive, '第一轮房主不该出局')
            candidates = [
                p.user_id for p in game.players.all()
                if p.alive and p.user_id != owner_id
            ]
            res = self.post(
                '/api/rooms/vote',
                {'roomId': room_id, 'targetUserId': candidates[0]},
                token=token,
            )
            self.assertEqual(res.status_code, 200, res.content)
            game.refresh_from_db()

        # 注意：第一轮就可能直接分出胜负（比如恰好把卧底投出去了），
        # 那时 phase 会是 OVER 而不是 RESULT —— 两种都是「结算过了」。
        self.assertIn(
            game.phase,
            (UndercoverGame.PHASE_RESULT, UndercoverGame.PHASE_OVER),
            '所有人都投完就该结算',
        )

    def test_bot_next_act_gate(self):
        """同一个人机不会被一次轮询连着驱动好几步（有节奏控制）。"""
        room_id, token, owner_id = self.create_room('undercover')
        self.set_bots(room_id, token, 3)
        self.start_game(room_id, token)

        member = RoomMember.objects.filter(room_id=room_id, user__is_bot=True).first()
        self.assertTrue(member.may_bot_act(), '第一次应该放行')
        self.assertFalse(member.may_bot_act(), '紧接着再来一次就该被挡下')

        member.refresh_from_db()
        self.assertIsNotNone(member.bot_next_act_at)

    def test_polling_drives_the_bots(self):
        """
        线上是靠**客户端轮询**驱动的：只要有人调 `room_detail`，
        人机就会被顺手推一把 —— 不需要任何后台进程。
        """
        room_id, token, owner_id = self.create_room('undercover')
        self.set_bots(room_id, token, 3)
        self.start_game(room_id, token)

        # 把节奏冷却清掉，免得测试真的等 2.5 秒
        RoomMember.objects.filter(room_id=room_id, user__is_bot=True).update(
            bot_next_act_at=None
        )

        res = self.get('/api/rooms/detail', token=token, roomId=room_id)
        self.assertEqual(res.status_code, 200, res.content)

        game = UndercoverGame.objects.get(room_id=room_id)
        voted = [p for p in game.players.all() if p.vote_target_id]
        self.assertTrue(voted, '轮询一次就该有机器人动起来了')
        self.assertTrue(
            any(p.user_id in self.bot_ids(room_id) for p in voted),
            '动手的应该是机器人，不是房主',
        )
