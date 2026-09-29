"""
party 的路由。路径与 AsrielParty/docs/backend-api.md 一一对应，
改这里就要同步改小程序 utils/api.js 的 ROUTES。
"""

from django.urls import path

from . import views

urlpatterns = [
    # 根路径：一个「服务在跑」的状态页（后端没有网页界面，这儿原本是 404）
    path('', views.index, name='index'),

    # 登录与用户
    path('api/auth/login', views.login, name='login'),
    path('api/user/me', views.user_me, name='user_me'),
    path('api/user/avatar', views.user_avatar, name='user_avatar'),

    # 游戏
    path('api/games', views.list_games, name='list_games'),

    # 房间
    path('api/rooms/mine', views.rooms_mine, name='rooms_mine'),
    path('api/rooms/create', views.room_create, name='room_create'),
    path('api/rooms/detail', views.room_detail, name='room_detail'),
    path('api/rooms/join', views.room_join, name='room_join'),
    path('api/rooms/leave', views.room_leave, name='room_leave'),
    path('api/rooms/update', views.room_update, name='room_update'),
    path('api/rooms/ready', views.room_ready, name='room_ready'),
    path('api/rooms/kick', views.room_kick, name='room_kick'),
    path('api/rooms/start', views.room_start, name='room_start'),
    path('api/rooms/bots', views.room_bots, name='room_bots'),

    # 实时语音
    path('api/rooms/voice-signature', views.voice_signature, name='voice_signature'),

    # 谁是卧底
    path('api/rooms/undercover-setup', views.undercover_setup, name='undercover_setup'),
    path('api/rooms/vote', views.undercover_vote, name='undercover_vote'),
    path('api/rooms/next-round', views.undercover_next_round, name='undercover_next_round'),

    # 狼人杀
    path('api/rooms/werewolf/act', views.werewolf_act, name='werewolf_act'),
    path('api/rooms/werewolf/vote', views.werewolf_vote, name='werewolf_vote'),
    path('api/rooms/werewolf/advance', views.werewolf_advance, name='werewolf_advance'),

    # 你画我猜
    path('api/rooms/draw/pick', views.draw_pick, name='draw_pick'),
    path('api/rooms/draw/strokes', views.draw_strokes, name='draw_strokes'),
    path('api/rooms/draw/guess', views.draw_guess, name='draw_guess'),
    path('api/rooms/draw/advance', views.draw_advance, name='draw_advance'),
    path('api/rooms/draw/clear', views.draw_clear, name='draw_clear'),
    path('api/rooms/draw/poll', views.draw_poll, name='draw_poll'),
]
