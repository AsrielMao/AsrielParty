/**
 * mock 模式下对后端接口的本地实现。
 * 返回结构与文档里约定的 Django 接口完全一致，方便后端写好之后无缝切换。
 */

const db = require('./db')
const util = require('../utils/util')
const games = require('../utils/games')
const CONFIG = require('../config/index')
const undercover = require('./undercover')

/** 模拟网络延迟（毫秒） */
const DELAY = 160

/** 谁是卧底的游戏 id（要和 utils/games.js 里的一致） */
const GAME_UNDERCOVER = 'undercover'

/**
 * 创建房间时自动塞进来的模拟玩家数量。
 *
 * 目的是让 12 个座位的环形布局、准备状态、房主切换游戏这些交互在开发阶段就能看到效果。
 * 不需要的话把它改成 0 即可（模拟玩家不会影响真实的后端逻辑）。
 */
const MOCK_AUTO_BOTS = 3

const BOT_POOL = [
  { nickname: '小林', avatar: '' },
  { nickname: '阿哲', avatar: '' },
  { nickname: '丸子', avatar: '' },
  { nickname: '大熊', avatar: '' },
  { nickname: '柚子', avatar: '' },
  { nickname: '老陈', avatar: '' },
  { nickname: '七月', avatar: '' },
  { nickname: '小满', avatar: '' }
]

function delay(value) {
  return new Promise(function (resolve) {
    setTimeout(function () {
      resolve(value)
    }, DELAY)
  })
}

function fail(message) {
  return Promise.reject(new Error(message))
}

/**
 * 当前请求的用户 id，由 requireUser() 顺手记下来。
 * clone() 靠它过滤那些不能整包下发的字段（目前是卧底游戏里别人的词与身份）。
 */
let requestUserId = ''

/**
 * 深拷贝一份数据发给客户端。
 * 会顺手把 gameState 换成「只看得到自己」的公开版本 ——
 * 卧底游戏里别人的词和身份绝不能下发给小程序。
 */
function clone(obj) {
  const copy = JSON.parse(JSON.stringify(obj))
  maskPrivate(copy)
  return copy
}

/** 递归找出数据里的 gameState，换成按当前用户过滤过的公开版本 */
function maskPrivate(node) {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) {
    node.forEach(maskPrivate)
    return
  }
  if (node.gameState) {
    node.gameState = undercover.publicState(node.gameState, requestUserId)
  }
}

function currentUser() {
  const d = db.read()
  const user = d.users[d.currentUserId]
  if (!user) return null
  return user
}

function requireUser() {
  const user = currentUser()
  if (!user) throw new Error('尚未登录')
  requestUserId = user.id
  return user
}

function findRoom(d, payload) {
  if (payload && payload.roomId && d.rooms[payload.roomId]) {
    return d.rooms[payload.roomId]
  }
  if (payload && payload.code) {
    const code = String(payload.code).toUpperCase()
    const ids = Object.keys(d.rooms)
    for (let i = 0; i < ids.length; i++) {
      if (d.rooms[ids[i]].code === code) return d.rooms[ids[i]]
    }
  }
  return null
}

function requireRoom(d, payload) {
  const room = findRoom(d, payload)
  if (!room) throw new Error('房间不存在或已解散')
  return room
}

function requireOwner(room, user) {
  if (room.ownerId !== user.id) throw new Error('只有房主可以进行这个操作')
}

function memberOf(room, userId) {
  for (let i = 0; i < room.members.length; i++) {
    if (room.members[i].userId === userId) return room.members[i]
  }
  return null
}

/** 找最小空闲座位号 */
function nextFreeSeat(room) {
  const used = {}
  room.members.forEach(function (m) {
    used[m.seat] = true
  })
  for (let i = 0; i < room.maxPlayers; i++) {
    if (!used[i]) return i
  }
  return -1
}

function uniqueCode(d) {
  let code = ''
  let guard = 0
  do {
    code = util.randomRoomCode(CONFIG.roomCodeLength)
    guard++
  } while (
    guard < 100 &&
    Object.keys(d.rooms).some(function (k) {
      return d.rooms[k].code === code
    })
  )
  return code
}

function touch(room) {
  room.updatedAt = Date.now()
}

/** 房主退出时把房主身份顺移给下一个还在的成员 */
function transferOwner(room) {
  if (room.members.length === 0) return
  const hasOwner = room.members.some(function (m) {
    return m.userId === room.ownerId
  })
  if (!hasOwner) {
    room.ownerId = room.members[0].userId
  }
}

/* ============================ 用户 ============================ */

function login(payload) {
  const d = db.read()
  const p = payload || {}

  let user = d.users[d.currentUserId]
  if (!user) {
    const id = util.localId('u')
    user = {
      id: id,
      openid: 'mock_openid_' + id,
      nickname: '',
      avatar: '',
      createdAt: Date.now()
    }
    d.users[id] = user
    d.currentUserId = id
  }

  // 登录时如果带了头像昵称（用户刚在授权弹层里选好），一并保存
  if (p.nickname) user.nickname = String(p.nickname).slice(0, 20)
  if (p.avatar) user.avatar = p.avatar
  if (!user.nickname) user.nickname = '微信用户'

  d.users[user.id] = user
  db.write(d)

  return delay({
    token: 'mock_token_' + user.id,
    user: clone(user)
  })
}

function getMe() {
  try {
    const user = requireUser()
    return delay(clone(user))
  } catch (e) {
    return fail(e.message)
  }
}

function updateMe(payload) {
  try {
    const user = requireUser()
    const p = payload || {}
    const d = db.read()

    if (typeof p.nickname === 'string') {
      const nickname = p.nickname.trim()
      if (!nickname) return fail('昵称不能为空')
      if (nickname.length > 20) return fail('昵称不能超过 20 个字')
      user.nickname = nickname
    }
    if (typeof p.avatar === 'string') {
      user.avatar = p.avatar
    }

    d.users[user.id] = user
    db.write(d)

    // 同步更新我在各个房间里的展示信息
    const ids = Object.keys(d.rooms)
    let changed = false
    ids.forEach(function (rid) {
      const room = d.rooms[rid]
      const m = memberOf(room, user.id)
      if (m) {
        m.nickname = user.nickname
        m.avatar = user.avatar
        touch(room)
        changed = true
      }
    })
    if (changed) db.write(d)

    return delay(clone(user))
  } catch (e) {
    return fail(e.message)
  }
}

/* ============================ 游戏 ============================ */

function listGames() {
  return delay(games.GAMES.map(games.decorate))
}

/* ============================ 房间 ============================ */

function createRoom(payload) {
  try {
    const user = requireUser()
    const p = payload || {}
    const d = db.read()

    const game = games.getGame(p.gameId)
    if (!game) return fail('请选择一个游戏类型')

    // 座位数：有版型的游戏（狼人杀）由版型决定，其余一律用固定座位数
    const wantedVariant = p.variantId
    const variant = games.getVariant(game, wantedVariant)
    if (wantedVariant && !variant) return fail('版型不存在')

    const maxPlayers = variant ? variant.seats : CONFIG.defaultSeats

    if (maxPlayers < game.minPlayers) {
      return fail(game.name + ' 至少需要 ' + game.minPlayers + ' 人，座位数不能比这还少')
    }

    const roomId = util.localId('r')
    const now = Date.now()
    const room = {
      id: roomId,
      code: uniqueCode(d),
      title: (p.title || '').trim() || (user.nickname + ' 的房间'),
      gameId: game.id,
      /** 版型 id（狼人杀用；不传就用该游戏的默认版型） */
      variantId: variant ? variant.id : '',
      maxPlayers: maxPlayers,
      ownerId: user.id,
      status: 'waiting',
      /** 谁是卧底的卧底人数，开局前由房主设定，默认 1 */
      undercoverCount: 1,
      /** 对局状态，开局后由 mock/undercover.js 生成；只能经 clone 过滤后下发 */
      gameState: null,
      createdAt: now,
      updatedAt: now,
      members: [
        {
          userId: user.id,
          nickname: user.nickname,
          avatar: user.avatar,
          ready: false,
          seat: 0,
          joinedAt: now,
          online: true
        }
      ]
    }

    // 塞几个模拟玩家，方便看到座位布局效果（MOCK_AUTO_BOTS 设为 0 可关闭）
    for (let i = 0; i < MOCK_AUTO_BOTS && i < maxPlayers - 1; i++) {
      const bot = BOT_POOL[i % BOT_POOL.length]
      room.members.push({
        userId: 'bot_' + i,
        nickname: bot.nickname,
        avatar: bot.avatar,
        ready: true,
        seat: i + 1,
        joinedAt: now,
        online: true
      })
    }

    d.rooms[roomId] = room
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function getRoom(payload) {
  try {
    // 取一次当前用户：clone() 要靠它决定卧底游戏里哪份数据能下发
    requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    // mock 是「请求驱动」的：趁这次读取把到点的模拟玩家推一把。
    // 不这么做的话，一个人玩时机器人永远不会投票，结算就永远等不到。
    if (undercover.tickBots(room.gameState)) {
      touch(room)
      db.write(d)
    }

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function joinRoom(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    if (room.status === 'closed') return fail('房间已解散')

    const existing = memberOf(room, user.id)
    if (existing) {
      existing.online = true
      touch(room)
      db.write(d)
      return delay(clone(room))
    }

    if (room.members.length >= room.maxPlayers) return fail('房间已满')

    const seat = nextFreeSeat(room)
    if (seat < 0) return fail('房间已满')

    room.members.push({
      userId: user.id,
      nickname: user.nickname,
      avatar: user.avatar,
      ready: false,
      seat: seat,
      joinedAt: Date.now(),
      online: true
    })
    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function leaveRoom(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    room.members = room.members.filter(function (m) {
      return m.userId !== user.id
    })

    // 走了的人要从对局里摘掉，否则「等所有人投完」会永远等不到他
    if (room.gameState) undercover.removePlayer(room.gameState, user.id)

    if (room.members.length === 0) {
      delete d.rooms[room.id]
    } else {
      transferOwner(room)
      touch(room)
    }
    db.write(d)

    return delay({ ok: true })
  } catch (e) {
    return fail(e.message)
  }
}

function updateRoom(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)
    const p = payload || {}

    requireOwner(room, user)

    if (p.gameId !== undefined) {
      const game = games.getGame(p.gameId)
      if (!game) return fail('游戏类型不存在')

      // 换游戏时顺带换版型 —— 座位数由版型决定
      const variant = games.getVariant(game, p.variantId)
      if (p.variantId && !variant) return fail('版型不存在')

      const newSeats = variant ? variant.seats : room.maxPlayers
      if (newSeats < game.minPlayers) {
        return fail(game.name + ' 至少需要 ' + game.minPlayers + ' 人')
      }
      if (room.members.length > newSeats) {
        return fail(
          '房间里已经有 ' + room.members.length + ' 个人，这个版型只有 ' + newSeats + ' 个座位'
        )
      }

      room.gameId = game.id
      room.variantId = variant ? variant.id : ''
      room.maxPlayers = newSeats
      // 换了游戏，大家的准备状态清零
      room.members.forEach(function (m) {
        m.ready = false
      })
      // 上一局的对局状态作废
      room.gameState = null
    }

    if (typeof p.title === 'string' && p.title.trim()) {
      room.title = p.title.trim()
    }

    // 结束本局：回到准备状态，并把所有人的准备清掉
    if (p.status === 'waiting' && room.status === 'playing') {
      room.status = 'waiting'
      room.gameState = null
      room.members.forEach(function (m) {
        m.ready = false
      })
    }

    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function setReady(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    const m = memberOf(room, user.id)
    if (!m) return fail('你不在这个房间里')

    m.ready = !!(payload && payload.ready)
    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

/**
 * 加减人机是后端的能力 —— mock 模式本来就会自动塞几个模拟玩家
 * （见上面 startGame 之前的 MOCK_AUTO_BOTS），所以这里明确回一句。
 */
function roomBots() {
  return fail('人机由后端驱动，请把 config/index.js 的 env 改成 dev 并启动后端')
}

function kickMember(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)
    const p = payload || {}

    requireOwner(room, user)
    if (p.userId === user.id) return fail('不能把自己移出房间')

    const target = memberOf(room, p.userId)
    if (!target) return fail('该玩家已不在房间中')

    room.members = room.members.filter(function (m) {
      return m.userId !== p.userId
    })
    // 被踢的人也要从对局里摘掉，否则「等所有人投完」会永远卡住
    if (room.gameState) undercover.removePlayer(room.gameState, p.userId)

    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function startGame(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    requireOwner(room, user)

    const game = games.getGame(room.gameId)
    if (!game) return fail('请先选择游戏类型')
    if (room.members.length < game.minPlayers) {
      return fail(game.name + ' 至少需要 ' + game.minPlayers + ' 人，现在只有 ' + room.members.length + ' 人')
    }

    // 狼人杀和你画我猜的流程逻辑（夜晚 / 投票 / 画板同步）只写在后端 Django 里，
    // mock 这边不再重复实现一遍 —— 免得两份规则慢慢跑偏。
    if (game.id === 'werewolf') {
      return fail('狼人杀的流程在后端，请把 config/index.js 的 env 改成 dev 并启动后端')
    }
    if (game.id === 'draw-guess') {
      return fail('你画我猜的画板要在后端同步，请把 config/index.js 的 env 改成 dev 并启动后端')
    }

    room.status = 'playing'

    // 谁是卧底：开局发牌 —— 随机抽一组词，再随机决定谁拿卧底词
    if (game.id === GAME_UNDERCOVER) {
      room.gameState = undercover.createState(room.members, room.undercoverCount)
    } else {
      room.gameState = null
    }

    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

function listMyRooms() {
  try {
    const user = requireUser()
    const d = db.read()
    const mine = db.roomsSorted(d).filter(function (room) {
      return !!memberOf(room, user.id)
    })
    return delay(clone(mine))
  } catch (e) {
    return fail(e.message)
  }
}

/* ============================ 谁是卧底 ============================ */

/**
 * 开局前设定卧底人数（房主）。
 * 上限是「必须小于房间人数的一半」，见 mock/undercover.js 的 maxUndercover。
 */
function setupUndercover(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)
    const p = payload || {}

    requireOwner(room, user)
    if (room.status === 'playing') return fail('游戏已经开始了，中途不能改卧底人数')
    if (room.gameId !== GAME_UNDERCOVER) return fail('当前房间不是谁是卧底')

    const cap = undercover.maxUndercover(room.members.length)
    if (cap < 1) return fail('人太少啦，至少要 4 个人才能玩谁是卧底')

    room.undercoverCount = Math.max(1, Math.min(Number(p.undercoverCount) || 1, cap))
    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

/** 投票。所有人都投完后会自动结算本轮（判定逻辑在 mock/undercover.js） */
function castVote(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)
    const p = payload || {}

    if (!room.gameState) return fail('游戏还没开始')
    undercover.vote(room.gameState, user.id, p.targetUserId)

    // 顺手把到点的模拟玩家也推一把，省得等他下次轮询
    undercover.tickBots(room.gameState)

    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

/** 房主开下一轮 */
function nextRound(payload) {
  try {
    const user = requireUser()
    const d = db.read()
    const room = requireRoom(d, payload)

    requireOwner(room, user)
    if (!room.gameState) return fail('游戏还没开始')
    undercover.nextRound(room.gameState)

    touch(room)
    db.write(d)

    return delay(clone(room))
  } catch (e) {
    return fail(e.message)
  }
}

/* ============================ 语音 ============================ */

/**
 * mock 模式不产生真实签名（真实签名必须由后端用 appsecret 计算）。
 * utils/voice.js 在 mock 模式下会跳过 wx.joinVoIPChat，直接模拟一个「已连接」状态。
 */
function voiceSignature(payload) {
  try {
    const d = db.read()
    const room = requireRoom(d, payload)
    return delay({
      mock: true,
      groupId: 'ap_room_' + room.code,
      signature: 'mock_signature',
      nonceStr: 'mock_nonce',
      timeStamp: Math.floor(Date.now() / 1000)
    })
  } catch (e) {
    return fail(e.message)
  }
}

module.exports = {
  login: login,
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
  roomBots: roomBots,
  listMyRooms: listMyRooms,
  voiceSignature: voiceSignature,
  setupUndercover: setupUndercover,
  castVote: castVote,
  nextRound: nextRound
}
