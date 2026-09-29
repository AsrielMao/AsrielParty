/**
 * 业务接口层：小程序里所有跟「服务端」打交道的调用都从这里出。
 *
 * 两套实现：
 *   - mock 模式（config.useMock = true）：走 ../mock/handlers.js，本地 storage 实现
 *   - 远程模式：走 wx.request 打你自己的 Django 后端，接口协议见 docs/backend-api.md
 *
 * 无论哪种模式，返回给页面的房间对象结构都一样：
 *   { id, code, title, gameId, game, maxPlayers, ownerId, isOwner, status,
 *     memberCount, members: [ { userId, nickname, avatar, ready, seat, online, isSelf, isOwner } ] }
 */

const CONFIG = require('../config/index')
const req = require('./request')
const store = require('./store')
const games = require('./games')
const mock = require('../mock/handlers')

/** 远程模式的接口表（Django 侧按这个路径实现，完整协议见 docs/backend-api.md） */
const ROUTES = {
  login: { url: '/api/auth/login', method: 'POST', auth: false },
  getMe: { url: '/api/user/me', method: 'GET' },
  updateMe: { url: '/api/user/me', method: 'PATCH' },
  listGames: { url: '/api/games', method: 'GET', auth: false },
  listMyRooms: { url: '/api/rooms/mine', method: 'GET' },
  createRoom: { url: '/api/rooms/create', method: 'POST' },
  getRoom: { url: '/api/rooms/detail', method: 'GET' },
  joinRoom: { url: '/api/rooms/join', method: 'POST' },
  leaveRoom: { url: '/api/rooms/leave', method: 'POST' },
  updateRoom: { url: '/api/rooms/update', method: 'PATCH' },
  setReady: { url: '/api/rooms/ready', method: 'POST' },
  kickMember: { url: '/api/rooms/kick', method: 'POST' },
  startGame: { url: '/api/rooms/start', method: 'POST' },
  roomBots: { url: '/api/rooms/bots', method: 'POST' },
  voiceSignature: { url: '/api/rooms/voice-signature', method: 'POST' },
  setupUndercover: { url: '/api/rooms/undercover-setup', method: 'POST' },
  castVote: { url: '/api/rooms/vote', method: 'POST' },
  nextRound: { url: '/api/rooms/next-round', method: 'POST' },
  werewolfAct: { url: '/api/rooms/werewolf/act', method: 'POST' },
  werewolfVote: { url: '/api/rooms/werewolf/vote', method: 'POST' },
  werewolfAdvance: { url: '/api/rooms/werewolf/advance', method: 'POST' },
  drawPick: { url: '/api/rooms/draw/pick', method: 'POST' },
  drawStrokes: { url: '/api/rooms/draw/strokes', method: 'POST' },
  drawClear: { url: '/api/rooms/draw/clear', method: 'POST' },
  drawGuess: { url: '/api/rooms/draw/guess', method: 'POST' },
  drawAdvance: { url: '/api/rooms/draw/advance', method: 'POST' },
  drawPoll: { url: '/api/rooms/draw/poll', method: 'GET' }
}

/** 统一的调用入口：按模式分流 */
function call(name, payload) {
  if (CONFIG.useMock) {
    const handler = mock[name]
    if (!handler) return Promise.reject(new Error('mock 未实现接口：' + name))
    return handler(payload)
  }
  const route = ROUTES[name]
  if (!route) return Promise.reject(new Error('未定义接口：' + name))
  return req.request({
    url: route.url,
    method: route.method,
    data: payload,
    auth: route.auth !== false
  })
}

/* ============================ 派生字段 ============================ */

function currentUserId() {
  const user = store.get().user
  return user ? user.id : ''
}

function decorateMember(m, room, meId) {
  const nickname = m.nickname || '微信用户'
  return {
    userId: m.userId,
    /** 后端应返回成员 openid，语音状态用它匹配「谁在说话」；mock 下为空 */
    openid: m.openid || '',
    nickname: nickname,
    avatar: m.avatar || '',
    ready: !!m.ready,
    seat: typeof m.seat === 'number' ? m.seat : 0,
    online: m.online !== false,
    /** 人机（后端自动驱动的那种） */
    isBot: !!m.isBot,
    isSelf: m.userId === meId,
    isOwner: m.userId === room.ownerId,
    /** 没有头像时显示昵称首字 */
    initial: nickname.slice(0, 1)
  }
}

/**
 * 给房间补上前端需要的派生字段。
 * 页面永远不用自己算 isSelf / isOwner，也不用关心是 mock 还是远程。
 */
function decorateRoom(room) {
  if (!room) return null
  const meId = currentUserId()

  const members = (room.members || [])
    .map(function (m) {
      return decorateMember(m, room, meId)
    })
    .sort(function (a, b) {
      return a.seat - b.seat
    })

  const game = games.getGame(room.gameId)

  return {
    id: room.id,
    code: room.code,
    title: room.title,
    gameId: room.gameId,
    game: game ? games.decorate(game) : null,
    gameName: game ? game.name : '未选择游戏',
    /** 版型（狼人杀用），房间页据此决定布局与「点头像能打的标签」 */
    variantId: room.variantId || '',
    maxPlayers: room.maxPlayers,
    ownerId: room.ownerId,
    status: room.status || 'waiting',
    createdAt: room.createdAt,
    updatedAt: room.updatedAt,
    members: members,
    memberCount: members.length,
    /** 房间里的人机数量（准备阶段房主可以加减） */
    botCount: members.filter(function (m) {
      return m.isBot
    }).length,
    isOwner: room.ownerId === meId,
    /** 我自己的成员记录（可能为 null，比如还没加入） */
    me: members.filter(function (m) {
      return m.isSelf
    })[0] || null,
    isEmptySeat: members.length < room.maxPlayers,
    /** 空位数量 */
    freeSeats: Math.max(0, room.maxPlayers - members.length),

    /** 谁是卧底：开局前房主设定的卧底人数 */
    undercoverCount: typeof room.undercoverCount === 'number' ? room.undercoverCount : 1,
    /**
     * 对局状态。mock / 后端都已经按「当前用户」过滤过了：
     * 这里只会看到自己的 myWord 与 myRole，别人的词和身份不在里面。
     */
    gameState: room.gameState || null
  }
}

function decorateUser(user) {
  if (!user) return null
  const nickname = user.nickname || '微信用户'
  return {
    id: user.id,
    openid: user.openid,
    nickname: nickname,
    avatar: user.avatar || '',
    initial: nickname.slice(0, 1),
    createdAt: user.createdAt
  }
}

/* ============================ 用户 / 登录 ============================ */

/**
 * 微信登录：小程序侧只拿到 code，交给后端换 openid。
 * 如果用户已经填过头像昵称，一并带上去。
 */
function login(payload) {
  return call('login', payload || {}).then(function (res) {
    if (res && res.token) req.setToken(res.token)
    const user = decorateUser(res && res.user)
    if (user) store.patch({ user: user, logged: true })
    return user
  })
}

function getMe() {
  return call('getMe').then(function (res) {
    const user = decorateUser(res && res.user ? res.user : res)
    if (user) store.patch({ user: user, logged: true })
    return user
  })
}

function updateMe(payload) {
  return call('updateMe', payload).then(function (res) {
    const user = decorateUser(res && res.user ? res.user : res)
    if (user) store.patch({ user: user, logged: true })
    return user
  })
}

function logout() {
  req.setToken('')
  store.patch({ user: null, logged: false })
}

/* ============================ 游戏 ============================ */

function listGames() {
  return call('listGames').then(function (list) {
    if (list && list.length) return list
    // 后端没返回时用本地静态数据兜底，保证页面永远有内容
    return games.GAMES
  })
}

/* ============================ 房间 ============================ */

function createRoom(payload) {
  return call('createRoom', payload).then(decorateRoom)
}

function getRoom(payload) {
  return call('getRoom', payload).then(decorateRoom)
}

function joinRoom(payload) {
  return call('joinRoom', payload).then(decorateRoom)
}

function leaveRoom(payload) {
  return call('leaveRoom', payload)
}

/** 切换游戏类型（房主） */
function updateRoom(payload) {
  return call('updateRoom', payload).then(decorateRoom)
}

function setReady(payload) {
  return call('setReady', payload).then(decorateRoom)
}

function kickMember(payload) {
  return call('kickMember', payload).then(decorateRoom)
}

function startGame(payload) {
  return call('startGame', payload).then(decorateRoom)
}

function listMyRooms() {
  return call('listMyRooms').then(function (list) {
    return (list || []).map(decorateRoom)
  })
}

/**
 * 房主设置房间里的人机数量（一个人 + 一台手机也能玩需要好几个人的游戏）。
 * 后端会把人数机调成 count 个；游戏进行中不允许改。
 */
function setBots(payload) {
  return call('roomBots', payload).then(decorateRoom)
}

/* ============================ 语音 ============================ */

/** 取实时语音签名（签名必须由后端用 appsecret 计算，不能放小程序里） */
function voiceSignature(payload) {
  return call('voiceSignature', payload)
}

/* ============================ 上传 ============================ */

/**
 * 上传头像。
 *
 * chooseAvatar 拿到的是本地临时路径，正式环境必须传到后端换成网络地址，
 * 否则临时文件一过期头像就裂了。
 * mock 模式没有服务器，直接返回本地路径（重启小程序后可能失效，属预期内，
 * seat 组件会自动回退成昵称首字）。
 *
 * 对应后端接口（multipart/form-data）：POST /api/user/avatar，字段名 avatar，返回 { url }
 */
function uploadAvatar(filePath) {
  if (!filePath) return Promise.resolve('')
  if (CONFIG.useMock) return Promise.resolve(filePath)

  return new Promise(function (resolve, reject) {
    wx.uploadFile({
      url: CONFIG.baseUrl + '/api/user/avatar',
      filePath: filePath,
      name: 'avatar',
      header: {
        Authorization: 'Bearer ' + req.getToken()
      },
      success: function (res) {
        let body = res.data
        try {
          body = JSON.parse(body)
        } catch (e) {
          // 后端没返回 JSON，下面按错误处理
        }
        if (res.statusCode >= 200 && res.statusCode < 300 && body && body.url) {
          resolve(body.url)
          return
        }
        reject(new Error((body && (body.detail || body.message)) || '头像上传失败'))
      },
      fail: function () {
        reject(new Error('头像上传失败，请检查网络'))
      }
    })
  })
}

/* ============================ 房间状态 ============================ */

/**
 * 结束本局，回到准备状态（房主）。
 * 复用 updateRoom 传 status='waiting'，后端负责校验只有房主能这么改。
 */
function endGame(payload) {
  const p = payload || {}
  return call('updateRoom', {
    roomId: p.roomId,
    code: p.code,
    status: 'waiting'
  }).then(decorateRoom)
}

/* ============================ 谁是卧底 ============================ */

/** 开局前设定卧底人数（房主），上限是「小于房间人数的一半」 */
function setupUndercover(payload) {
  return call('setupUndercover', payload).then(decorateRoom)
}

/** 投票给某人；所有人都投完后服务端会自动结算本轮 */
function castVote(payload) {
  return call('castVote', payload).then(decorateRoom)
}

/** 房主开下一轮 */
function nextRound(payload) {
  return call('nextRound', payload).then(decorateRoom)
}

/* ============================ 狼人杀 ============================ */

/**
 * 夜晚行动（封锁 / 摄梦 / 刀人 / 守护 / 查验 / 验尸 / 猎人开枪）。
 *
 * 具体做什么由「现在轮到哪个角色」决定，所以只有一个入口：
 *   targetUserId  选的人；不传表示「这步不用选人」或「跳过」
 *   skill         女巫 'save' 解药 / 'poison' 毒药；摄梦人 'slay' 让梦语者出局
 *
 * 规则判定全在后端，这里的校验只是为了让用户少点错。
 */
function werewolfAct(payload) {
  return call('werewolfAct', payload).then(decorateRoom)
}

/** 放逐投票 / PK 投票；targetUserId 为空即弃票 */
function werewolfVote(payload) {
  return call('werewolfVote', payload).then(decorateRoom)
}

/** 推进阶段（天亮宣布 / 发言完 / 结算进入下一夜）。不需要房主 */
function werewolfAdvance(payload) {
  return call('werewolfAdvance', payload).then(decorateRoom)
}

/* ============================ 你画我猜 ============================ */

/** 画手从候选词里挑一个（wordIndex 是候选词下标） */
function drawPick(payload) {
  return call('drawPick', payload).then(decorateRoom)
}

/**
 * 画手提交一批笔画。
 * points 是**归一化**坐标 [[x, y], ...]（0~1），由客户端按画布尺寸换算。
 * 返回很轻（只有序号），因为画手画的时候会频繁调它。
 */
function drawStrokes(payload) {
  return call('drawStrokes', payload)
}

/** 画手擦掉重画（服务端会发一条「清屏」信号给所有人） */
function drawClear(payload) {
  return call('drawClear', payload)
}

/** 提交一个猜测 */
function drawGuess(payload) {
  return call('drawGuess', payload).then(decorateRoom)
}

/** 本轮看完，进入下一轮 */
function drawAdvance(payload) {
  return call('drawAdvance', payload).then(decorateRoom)
}

/**
 * 画板的快速轮询（大约每 0.7 秒一次）。
 *
 * 返回：`{ strokes, seq, round, phase, remainSeconds, solvedUserIds, guesses, ... }`
 * —— `strokes` 是 sinceSeq 之后新增的笔画；`round` 变了就说明换了一轮，
 * 客户端要把画布清空并把 sinceSeq 归零。
 */
function drawPoll(payload) {
  return call('drawPoll', payload)
}

module.exports = {
  ROUTES: ROUTES,
  uploadAvatar: uploadAvatar,
  endGame: endGame,
  setupUndercover: setupUndercover,
  castVote: castVote,
  nextRound: nextRound,
  werewolfAct: werewolfAct,
  werewolfVote: werewolfVote,
  werewolfAdvance: werewolfAdvance,
  drawPick: drawPick,
  drawStrokes: drawStrokes,
  drawClear: drawClear,
  drawGuess: drawGuess,
  drawAdvance: drawAdvance,
  drawPoll: drawPoll,
  login: login,
  logout: logout,
  getMe: getMe,
  updateMe: updateMe,
  listGames: listGames,
  createRoom: createRoom,
  getRoom: getRoom,
  joinRoom: joinRoom,
  leaveRoom: leaveRoom,
  updateRoom: updateRoom,
  setReady: setReady,
  kickMember: kickMember,
  startGame: startGame,
  listMyRooms: listMyRooms,
  setBots: setBots,
  voiceSignature: voiceSignature,
  decorateRoom: decorateRoom
}
