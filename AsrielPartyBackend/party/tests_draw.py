"""
「你画我猜」的集成测试。

    .venv/Scripts/python.exe manage.py test party

覆盖：开局排画手顺序 → 选词 → 提交笔画（增量同步）→ 猜词计分 →
本轮结算 → 下一轮 → 整局结束，以及倒计时自动推进（tick）、
「答案只有画手看得到」这条底线、有人中途退出时的收尾。
"""

import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from party import draw_guess as draw
from party.models import DrawGame

GAME_ID = 'draw-guess'


class DrawMixin:
    """建局 / 发请求 / 走流程的公共辅助。"""

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

    def login(self, code, nickname):
        res = self.post('/api/auth/login', {'code': code, 'nickname': nickname})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        return body['token'], body['user']

    # ---------- 建局 ----------

    def open_room(self, seats=3, prefix='draw', start=True):
        """开一局你画我猜，返回 (room_id, {userId: token})。"""
        owner_token, owner = self.login(prefix + 'owner', '房主')
        room = self.post(
            '/api/rooms/create',
            {'gameId': GAME_ID, 'title': '画室'},
            token=owner_token,
        ).json()

        tokens = {owner['id']: owner_token}
        for i in range(seats - 1):
            token, user = self.login('%sp%d' % (prefix, i), 'P%s' % i)
            self.post('/api/rooms/join', {'roomId': room['id']}, token=token)
            tokens[user['id']] = token

        if start:
            res = self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
            self.assertEqual(res.status_code, 200, res.content)

        return room['id'], tokens

    # ---------- 查询 ----------

    def game_of(self, room_id):
        return DrawGame.objects.get(room_id=room_id)

    def state_of(self, room_id, user_id, tokens=None):
        detail = self.get('/api/rooms/detail', token=(tokens or {})[user_id], roomId=room_id)
        self.assertEqual(detail.status_code, 200, detail.content)
        return detail.json()['gameState']

    def other_than(self, tokens, user_id):
        for uid in tokens:
            if uid != user_id:
                return uid
        raise AssertionError('房间里没有别人')

    # ---------- 动作 ----------

    def pick(self, room_id, tokens, index=0):
        """让当前画手选词，返回选中的词。"""
        game = self.game_of(room_id)
        options = list(game.word_options)
        res = self.post(
            '/api/rooms/draw/pick',
            {'roomId': room_id, 'wordIndex': index},
            token=tokens[game.drawer_user_id],
        )
        self.assertEqual(res.status_code, 200, res.content)
        return options[index]

    def stroke(self, room_id, tokens, points=None):
        game = self.game_of(room_id)
        return self.post(
            '/api/rooms/draw/strokes',
            {
                'roomId': room_id,
                'strokes': [{'points': points or [[0.1, 0.1], [0.5, 0.5]], 'color': '#ff0000', 'width': 8}],
            },
            token=tokens[game.drawer_user_id],
        )

    def guess(self, room_id, tokens, user_id, text):
        return self.post(
            '/api/rooms/draw/guess',
            {'roomId': room_id, 'text': text},
            token=tokens[user_id],
        )

    def poll(self, room_id, tokens, user_id, since_seq=0):
        return self.get(
            '/api/rooms/draw/poll',
            token=tokens[user_id],
            roomId=room_id,
            sinceSeq=since_seq,
        ).json()

    def advance(self, room_id, tokens, user_id=None):
        user_id = user_id or list(tokens.keys())[0]
        return self.post('/api/rooms/draw/advance', {'roomId': room_id}, token=tokens[user_id])

    # ---------- 时间 ----------

    def force_timeout(self, room_id):
        """把当前阶段的开始时间往前拨，让它「已经超时」。"""
        game = self.game_of(room_id)
        game.phase_started_at = timezone.now() - timedelta(seconds=(game.phase_duration or 0) + 5)
        game.save(update_fields=['phase_started_at'])
        return game


class DrawStartTests(DrawMixin, TestCase):
    def test_start_deals_and_waits_for_pick(self):
        """开局：按座位排好画手顺序，进入选词阶段，分数都从 0 开始。"""
        room_id, tokens = self.open_room(3)
        game = self.game_of(room_id)

        self.assertEqual(game.phase, DrawGame.PHASE_PICKING)
        self.assertEqual(game.round_no, 1)
        self.assertEqual(game.total_rounds, 3)
        self.assertEqual(len(game.word_options), 3)
        self.assertEqual(game.word, '')
        self.assertEqual(len({int(k) for k in game.scores}), 3)
        self.assertTrue(all(v == 0 for v in game.scores.values()))

        # 队列里还剩 2 个人等着当画手
        self.assertEqual(len(game.drawer_order), 2)
        self.assertNotIn(game.drawer_user_id, game.drawer_order)

    def test_room_needs_three_players(self):
        """人数不够时开不了局（房间层的通用检查）。"""
        room_id, tokens = self.open_room(2, start=False)
        owner = list(tokens.keys())[0]

        res = self.post('/api/rooms/start', {'roomId': room_id}, token=tokens[owner])
        self.assertEqual(res.status_code, 400)
        self.assertIn('3 人', res.json()['detail'])

    def test_start_game_guards_against_a_lonely_room(self):
        """就算绕过房间检查直接开局，也不能让一个人自己跟自己玩。"""
        from party import draw_guess as draw
        from party.auth import ApiError
        from party.models import Room, RoomMember

        room_id, tokens = self.open_room(2, start=False)
        solo = list(tokens.keys())[0]
        RoomMember.objects.filter(room_id=room_id).exclude(user_id=solo).delete()

        room = Room.objects.get(id=room_id)
        self.assertEqual(room.members.count(), 1)
        with self.assertRaises(ApiError):
            draw.start_game(room)


class DrawPickTests(DrawMixin, TestCase):
    def test_only_drawer_can_pick(self):
        room_id, tokens = self.open_room(3)
        game = self.game_of(room_id)
        other = self.other_than(tokens, game.drawer_user_id)

        res = self.post(
            '/api/rooms/draw/pick', {'roomId': room_id, 'wordIndex': 0}, token=tokens[other]
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('不是你画', res.json()['detail'])

    def test_pick_moves_to_drawing(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, DrawGame.PHASE_DRAWING)
        self.assertEqual(game.word, word)
        self.assertTrue(game.word_category, '词类要能查到（给猜的人当提示）')
        self.assertGreater(game.phase_duration, 0)

    def test_bad_index_rejected(self):
        room_id, tokens = self.open_room(3)
        game = self.game_of(room_id)

        res = self.post(
            '/api/rooms/draw/pick',
            {'roomId': room_id, 'wordIndex': 99},
            token=tokens[game.drawer_user_id],
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('候选词', res.json()['detail'])


class DrawStrokeTests(DrawMixin, TestCase):
    def test_drawer_can_post_and_others_can_pull_increment(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        game = self.game_of(room_id)

        res = self.stroke(room_id, tokens)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['strokeSeq'], 1)

        # 画手再画一笔
        self.stroke(room_id, tokens, points=[[0.2, 0.2], [0.8, 0.8], [0.9, 0.1]])
        self.assertEqual(self.game_of(room_id).stroke_seq, 2)

        other = self.other_than(tokens, game.drawer_user_id)
        data = self.poll(room_id, tokens, other, since_seq=0)
        self.assertEqual(len(data['strokes']), 2)
        self.assertEqual(data['strokes'][0]['seq'], 1)
        self.assertEqual(data['strokes'][0]['color'], '#ff0000')
        self.assertEqual(data['strokes'][0]['width'], 8)

        # 增量：已经拿过的就不再返回
        data = self.poll(room_id, tokens, other, since_seq=1)
        self.assertEqual([s['seq'] for s in data['strokes']], [2])

    def test_points_are_normalized_and_clamped(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)

        res = self.post(
            '/api/rooms/draw/strokes',
            {
                'roomId': room_id,
                'strokes': [{
                    'points': [[-1, 0.5], [2, 0.5], [-3, 9]],
                    'color': 'javascript:alert(1)',
                    'width': 999,
                }],
            },
            token=tokens[self.game_of(room_id).drawer_user_id],
        )
        self.assertEqual(res.status_code, 200, res.content)

        game = self.game_of(room_id)
        stroke = game.strokes.first()
        self.assertEqual(stroke.points, [[0.0, 0.5], [1.0, 0.5], [0.0, 1.0]], '坐标要被夹到 0~1')
        self.assertEqual(stroke.color, draw.DEFAULT_COLOR, '非法颜色回退成默认色')
        self.assertEqual(stroke.width, 24, '线宽有上限')

    def test_only_drawer_can_post_strokes(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        game = self.game_of(room_id)
        other = self.other_than(tokens, game.drawer_user_id)

        res = self.post(
            '/api/rooms/draw/strokes',
            {'roomId': room_id, 'strokes': [{'points': [[0.1, 0.1], [0.2, 0.2]]}]},
            token=tokens[other],
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('不是你画', res.json()['detail'])

    def test_single_point_stroke_is_ignored(self):
        """只有一个点的「笔画」画不出东西，直接丢掉，不占序号。"""
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)

        res = self.post(
            '/api/rooms/draw/strokes',
            {'roomId': room_id, 'strokes': [{'points': [[0.3, 0.3]]}]},
            token=tokens[self.game_of(room_id).drawer_user_id],
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.game_of(room_id).stroke_seq, 0)

    def test_clear_canvas_notifies_everyone(self):
        """擦了重画：别人要收到一条「空笔画」当清屏信号。"""
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id
        other = self.other_than(tokens, drawer)
        self.stroke(room_id, tokens)

        res = self.post('/api/rooms/draw/clear', {'roomId': room_id}, token=tokens[drawer])
        self.assertEqual(res.status_code, 200, res.content)

        game = self.game_of(room_id)
        self.assertEqual(game.strokes.count(), 1, '只留下一条清屏信号')
        self.assertEqual(game.strokes.first().points, [])

        # 别人从头拉一遍：先前的笔画没了，只拿到那条清屏信号
        data = self.poll(room_id, tokens, other, since_seq=0)
        self.assertEqual(len(data['strokes']), 1)
        self.assertEqual(data['strokes'][0]['points'], [])

    def test_only_drawer_can_clear(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id
        other = self.other_than(tokens, drawer)

        res = self.post('/api/rooms/draw/clear', {'roomId': room_id}, token=tokens[other])
        self.assertEqual(res.status_code, 400)
        self.assertIn('不是你画', res.json()['detail'])


class DrawGuessTests(DrawMixin, TestCase):
    def test_wrong_guess_is_public_but_scores_nothing(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        game = self.game_of(room_id)
        guesser = self.other_than(tokens, game.drawer_user_id)

        res = self.guess(room_id, tokens, guesser, '肯定不是这个')
        self.assertEqual(res.status_code, 200, res.content)

        game = self.game_of(room_id)
        self.assertEqual(game.guesses[-1]['text'], '肯定不是这个')
        self.assertFalse(game.guesses[-1]['correct'])
        self.assertEqual(draw.score_of(game, guesser), 0)

    def test_correct_guess_scores_and_hides_the_answer(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        game = self.game_of(room_id)
        drawer = game.drawer_user_id
        guesser = self.other_than(tokens, drawer)

        self.guess(room_id, tokens, guesser, word)

        game = self.game_of(room_id)
        self.assertIn(guesser, game.solved_user_ids)
        self.assertEqual(draw.score_of(game, guesser), draw.SCORE_GUESS)
        self.assertEqual(draw.score_of(game, drawer), draw.SCORE_DRAWER_PER_SOLVER)
        # 猜对的内容不写进流水，否则等于把答案告诉其他人
        self.assertEqual(game.guesses[-1]['text'], '')
        self.assertTrue(game.guesses[-1]['correct'])

    def test_guess_ignores_spaces_and_case(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        game = self.game_of(room_id)
        guesser = self.other_than(tokens, game.drawer_user_id)

        self.guess(room_id, tokens, guesser, '  %s  ' % word)

        self.assertIn(guesser, self.game_of(room_id).solved_user_ids)

    def test_drawer_cannot_guess(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        game = self.game_of(room_id)

        res = self.guess(room_id, tokens, game.drawer_user_id, '随便猜')
        self.assertEqual(res.status_code, 400)
        self.assertIn('画手不能猜', res.json()['detail'])

    def test_cannot_guess_twice(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        game = self.game_of(room_id)
        guesser = self.other_than(tokens, game.drawer_user_id)

        self.guess(room_id, tokens, guesser, word)
        res = self.guess(room_id, tokens, guesser, word)
        self.assertEqual(res.status_code, 400)
        self.assertIn('已经猜对', res.json()['detail'])

    def test_everyone_solved_ends_the_round(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        game = self.game_of(room_id)
        drawer = game.drawer_user_id

        for uid in tokens:
            if uid != drawer:
                self.guess(room_id, tokens, uid, word)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, DrawGame.PHASE_RESULT, '全猜对了就该直接结算')
        self.assertEqual(game.round_result['word'], word)
        self.assertFalse(game.round_result['timeout'])


class DrawRoundTests(DrawMixin, TestCase):
    def test_rounds_cycle_and_game_ends(self):
        """3 个人 = 3 轮；每轮画手不同；最后一轮之后整局结束。"""
        room_id, tokens = self.open_room(3)
        drawers = []

        for _ in range(3):
            game = self.game_of(room_id)
            self.assertEqual(game.phase, DrawGame.PHASE_PICKING)
            drawers.append(game.drawer_user_id)

            word = self.pick(room_id, tokens)
            drawer = game.drawer_user_id
            for uid in tokens:
                if uid != drawer:
                    self.guess(room_id, tokens, uid, word)

            self.assertEqual(self.game_of(room_id).phase, DrawGame.PHASE_RESULT)
            self.advance(room_id, tokens)

        self.assertEqual(len(set(drawers)), 3, '每个人只当一次画手')
        self.assertEqual(self.game_of(room_id).phase, DrawGame.PHASE_OVER)

    def test_strokes_are_cleared_between_rounds(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        self.stroke(room_id, tokens)

        drawer = self.game_of(room_id).drawer_user_id
        for uid in tokens:
            if uid != drawer:
                self.guess(room_id, tokens, uid, word)
        self.advance(room_id, tokens)

        game = self.game_of(room_id)
        self.assertEqual(game.stroke_seq, 0, '新的一轮要把画布清空')
        self.assertEqual(game.strokes.count(), 0)

    def test_scores_accumulate_and_winner_is_reported(self):
        """分数会累积；整局结束时给出最高分（并列也要都给出来）。"""
        room_id, tokens = self.open_room(3)

        # 把 3 轮都打完（每轮都让所有人猜出来）
        for _ in range(3):
            self.assertEqual(self.game_of(room_id).phase, DrawGame.PHASE_PICKING)
            word = self.pick(room_id, tokens)
            drawer = self.game_of(room_id).drawer_user_id
            for uid in tokens:
                if uid != drawer:
                    self.guess(room_id, tokens, uid, word)

            self.assertEqual(self.game_of(room_id).phase, DrawGame.PHASE_RESULT)
            self.advance(room_id, tokens)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, DrawGame.PHASE_OVER)

        # 3 人局每人当一次画手（+5×2 人猜对 = +10）、猜对两次（+10×2）→ 都是 30 分
        state = self.state_of(room_id, list(tokens.keys())[0], tokens)
        self.assertEqual([row['score'] for row in state['scores']], [30, 30, 30])
        self.assertEqual(len(state['winnerUserIds']), 3, '三个人并列最高分')

    # ---------- 倒计时自动推进 ----------

    def test_timeout_picks_a_word_for_the_drawer(self):
        """画手忘了选词：时间一到自动替他选，别把大家晾着。"""
        room_id, tokens = self.open_room(3)
        game = self.force_timeout(room_id)

        self.poll(room_id, tokens, list(tokens.keys())[0])

        game.refresh_from_db()
        self.assertEqual(game.phase, DrawGame.PHASE_DRAWING)
        self.assertTrue(game.word)

    def test_timeout_settles_the_round(self):
        """作画时间到：自动结算，并把答案公布出来。"""
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        self.force_timeout(room_id)

        data = self.poll(room_id, tokens, list(tokens.keys())[0])
        self.assertEqual(data['phase'], DrawGame.PHASE_RESULT)
        self.assertEqual(data['myWord'], word, '公布之后答案对所有人可见')

        game = self.game_of(room_id)
        self.assertTrue(game.round_result['timeout'])

    def test_timeout_moves_to_the_next_round(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id
        for uid in tokens:
            if uid != drawer:
                self.guess(room_id, tokens, uid, word)

        self.force_timeout(room_id)
        self.poll(room_id, tokens, list(tokens.keys())[0])

        game = self.game_of(room_id)
        self.assertEqual(game.round_no, 2)
        self.assertEqual(game.phase, DrawGame.PHASE_PICKING)


class DrawPrivacyTests(DrawMixin, TestCase):
    """你画我猜的底线：题目只有画手看得到（猜对的人和公布之后除外）。"""

    def test_word_is_only_visible_to_the_drawer(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id

        for uid in tokens:
            state = self.state_of(room_id, uid, tokens)
            if uid == drawer:
                self.assertEqual(state['myWord'], word, '画手要一直看得到答案')
                self.assertTrue(state['isDrawer'])
            else:
                self.assertEqual(state['myWord'], '', '别人不该看到答案')
                self.assertFalse(state['isDrawer'])
                # 但要给提示：字数和词类
                self.assertEqual(state['wordLength'], len(word))
                self.assertTrue(state['wordCategory'])

    def test_options_only_for_the_drawer_while_picking(self):
        room_id, tokens = self.open_room(3)
        drawer = self.game_of(room_id).drawer_user_id

        for uid in tokens:
            state = self.state_of(room_id, uid, tokens)
            if uid == drawer:
                self.assertEqual(len(state['wordOptions']), 3)
                self.assertTrue(state['canPick'])
            else:
                self.assertEqual(state['wordOptions'], [], '候选词只给画手')
                self.assertFalse(state['canPick'])

    def test_solver_can_see_the_word_after_guessing_right(self):
        room_id, tokens = self.open_room(3)
        word = self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id
        guesser = self.other_than(tokens, drawer)

        self.guess(room_id, tokens, guesser, word)

        state = self.state_of(room_id, guesser, tokens)
        self.assertEqual(state['myWord'], word, '猜对的人当然应该看得到答案')
        self.assertTrue(state['mySolved'])
        self.assertFalse(state['canGuess'], '猜过了就不能再猜')

    def test_can_guess_flags(self):
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        drawer = self.game_of(room_id).drawer_user_id
        other = self.other_than(tokens, drawer)

        self.assertTrue(self.state_of(room_id, other, tokens)['canGuess'])
        drawer_state = self.state_of(room_id, drawer, tokens)
        self.assertTrue(drawer_state['canDraw'])
        self.assertFalse(drawer_state['canGuess'])


class DrawLeaveTests(DrawMixin, TestCase):
    def test_drawer_leaving_ends_the_round(self):
        """画手退出房间：这一轮直接收掉，别让整桌人等他。"""
        room_id, tokens = self.open_room(3)
        self.pick(room_id, tokens)
        game = self.game_of(room_id)
        drawer = game.drawer_user_id

        res = self.post('/api/rooms/leave', {'roomId': room_id}, token=tokens[drawer])
        self.assertEqual(res.status_code, 200, res.content)

        game = self.game_of(room_id)
        self.assertEqual(game.phase, DrawGame.PHASE_RESULT, '画手走了就把这一轮收掉')
        self.assertNotIn(drawer, game.drawer_order or [])

    def test_waiting_player_leaving_shrinks_the_game(self):
        room_id, tokens = self.open_room(4)
        game = self.game_of(room_id)
        waiting = list(game.drawer_order)[0]

        self.post('/api/rooms/leave', {'roomId': room_id}, token=tokens[waiting])

        game = self.game_of(room_id)
        self.assertNotIn(waiting, game.drawer_order)
        self.assertEqual(game.total_rounds, 3, '少一个人就少一轮')
