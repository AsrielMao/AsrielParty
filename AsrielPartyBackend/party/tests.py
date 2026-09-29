"""
后端接口的冒烟测试。

    .venv/Scripts/python.exe manage.py test

覆盖：登录 → 建房间 → 加入 → 开局发牌 → 投票结算 的完整链路，
以及「别人的词和身份绝不能被下发」这条安全约定。
"""

import json
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from party import urls as party_urls
from party.constants import UNDERCOVER_GAME_ID
from party.models import (
    DrawGame,
    Room,
    UndercoverGame,
    UndercoverPlayer,
    UndercoverWordPair,
    User,
    WerewolfGame,
)


class StatusPageTests(TestCase):
    """
    根路径上的那个状态页。

    后端只有 /api/... 接口，根路径原本是 404 —— 在浏览器里打开会以为是服务挂了。
    """

    def test_root_is_a_status_page_not_404(self):
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200, '根路径不该是 404')

        content = res.content.decode('utf-8')
        self.assertIn('后端服务在跑', content)
        # 页面上要能看到真实存在的接口
        self.assertIn('/api/rooms/start', content)
        self.assertIn('/api/rooms/draw/poll', content)

    def test_status_page_lists_every_route(self):
        """接口清单是自动从路由里读的，加了新接口不用改模板。"""
        res = self.client.get('/')
        content = res.content.decode('utf-8')

        for pattern in party_urls.urlpatterns:
            route = str(pattern.pattern)
            if not route.startswith('api/'):
                continue
            self.assertIn('/' + route, content, '状态页应该列出 %s' % route)

    def test_api_routes_still_work(self):
        """状态页别把接口路由挤掉。"""
        res = self.client.get('/api/games')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_status_page_reports_migrations(self):
        """
        状态页要能看出数据库是不是落后了 ——
        落后时接口会直接 500（比如登录），但报错里看不出是这个原因。
        """
        res = self.client.get('/')
        content = res.content.decode('utf-8')

        self.assertIn('数据库迁移', content)
        self.assertIn('已是最新', content, '测试库总是迁到最新的')


class ApiMixin:
    def setUp(self):
        self.client = Client()
        # 测试跑在独立的测试数据库里，词库得自己造一份
        if UndercoverWordPair.objects.count() == 0:
            UndercoverWordPair.objects.create(word_a='可乐', word_b='雪碧')
            UndercoverWordPair.objects.create(word_a='苹果', word_b='梨')

    # ---- 便捷请求 ----

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

    # ---- 业务动作 ----

    def login(self, code, nickname):
        res = self.post('/api/auth/login', {'code': code, 'nickname': nickname})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        return body['token'], body['user']

    def create_room(self, token, game_id=UNDERCOVER_GAME_ID, **extra):
        data = {'gameId': game_id}
        data.update(extra)
        res = self.post('/api/rooms/create', data, token=token)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def join_room(self, token, room_id):
        res = self.post('/api/rooms/join', {'roomId': room_id}, token=token)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def detail(self, token, room_id):
        res = self.get('/api/rooms/detail', token=token, roomId=room_id)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()


class AvatarUploadTests(ApiMixin, TestCase):
    """
    头像上传。

    这条链路之前踩过坑：**它需要登录态**，但小程序那边一开始写的是
    「先传头像再登录」，切到真的后端之后整个登录都卡死了。
    （见 AsrielParty/pages/index/index.js 里的注释。）
    """

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)

    def upload(self, filename='me.png', token=None, content=b'fake-image-bytes'):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        with override_settings(MEDIA_ROOT=self.media):
            return self.client.post(
                '/api/user/avatar',
                {'avatar': SimpleUploadedFile(filename, content)},
                **headers
            )

    def stored_names(self):
        folder = os.path.join(self.media, 'avatars')
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_requires_login(self):
        """没登录就传头像 -> 401。小程序那个「先传再登录」的 bug 正是撞在这上面。"""
        res = self.upload()
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.stored_names(), [], '没登录不该写进任何文件')

    def test_uploads_and_returns_a_url(self):
        token, _ = self.login('avatar-user', '小林')

        res = self.upload(token=token)
        self.assertEqual(res.status_code, 200, res.content)

        url = res.json()['url']
        self.assertIn('/media/avatars/', url)

        stored = self.stored_names()
        self.assertEqual(len(stored), 1, '文件要真的落盘')
        self.assertTrue(stored[0].endswith('.png'))

    def test_weird_extension_is_forced_to_png(self):
        """不认识的扩展名一律按 png 存 —— 别让上传的人决定服务器上写什么文件。"""
        token, _ = self.login('avatar-user2', 'P1')

        res = self.upload(filename='evil.txt', token=token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(self.stored_names()[0].endswith('.png'))

    def test_missing_file_is_rejected(self):
        token, _ = self.login('avatar-user3', 'P2')
        with override_settings(MEDIA_ROOT=self.media):
            res = self.client.post(
                '/api/user/avatar', {}, HTTP_AUTHORIZATION='Bearer ' + token
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn('头像文件', res.json()['detail'])

    def test_the_real_order_works(self):
        """
        完整走一遍小程序那边的顺序：**登录 → 传头像 → 把地址写进资料**。

        这三步的顺序不能换 —— 传头像在登录之前就会被 401 挡掉。
        """
        token, user = self.login('avatar-flow', '小林')

        res = self.upload(token=token)
        self.assertEqual(res.status_code, 200, res.content)
        url = res.json()['url']

        save = self.client.patch(
            '/api/user/me',
            data=json.dumps({'nickname': '小林', 'avatar': url}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer ' + token,
        )
        self.assertEqual(save.status_code, 200, save.content)
        self.assertEqual(save.json()['avatar'], url)

        me = self.get('/api/user/me', token=token).json()
        self.assertEqual(me['avatar'], url, '资料里要留下这个头像地址')


class RoomStartAtomicTests(ApiMixin, TestCase):
    """
    开局必须是原子的。

    踩过的坑：`room_start` 先写 `status='playing'`、再去建对局，两步没有事务保护 ——
    中途出错（比如库没迁移、词库为空）就会留下「房间是 playing、但一局都没有」的状态。
    前端看到 playing 就以为还在游戏中，一直去轮询一个不存在的对局，后端只能一直回 400。
    """

    def seat_four(self, prefix):
        token, _ = self.login(prefix + '-owner', '房主')
        room = self.create_room(token, UNDERCOVER_GAME_ID)
        for i in range(3):
            other, _ = self.login('%s-p%d' % (prefix, i), 'P%d' % i)
            self.join_room(other, room['id'])
        return room, token

    def test_failed_start_rolls_back_to_waiting(self):
        """开局失败 -> 房间不能停在 playing。"""
        room, token = self.seat_four('rollback')

        # 词库清空 -> 卧底开局会失败（这正是「中途出错」的那种情况）
        UndercoverWordPair.objects.all().delete()

        res = self.post('/api/rooms/start', {'roomId': room['id']}, token=token)
        self.assertEqual(res.status_code, 400, res.content)

        saved = Room.objects.get(id=room['id'])
        self.assertEqual(
            saved.status, Room.STATUS_WAITING,
            '开局失败后房间必须留在 waiting，否则前端会一直轮询一个不存在的对局',
        )
        self.assertEqual(UndercoverGame.objects.filter(room=saved).count(), 0)

    def test_successful_start_still_goes_playing(self):
        """正常开局还是要进 playing（别把事务包坏了）。"""
        room, token = self.seat_four('okstart')

        res = self.post('/api/rooms/start', {'roomId': room['id']}, token=token)
        self.assertEqual(res.status_code, 200, res.content)

        saved = Room.objects.get(id=room['id'])
        self.assertEqual(saved.status, Room.STATUS_PLAYING)
        self.assertEqual(UndercoverGame.objects.filter(room=saved).count(), 1)

    def test_no_room_is_stuck_in_playing_without_a_game(self):
        """兜底：任何时刻都不该存在「playing 但没有对局」的房间。"""
        room, token = self.seat_four('stuck')
        UndercoverWordPair.objects.all().delete()
        self.post('/api/rooms/start', {'roomId': room['id']}, token=token)

        for candidate in Room.objects.filter(status=Room.STATUS_PLAYING):
            has_game = (
                UndercoverGame.objects.filter(room=candidate).exists()
                or WerewolfGame.objects.filter(room=candidate).exists()
                or DrawGame.objects.filter(room=candidate).exists()
            )
            self.assertTrue(has_game, '房间 %s 卡在 playing 却没有对局' % candidate.id)


class LoginTests(ApiMixin, TestCase):
    def test_same_code_logs_into_same_user(self):
        """同一个 code 再次登录，应该还是同一个用户（openid 是唯一标识）。"""
        _, first = self.login('code-a', '小林')
        _, second = self.login('code-a', '小林改名了')

        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['nickname'], '小林改名了')
        self.assertEqual(User.objects.count(), 1)

    def test_different_code_creates_new_user(self):
        self.login('code-a', '小林')
        self.login('code-b', '阿哲')
        self.assertEqual(User.objects.count(), 2)

    def test_room_api_requires_login(self):
        res = self.post('/api/rooms/create', {'gameId': UNDERCOVER_GAME_ID})
        self.assertEqual(res.status_code, 401)
        self.assertIn('detail', res.json())


class UndercoverFlowTests(ApiMixin, TestCase):
    def seat_four(self):
        """建一个 4 人房间，返回 (房主 token, 房间, [四个 token])。"""
        owner_token, _ = self.login('owner', '房主')
        room = self.create_room(owner_token)
        self.assertEqual(room['maxPlayers'], 10)
        self.assertEqual(room['undercoverCount'], 1)

        tokens = [owner_token]
        for i in range(3):
            token, _ = self.login('p%d' % i, '玩家%d' % i)
            self.join_room(token, room['id'])
            tokens.append(token)

        room = self.detail(owner_token, room['id'])
        self.assertEqual(len(room['members']), 4)
        return owner_token, room, tokens

    def test_start_game_deals_words(self):
        """开局后每个人拿到自己的词，且只能看到自己的。"""
        owner_token, room, tokens = self.seat_four()

        res = self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
        self.assertEqual(res.status_code, 200, res.content)

        seen_words = set()
        for token in tokens:
            state = self.detail(token, room['id'])['gameState']
            self.assertIsNotNone(state)
            self.assertIn(state['phase'], ('reveal', 'voting'))
            self.assertTrue(state['myWord'], '每个人都应该拿到自己的词')

            # 别人的条目里绝不能出现 word / role
            for player in state['players']:
                self.assertNotIn('word', player)
                self.assertNotIn('role', player)

            seen_words.add(state['myWord'])

        # 一局里只可能有两种词
        self.assertLessEqual(len(seen_words), 2)
        self.assertGreaterEqual(len(seen_words), 1)

        # 卧底人数与词的分组对得上
        game = UndercoverGame.objects.get(room_id=room['id'])
        undercover_count = UndercoverPlayer.objects.filter(
            game=game, role=UndercoverPlayer.ROLE_UNDERCOVER
        ).count()
        self.assertEqual(undercover_count, 1)
        self.assertNotEqual(game.civilian_word, game.undercover_word)

    def test_vote_and_settle(self):
        """全员投票后自动结算，票最多的人出局。"""
        owner_token, room, tokens = self.seat_four()
        self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)

        room = self.detail(owner_token, room['id'])
        ids = [m['userId'] for m in room['members']]
        # 房主是 0 号座位，其余按座位排；这里统一按 userId 取
        target = ids[1]

        # 三个人投 target，target 自己投别人（保证票数唯一最多）
        for token, uid in zip(tokens, ids):
            vote_for = target if uid != target else ids[0]
            res = self.post(
                '/api/rooms/vote',
                {'roomId': room['id'], 'targetUserId': vote_for},
                token=token,
            )
            self.assertEqual(res.status_code, 200, res.content)

        state = self.detail(owner_token, room['id'])['gameState']
        self.assertIn(state['phase'], ('result', 'over'))
        self.assertIsNotNone(state['roundResult'])
        self.assertFalse(state['roundResult']['tie'])
        self.assertEqual(state['roundResult']['outUserId'], target)

    def test_cannot_vote_self(self):
        owner_token, room, tokens = self.seat_four()
        self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)

        me = self.detail(owner_token, room['id'])
        my_id = [m['userId'] for m in me['members'] if m['nickname'] == '房主'][0]
        res = self.post(
            '/api/rooms/vote',
            {'roomId': room['id'], 'targetUserId': my_id},
            token=owner_token,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('自己', res.json()['detail'])

    def test_undercover_count_cap(self):
        """卧底人数上限：必须小于人数的一半。4 人 → 1，5 人 → 2。"""
        owner_token, room, _ = self.seat_four()
        res = self.post(
            '/api/rooms/undercover-setup',
            {'roomId': room['id'], 'undercoverCount': 5},
            token=owner_token,
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['undercoverCount'], 1)

        # 再加一个人，上限变成 2
        token, _ = self.login('p3', '玩家3')
        self.join_room(token, room['id'])
        res = self.post(
            '/api/rooms/undercover-setup',
            {'roomId': room['id'], 'undercoverCount': 5},
            token=owner_token,
        )
        self.assertEqual(res.json()['undercoverCount'], 2)

    def test_undercover_count_refused_when_playing(self):
        owner_token, room, _ = self.seat_four()
        self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
        res = self.post(
            '/api/rooms/undercover-setup',
            {'roomId': room['id'], 'undercoverCount': 1},
            token=owner_token,
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn('已经开始了', res.json()['detail'])

    def test_start_needs_min_players(self):
        """人数不够不能开局（谁是卧底要 4 人）。"""
        owner_token, _ = self.login('solo', '独狼')
        room = self.create_room(owner_token)
        res = self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
        self.assertEqual(res.status_code, 400)
        self.assertIn('至少需要', res.json()['detail'])

    def test_unknown_room_returns_404(self):
        token, _ = self.login('ghost', '幽灵')
        res = self.get('/api/rooms/detail', token=token, roomId=999999)
        self.assertEqual(res.status_code, 404)


class RoomRuleTests(ApiMixin, TestCase):
    def test_non_werewolf_room_is_fixed_ten_seats(self):
        """非狼人杀游戏不开放座位设置，一律 10 座。"""
        token, _ = self.login('a', '甲')
        room = self.create_room(token, game_id=UNDERCOVER_GAME_ID, maxPlayers=12)
        self.assertEqual(room['maxPlayers'], 10)

    def test_werewolf_seats_come_from_variant(self):
        """狼人杀的座位数由版型决定，不是房主自选。"""
        token, _ = self.login('b', '乙')
        room = self.create_room(token, game_id='werewolf', variantId='newbie-6')
        self.assertEqual(room['maxPlayers'], 6)
        self.assertEqual(room['variantId'], 'newbie-6')

        # 不存在的版型要报错
        res = self.post(
            '/api/rooms/create',
            {'gameId': 'werewolf', 'variantId': 'not-a-variant'},
            token=token,
        )
        self.assertEqual(res.status_code, 400)

    def test_owner_leaves_transfers_ownership(self):
        owner_token, _ = self.login('o', '房主')
        room = self.create_room(owner_token)

        other_token, other = self.login('m', '成员')
        self.join_room(other_token, room['id'])

        res = self.post('/api/rooms/leave', {'roomId': room['id']}, token=owner_token)
        self.assertEqual(res.status_code, 200)

        detail = self.detail(other_token, room['id'])
        self.assertEqual(detail['ownerId'], other['id'])

    def test_last_member_leaving_deletes_room(self):
        token, _ = self.login('alone', '一个人')
        room = self.create_room(token)
        self.post('/api/rooms/leave', {'roomId': room['id']}, token=token)
        self.assertEqual(Room.objects.filter(pk=room['id']).count(), 0)

    def test_switch_game_clears_previous_round(self):
        owner_token, room, _ = self.seat_four_quick()
        self.post('/api/rooms/start', {'roomId': room['id']}, token=owner_token)
        self.assertEqual(UndercoverGame.objects.filter(room_id=room['id']).count(), 1)

        res = self.client.patch(
            '/api/rooms/update',
            data=json.dumps({'roomId': room['id'], 'gameId': 'draw-guess'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer ' + owner_token,
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(UndercoverGame.objects.filter(room_id=room['id']).count(), 0)

    def seat_four_quick(self):
        owner_token, _ = self.login('o2', '房主2')
        room = self.create_room(owner_token)
        for i in range(3):
            token, _ = self.login('q%d' % i, 'Q%d' % i)
            self.join_room(token, room['id'])
        return owner_token, self.detail(owner_token, room['id']), None
