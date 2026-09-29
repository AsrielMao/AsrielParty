/**
 * 谁是卧底 · 游戏逻辑（mock 后端）
 *
 * 正常情况这些规则应该跑在 Django 里，mock 只是把同一套规则在本地实现一遍。
 * 里面全是纯函数，不依赖 wx，方便以后照着抄到后端。
 *
 * 关键点：state 里存着所有人的明文词与身份，**绝不能整包下发给客户端**。
 * 下发给小程序一律走 publicState()，它只带当前用户自己的词。
 */

const wordbank = require('../utils/wordbank')

/**
 * 卧底人数上限。
 * 规则是「卧底人数必须小于当前人数的一半」，即 2 * 卧底 < 人数，
 * 所以上限是 floor((n - 1) / 2)：4 人最多 1 个、5 人最多 2 个、10 人最多 4 个。
 */
function maxUndercover(playerCount) {
  if (!playerCount || playerCount < 2) return 0
  return Math.floor((playerCount - 1) / 2)
}

/** 按 userId 找人 */
function findByUserId(players, userId) {
  for (let i = 0; i < players.length; i++) {
    if (players[i].userId === userId) return players[i]
  }
  return null
}

/** 还在场上的玩家 */
function alivePlayers(state) {
  return state.players.filter(function (p) {
    return p.alive
  })
}

function shuffle(list) {
  const out = list.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    const t = out[i]
    out[i] = out[j]
    out[j] = t
  }
  return out
}

/* ============================ 开局 ============================ */

/**
 * 开一局：随机抽一组词，再随机挑出卧底。
 * @param {Array} members 房间成员 [{ userId, seat, nickname }]
 * @param {Number} undercoverCount 房主设定的卧底人数
 */
function createState(members, undercoverCount) {
  const draw = wordbank.draw()
  const count = Math.max(1, Math.min(Number(undercoverCount) || 1, maxUndercover(members.length)))

  // 洗牌后取前 count 个当卧底
  const picked = shuffle(members).slice(0, count)
  const undercoverIds = {}
  picked.forEach(function (m) {
    undercoverIds[m.userId] = true
  })

  const players = members.map(function (m) {
    const isUndercover = !!undercoverIds[m.userId]
    return {
      userId: m.userId,
      seat: m.seat,
      nickname: m.nickname,
      role: isUndercover ? 'undercover' : 'civilian',
      /** 这名玩家看到的词 */
      word: isUndercover ? draw.undercover : draw.civilian,
      alive: true,
      /** 本轮投给谁（null 表示还没投） */
      votedTarget: null,
      /** 是否中途退出房间 */
      left: false,
      /** 模拟玩家的预定投票时间（只有 mock 模式会用到） */
      botVoteAt: 0
    }
  })

  const state = {
    gameId: 'undercover',
    /** reveal 刚发牌 | voting 投票中 | result 本轮已结算 | over 本局结束 */
    phase: 'reveal',
    round: 1,
    undercoverCount: count,
    /** 抽中的词库序号，方便复盘时定位是哪组词 */
    wordIndex: draw.index,
    civilianWord: draw.civilian,
    undercoverWord: draw.undercover,
    players: players,
    roundResult: null,
    /** 'civilian' | 'undercover' | null */
    winner: null
  }

  // 开局就给模拟玩家排好投票时间，这样一个人也能把整局走完
  scheduleBots(state)
  return state
}

/* ============================ 下发 ============================ */

/**
 * 生成给某个客户端看的游戏状态。
 * 只带他自己的词和身份；别人的词一律不出现；投票只暴露「投没投」，不暴露投给谁。
 */
function publicState(state, meId) {
  if (!state) return null

  const alive = alivePlayers(state)
  const me = findByUserId(state.players, meId)
  const votedCount = alive.filter(function (p) {
    return !!p.votedTarget
  }).length

  // 我现在还能不能投票（自己出局了、本轮已结算、或整局已结束时都不能投）
  const canVoteNow = !!(me && me.alive && !state.winner && state.phase !== 'result')

  return {
    phase: state.phase,
    round: state.round,
    undercoverCount: state.undercoverCount,
    playerCount: state.players.length,
    aliveCount: alive.length,
    votedCount: votedCount,
    allVoted: alive.length > 0 && votedCount >= alive.length,

    myUserId: me ? me.userId : '',
    myRole: me ? me.role : '',
    myWord: me ? me.word : '',
    myAlive: me ? me.alive : false,
    myVotedTarget: me ? me.votedTarget : null,
    /** 我现在还有没有投票权 */
    canVoteNow: canVoteNow,

    players: state.players.map(function (p) {
      return {
        userId: p.userId,
        seat: p.seat,
        nickname: p.nickname,
        alive: p.alive,
        left: !!p.left,
        isMe: p.userId === meId,
        voted: !!p.votedTarget,
        /** 这一票现在能不能投给他（小程序列表直接用这个决定显不显示「投票」） */
        canVote: !!(canVoteNow && p.alive && p.userId !== meId)
      }
    }),

    /** 本轮结果（结算后才非空，含出局者身份与票型） */
    roundResult: state.roundResult,
    winner: state.winner
  }
}

/* ============================ 投票 ============================ */

/**
 * 投票。所有人都投完后自动结算本轮。
 */
function vote(state, voterId, targetUserId) {
  if (!state) throw new Error('游戏还没开始')
  if (state.phase === 'over') throw new Error('这局已经结束了')
  if (state.phase === 'result') throw new Error('本轮已经结算，等房主开下一轮')

  const voter = findByUserId(state.players, voterId)
  if (!voter) throw new Error('你不在这一局里')
  if (!voter.alive) throw new Error('你已经出局了，不能再投票')

  const target = findByUserId(state.players, targetUserId)
  if (!target || !target.alive) throw new Error('只能投给还在场上的玩家')
  if (target.userId === voterId) throw new Error('不能投自己')

  voter.votedTarget = targetUserId

  if (state.phase === 'reveal') state.phase = 'voting'

  // 全员投完 -> 结算
  const alive = alivePlayers(state)
  if (alive.length > 0 && alive.every(function (p) { return !!p.votedTarget })) {
    settle(state)
  }

  return state
}

/* ============================ 结算与判定 ============================ */

function settle(state) {
  const tally = {}
  alivePlayers(state).forEach(function (p) {
    if (!p.votedTarget) return
    tally[p.votedTarget] = (tally[p.votedTarget] || 0) + 1
  })

  let max = 0
  let top = []
  Object.keys(tally).forEach(function (uid) {
    const n = tally[uid]
    if (n > max) {
      max = n
      top = [uid]
    } else if (n === max) {
      top.push(uid)
    }
  })

  const rows = Object.keys(tally)
    .map(function (uid) {
      const p = findByUserId(state.players, uid)
      return { userId: uid, nickname: p ? p.nickname : '?', count: tally[uid] }
    })
    .sort(function (a, b) {
      return b.count - a.count
    })

  if (top.length === 1) {
    const out = findByUserId(state.players, top[0])
    out.alive = false
    state.roundResult = {
      tie: false,
      outUserId: out.userId,
      outNickname: out.nickname,
      outRole: out.role,
      outWord: out.word,
      tally: rows
    }
  } else {
    // 平票：本轮没人出局，直接进下一轮
    state.roundResult = {
      tie: true,
      outUserId: '',
      outNickname: '',
      outRole: '',
      outWord: '',
      tally: rows
    }
  }

  state.phase = 'result'
  checkWinner(state)
  return state
}

/**
 * 胜负判定：
 *   - 卧底全部出局 -> 平民胜
 *   - 卧底人数 >= 平民人数 -> 卧底胜
 */
function checkWinner(state) {
  const alive = alivePlayers(state)
  const uc = alive.filter(function (p) {
    return p.role === 'undercover'
  }).length
  const civilian = alive.length - uc

  if (uc === 0) state.winner = 'civilian'
  else if (uc >= civilian) state.winner = 'undercover'
  else state.winner = null

  if (state.winner) state.phase = 'over'
  return state
}

/* ============================ 下一轮 ============================ */

function nextRound(state) {
  if (!state) throw new Error('游戏还没开始')
  if (state.phase === 'over') throw new Error('这局已经结束了')
  if (state.phase !== 'result') throw new Error('本轮还没投完票')

  state.round += 1
  state.players.forEach(function (p) {
    p.votedTarget = null
  })
  state.roundResult = null
  state.phase = 'reveal'
  // 进入新一轮，给机器人重新排一次投票时间
  scheduleBots(state)
  return state
}

/* ============================ 成员变动 ============================ */

/**
 * 有人中途退出房间：把他标记成已出局，必要时立刻结算。
 * 否则「等所有人投完」会永远等不到那个人。
 */
function removePlayer(state, userId) {
  if (!state) return state
  const p = findByUserId(state.players, userId)
  if (!p || !p.alive) return state

  p.alive = false
  p.votedTarget = null
  p.left = true

  const alive = alivePlayers(state)
  if (alive.length > 0 && (state.phase === 'reveal' || state.phase === 'voting')) {
    if (alive.every(function (x) { return !!x.votedTarget })) {
      settle(state)
    } else {
      checkWinner(state)
    }
  } else if (state.phase === 'result') {
    checkWinner(state)
  }

  return state
}

/* ============================ 模拟玩家（仅 mock 模式有） ============================ */

/**
 * 「模拟玩家」只有 mock 模式下存在 —— 就是 mock/handlers.js 里 MOCK_AUTO_BOTS 塞进来的那几个。
 * 正式后端（Django）里没有这种东西，下面这些代码不会出现在后端。
 */
function isBot(userId) {
  return typeof userId === 'string' && userId.indexOf('bot_') === 0
}

/** 机器人决定投票的随机延迟：4 ~ 11 秒，看起来像是真人在想 */
function botDelay() {
  return 4000 + Math.floor(Math.random() * 7000)
}

/** 开局 / 新一轮时，给每个机器人安排一个投票时间 */
function scheduleBots(state) {
  if (!state) return state
  state.players.forEach(function (p) {
    p.botVoteAt = isBot(p.userId) ? Date.now() + botDelay() : 0
  })
  return state
}

/**
 * 让到点的机器人自己投票，并在全员投完后结算本轮。
 *
 * 调用时机：每次读取房间时（getRoom，轮询会走到）和玩家投完票后（castVote）。
 * 因为 mock 是「请求驱动」的，不这么做机器人永远不会动，
 * 一个人也就没法在开发者工具里把一局走完（卡在「已投 1/4」）。
 *
 * @returns {Boolean} 有没有发生改动（调用方据此决定要不要写回 storage）
 */
function tickBots(state) {
  if (!state) return false
  if (state.phase === 'result' || state.phase === 'over') return false

  const now = Date.now()
  const alive = alivePlayers(state)
  let changed = false

  alive.forEach(function (p) {
    if (!isBot(p.userId)) return
    if (p.votedTarget) return

    if (!p.botVoteAt) {
      p.botVoteAt = now + botDelay()
      return
    }
    if (now < p.botVoteAt) return

    // 随机投一个「还能投的人」：排除自己和已经出局的
    const candidates = alive.filter(function (x) {
      return x.userId !== p.userId
    })
    if (!candidates.length) return

    p.votedTarget = candidates[Math.floor(Math.random() * candidates.length)].userId
    changed = true
  })

  if (!changed) return false

  if (state.phase === 'reveal') state.phase = 'voting'

  // 机器人补上票之后，可能刚好凑齐全员投完
  const after = alivePlayers(state)
  if (after.length > 0 && after.every(function (x) { return !!x.votedTarget })) {
    settle(state)
  }

  return true
}

module.exports = {
  maxUndercover: maxUndercover,
  findByUserId: findByUserId,
  alivePlayers: alivePlayers,
  isBot: isBot,
  createState: createState,
  publicState: publicState,
  vote: vote,
  nextRound: nextRound,
  removePlayer: removePlayer,
  tickBots: tickBots
}
