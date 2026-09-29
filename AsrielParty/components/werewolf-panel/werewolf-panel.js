/**
 * 狼人杀 · 牌桌
 *
 * 房间进入 playing 后替换掉「左右两列座位 + 中间留空」，变成能真正操作的牌桌。
 * 布局固定是这一套：
 *
 *     ┌───────────────────────────────┐
 *     │ 第 2 天 · 投票 · 01:12         │  ← 顶栏
 *     ├──────┬─────────────────┬──────┤
 *     │ 1 号 │  进程记录        │ 2 号 │  ← 左右两列座位，中间是记录
 *     │ 3 号 │  第 1 夜         │ 4 号 │
 *     │ 5 号 │  3 号出局…       │ 6 号 │
 *     ├──────┴─────────────────┴──────┤
 *     │ 点上面的人投票    [弃票]        │  ← 底部操作区
 *     └───────────────────────────────┘
 *
 * 它只管「显示 + 收操作」，**规则判定全在后端**（party/werewolf.py）：
 *   - 夜晚：被唤醒的人选目标提交，别人只能等
 *   - 天亮：宣布昨夜死讯 → 开始发言
 *   - 发言：当前发言人点「我说完了」
 *   - 投票：点人投票（可以弃票），所有人表态完自动结算
 *   - 结算：公布谁出局、谁投给了谁 → 进入下一夜
 *   - 猎人出局等着开枪：选人开枪，或者放弃
 *
 * 身份可见性由后端决定（`gameState` 是「按当前用户过滤过」的），
 * 这里**不做任何二次裁切**，免得哪天规则改了前端变成漏勺。
 * 中间那栏的进程记录也是后端下发的公开事件，所有人看到的是同一份。
 *
 * 头像的点击语义：
 *   - 现在需要选人（夜晚 / 投票 / 开枪）→ 选中他
 *   - 其它时候 → 自己看身份卡、别人打标记（标记只存在本机）
 *
 * 所有改动都通过 triggerEvent('changed', { room }) 把最新房间丢回页面，
 * 由页面统一渲染，避免两处各存一份状态。
 */

const api = require('../../utils/api')
const util = require('../../utils/util')

/** 阶段名（和后端 party/models.py 里的 PHASE_* 一一对应） */
const PHASE_TEXT = {
  night: '夜晚',
  daybreak: '天亮了',
  sheriff: '警长竞选',
  speech: '发言',
  vote: '投票',
  pk_speech: 'PK 发言',
  pk_vote: 'PK 投票',
  result: '结算',
  over: '本局结束'
}

/**
 * 夜晚各步要不要选人。和后端 constants.ROLES 里的 night.action 对应，
 * 加新技能时这里也要补一行（不补的话按「要选人」处理）。
 */
const NIGHT_NEEDS_TARGET = {
  block: true,
  dream: true,
  kill: true,
  guard: true,
  check: true,
  witch: false, // 用药走专门的按钮，不用列表选人
  grave: false  // 守墓人查的是上一个被放逐的人，不用自己选
}

/** 开局时身份卡摆在中间自动显示，这么久之后收起来 */
const ROLE_CARD_MS = 4000

/** 守卫 / 摄梦人「不能连续两晚选同一个人」——摄梦人的上一晚目标后端会下发，守卫的只能自己记 */
function lastPickOf(gs, action, fallback) {
  if (action === 'dream' && gs.night) return gs.night.lastTargetUserId || null
  return fallback || null
}

/** 为什么这个人现在不能点；能点就返回空串 */
function lockReason(gs, row, ctx) {
  if (!row.alive) return '已经出局了'

  if (ctx.mode === 'night') {
    const action = ctx.action
    if (action === 'dream') {
      // 可以选自己（相当于自保）
      if (row.userId === ctx.lastPick) return '不能连续两晚选同一个人'
    }
    if (action === 'guard') {
      if (row.isMe) return '不能守自己'
      if (row.userId === ctx.lastPick) return '不能连续两晚守同一个人'
    }
    if (action === 'check' && row.isMe) return '不能查验自己'
    if (action === 'kill' && row.camp === 'wolf') return '不能袭击同伴'
    return ''
  }

  if (ctx.mode === 'vote') {
    // 可以投自己
    if (gs.phase === 'pk_vote' && gs.pkUserIds.indexOf(row.userId) < 0) {
      return 'PK 投票只能投参与 PK 的玩家'
    }
    return ''
  }

  if (ctx.mode === 'shoot') {
    if (row.isMe) return '不能带走自己'
    return ''
  }

  return ''
}

/** 组一个座位（左右两列里的一格） */
function buildSeat(p, mark) {
  return {
    userId: p.userId,
    seat: p.seat,
    seatNo: p.seat + 1,
    nickname: p.nickname || '玩家',
    alive: !!p.alive,
    left: !!p.left,
    isMe: !!p.isMe,
    isBot: !!p.isBot,
    // 后端只对「我自己」下发真实值，别人的一律 false —— 投票中谁投了谁是秘密
    voted: !!p.voted,
    roleName: p.roleName || '',
    camp: p.camp || '',
    markName: mark ? mark.name : '',
    markCamp: mark ? mark.camp : '',
    canPick: false,
    picked: false,
    lock: ''
  }
}

/**
 * 把 gameState 加工成 wxml 直接能用的视图模型。
 * 纯函数，方便反复重建（选中/取消目标时用）。
 */
function buildView(gs, local) {
  if (!gs) return null

  const me = gs.players.filter(function (p) {
    return p.isMe
  })[0] || null
  const myUserId = me ? me.userId : null

  const markOf = {}
  ;(local.marks || []).forEach(function (m) {
    markOf[m.userId] = m
  })

  const seats = gs.players
    .map(function (p) {
      return buildSeat(p, markOf[p.userId])
    })
    .sort(function (a, b) {
      return a.seat - b.seat
    })

  const night = gs.night || null
  const shooting = !!gs.pendingShooterUserId
  const isNight = gs.phase === 'night'
  const isVote = gs.phase === 'vote' || gs.phase === 'pk_vote'
  const action = night ? night.stepAction : ''
  const myTurn = !!(night && night.isMyTurn)

  // ---- 这一屏到底要不要选人、选了要干什么 ----
  let mode = ''
  if (shooting) {
    mode = gs.canShoot ? 'shoot' : ''
  } else if (isNight && myTurn) {
    if (action === 'witch') {
      mode = local.witchPoison ? 'night' : ''
    } else if (NIGHT_NEEDS_TARGET[action]) {
      mode = 'night'
    }
  } else if (isVote && gs.myAlive) {
    // PK 玩家本人这一轮不投票
    mode = gs.phase === 'pk_vote' && gs.pkUserIds.indexOf(myUserId) >= 0 ? '' : 'vote'
  }

  const ctx = {
    mode: mode,
    action: action,
    lastPick: night ? lastPickOf(gs, action, local.lastGuardTargetId) : null
  }

  seats.forEach(function (row) {
    const lock = lockReason(gs, row, ctx)
    row.lock = lock
    row.canPick = lock === ''
    row.picked = row.userId === local.pickedUserId
  })

  // 左右两列：前半边左、后半边右，座位号从小到大
  const half = Math.ceil(seats.length / 2)
  const leftSeats = seats.slice(0, half)
  const rightSeats = seats.slice(half)

  // ---- 顶栏 ----
  const day = gs.day || 0
  const dayText = gs.phase === 'night'
    ? '第 ' + (day + 1) + ' 夜'
    : (day > 0 ? '第 ' + day + ' 天' : '开局')

  // ---- 我的身份卡（开局自动弹一次，之后点自己头像随时能看） ----
  const chips = []
  if (gs.myRoleId === 'witch') {
    chips.push({ id: 'heal', text: '解药' + (gs.mySkills.healUsed ? '已用' : '在手'), used: gs.mySkills.healUsed })
    chips.push({ id: 'poison', text: '毒药' + (gs.mySkills.poisonUsed ? '已用' : '在手'), used: gs.mySkills.poisonUsed })
  }
  if (gs.myRoleId === 'hunter') {
    chips.push({ id: 'shot', text: '枪' + (gs.mySkills.shotUsed ? '已开' : '在手'), used: gs.mySkills.shotUsed })
  }
  if (typeof gs.myDisguised === 'boolean') {
    chips.push({ id: 'hide', text: gs.myDisguised ? '仍在伪装' : '伪装已失效', used: !gs.myDisguised })
  }
  if (gs.myBlock) {
    chips.push({
      id: 'block',
      text: gs.myBlock.knownBroken ? '封锁已失效' : '封锁可用',
      used: !!gs.myBlock.knownBroken
    })
  }

  // 狼人知道自己队友是谁；其他人只看得到「我是谁」
  const mates = []
  if (gs.myCamp === 'wolf') {
    seats.forEach(function (row) {
      if (row.userId !== myUserId && row.camp === 'wolf') {
        mates.push({ id: 'mate-' + row.userId, seatNo: row.seatNo, nickname: row.nickname })
      }
    })
  }

  const campText = gs.myCamp === 'wolf' ? '狼人阵营' : (gs.myCamp === 'good' ? '好人阵营' : '')
  let sub = campText
  if (gs.mySeat !== null && gs.mySeat !== undefined) {
    sub += (sub ? ' · ' : '') + (gs.mySeat + 1) + ' 号'
  }
  if (!gs.myAlive) sub += (sub ? ' · ' : '') + '已出局'

  const roleCard = {
    emoji: gs.myRoleEmoji || '❓',
    roleName: gs.myRoleName || '未知身份',
    sub: sub,
    chips: chips,
    mates: mates
  }

  // ---- 进程记录（后端下发的公开事件，正序 = 从早到晚） ----
  const log = []
  ;(gs.gameLog || []).forEach(function (e) {
    const item = {
      id: 'log-' + e.id,
      kind: e.kind,
      tone: e.tone || 'info',
      text: e.text,
      // 票型按**投给谁**分组：「投给 3 号的：1、2、5」
      ticketLines: []
    }

    const groups = []
    const byTarget = {}
    ;(e.ballots || []).forEach(function (b) {
      const abstain = b.targetSeat === null || b.targetSeat === undefined
      const key = abstain ? 'abstain' : String(b.targetUserId)

      if (!byTarget[key]) {
        byTarget[key] = {
          id: 'g-' + e.id + '-' + key,
          abstain: abstain,
          seatNo: abstain ? 0 : (b.targetSeat + 1),
          voters: []
        }
        groups.push(byTarget[key])
      }
      if (b.voterSeat !== null && b.voterSeat !== undefined) {
        byTarget[key].voters.push(b.voterSeat + 1)
      }
    })

    // 目标座位号从小到大，弃票的垫在最后
    groups.sort(function (a, b) {
      if (a.abstain !== b.abstain) return a.abstain ? 1 : -1
      return a.seatNo - b.seatNo
    })
    groups.forEach(function (g) {
      g.voters.sort(function (a, b) {
        return a - b
      })
      g.voterText = g.voters.join('、')
      item.ticketLines.push(g)
    })

    log.push(item)
  })

  // 结算 / 结束横幅（白天出局的人**不公布身份**，身份只在 over 时全公开）
  let banner = null
  if (gs.winner) {
    banner = {
      tone: gs.winner === 'good' ? 'is-good' : 'is-wolf',
      title: gs.winner === 'good' ? '🎉 好人阵营获胜' : '🐺 狼人阵营获胜',
      sub: gs.winReason || ''
    }
  } else if (gs.roundResult) {
    const r = gs.roundResult
    banner = {
      tone: r.tie ? 'is-tie' : 'is-out',
      title: r.tie
        ? '平票，本轮没人出局'
        : ('☠️ ' + (r.outSeat + 1) + ' 号 ' + r.outNickname + ' 出局'),
      sub: r.tie ? (r.note || '') : '请留遗言'
    }
  }

  // ---- 底部操作区 ----
  const stage = {
    title: '',
    note: '',
    hint: '',
    lines: [],
    buttons: []
  }

  const isMySpeech = !!(gs.currentSpeakerUserId && gs.currentSpeakerUserId === myUserId)

  if (shooting) {
    const mine = gs.canShoot
    stage.title = '猎人开枪'
    stage.note = (gs.pendingShooterSeat !== null ? (gs.pendingShooterSeat + 1) + ' 号' : '')
    stage.hint = mine
      ? '带走一个人，或者放弃开枪'
      : ('等 ' + (gs.pendingShooterSeat !== null ? (gs.pendingShooterSeat + 1) + ' 号' : '猎人') + ' 决定开不开枪…')
    if (mine) {
      stage.buttons = [
        { key: 'shoot', text: '开枪', tone: 'is-primary', disabled: !local.pickedUserId },
        { key: 'skip', text: '放弃', tone: 'is-ghost', disabled: false }
      ]
    }
  } else if (isNight) {
    stage.title = (night ? night.stepLabel : '夜晚') + (night && night.stepRoleName ? ' · ' + night.stepRoleName : '')
    stage.hint = night ? night.stepHint : ''

    if (!gs.myAlive) {
      stage.hint = '你已经出局了，等这一局结束'
    } else if (myTurn) {
      if (action === 'witch') {
        if (night.wolfTargetSeat !== null && night.wolfTargetSeat !== undefined) {
          stage.lines.push({ text: '今晚 ' + (night.wolfTargetSeat + 1) + ' 号被狼人袭击了' })
        } else {
          stage.lines.push({ text: '今晚没有人被狼人袭击' })
        }
        if (local.witchPoison) {
          stage.hint = '选一个人下毒'
          stage.buttons = [
            { key: 'poison', text: '确认下毒', tone: 'is-danger', disabled: !local.pickedUserId },
            { key: 'cancel-poison', text: '算了', tone: 'is-ghost', disabled: false }
          ]
        } else {
          const canHeal = !gs.mySkills.healUsed
            && !!night.wolfTargetUserId
            && night.wolfTargetUserId !== myUserId
          const canPoison = !gs.mySkills.poisonUsed
          const buttons = []
          if (canHeal) {
            buttons.push({ key: 'save', text: '用解药救他', tone: 'is-primary', disabled: false })
          } else if (!gs.mySkills.healUsed && night.wolfTargetUserId === myUserId) {
            stage.lines.push({ text: '（被袭击的是你自己，这个版型的解药不能自救）' })
          }
          if (canPoison) {
            buttons.push({ key: 'poison-mode', text: '用毒药', tone: 'is-danger', disabled: false })
          }
          buttons.push({ key: 'skip', text: '这晚不用药', tone: 'is-ghost', disabled: false })
          stage.buttons = buttons
        }
      } else if (action === 'dream') {
        if (night.dreamPicked) {
          // 梦语者已经定下了 —— 这时读心结果也出来了，让她看着结果再决定放不放逐
          stage.hint = night.canSlay
            ? '梦语者已定。你已知他今晚动没动 —— 要不要直接让他出局？'
            : '梦语者已定。点「就这样」结束这一晚'
          const buttons = []
          if (night.canSlay) {
            buttons.push({ key: 'slay', text: '让他出局', tone: 'is-danger', disabled: false })
          }
          buttons.push({ key: 'confirm-dream', text: '就这样', tone: 'is-primary', disabled: false })
          stage.buttons = buttons
        } else {
          if (night.lastTargetSeat !== null && night.lastTargetSeat !== undefined) {
            stage.lines.push({ text: '昨晚的梦语者是 ' + (night.lastTargetSeat + 1) + ' 号，今晚不能重复' })
          }
          stage.buttons = [
            { key: 'act', text: '确认梦语者', tone: 'is-primary', disabled: !local.pickedUserId }
          ]
        }
      } else if (action === 'block') {
        if (night.canBlock === false) {
          stage.hint = '封锁已经用掉并失效了，这一晚跳过就行'
          stage.buttons = [{ key: 'skip', text: '跳过', tone: 'is-ghost', disabled: false }]
        } else {
          stage.buttons = [
            { key: 'act', text: '确认封锁', tone: 'is-primary', disabled: !local.pickedUserId }
          ]
        }
      } else if (action === 'kill') {
        if (night.wolfChoices && night.wolfChoices.length) {
          night.wolfChoices.forEach(function (c) {
            stage.lines.push({
              text: (c.seat + 1) + ' 号狼选了 ' + (c.targetSeat !== null ? (c.targetSeat + 1) + ' 号' : '还没选')
            })
          })
        }
        stage.buttons = [
          { key: 'act', text: '确认刀人', tone: 'is-primary', disabled: !local.pickedUserId }
        ]
      } else if (action === 'grave') {
        stage.buttons = [{ key: 'act', text: '查看验尸结果', tone: 'is-primary', disabled: false }]
      } else {
        const label = action === 'check' ? '确认查验' : (action === 'guard' ? '确认守护' : '确认')
        stage.buttons = [
          { key: 'act', text: label, tone: 'is-primary', disabled: !local.pickedUserId }
        ]
      }
    } else {
      stage.hint = '等「' + (night ? night.stepRoleName : '其他人') + '」行动…'
    }

    if (night && night.actedCount) {
      stage.note = night.actedCount + '/' + night.actorCount
    }
  } else if (gs.phase === 'daybreak') {
    stage.title = '天亮了'
    const deaths = gs.lastNightDeaths || []
    if (gs.peacefulNight) {
      stage.hint = '昨夜是个平安夜，没有人出局'
    } else if (deaths.length) {
      stage.hint = '昨夜出局：' + deaths.map(function (d) {
        return (d.seat + 1) + ' 号 ' + d.nickname
      }).join('、')
    } else {
      stage.hint = '昨夜没有人出局'
    }
    stage.buttons = [{ key: 'advance', text: '开始发言', tone: 'is-primary', disabled: false }]
  } else if (gs.phase === 'speech' || gs.phase === 'pk_speech') {
    stage.title = gs.phase === 'pk_speech' ? 'PK 发言' : '发言'
    stage.note = (gs.speechIndex + 1) + '/' + (gs.speechOrder || []).length
    if (isMySpeech) {
      stage.hint = '轮到你发言了，说完点下面'
      stage.buttons = [{ key: 'advance', text: '我说完了', tone: 'is-primary', disabled: false }]
    } else {
      stage.hint = '等 ' + (gs.currentSpeakerSeat !== null ? (gs.currentSpeakerSeat + 1) + ' 号 ' : '') + '发言…'
    }
  } else if (isVote) {
    stage.title = gs.phase === 'pk_vote' ? 'PK 投票' : '放逐投票'
    // 不显示「几个人投了」——投票期间谁都不该知道别人投没投
    const mySeat = seats.filter(function (p) {
      return p.isMe
    })[0]
    if (!gs.myAlive) {
      stage.hint = '你已经出局了，只能围观'
    } else if (mode !== 'vote') {
      stage.hint = '你在 PK 里，这一轮不投票'
    } else if (mySeat && mySeat.voted) {
      stage.hint = '你投过票了，点别人可以改票'
      stage.buttons = [{ key: 'abstain', text: '弃票', tone: 'is-ghost', disabled: false }]
    } else {
      stage.hint = '点左边或右边的人投票；也可以弃票'
      stage.buttons = [{ key: 'abstain', text: '弃票', tone: 'is-ghost', disabled: false }]
    }
  } else if (gs.phase === 'result') {
    stage.title = '本轮结算'
    stage.hint = gs.roundResult && gs.roundResult.note ? gs.roundResult.note : '看完就能进入下一夜'
    stage.buttons = [{ key: 'advance', text: '进入下一夜', tone: 'is-primary', disabled: false }]
  } else if (gs.phase === 'over') {
    stage.title = '本局结束'
    stage.hint = gs.winReason || ''
  } else {
    stage.title = PHASE_TEXT[gs.phase] || gs.phase
    stage.hint = '…'
  }

  // ---- 我的记录（查验 / 验尸 / 摄梦读心），只有自己看得到 ----
  const checks = []
  ;(gs.myChecks || []).forEach(function (c) {
    checks.push({
      id: 'check-' + c.userId,
      userId: c.userId,
      seatNo: c.seat !== null ? c.seat + 1 : '?',
      nickname: c.nickname,
      text: c.result === 'wolf' ? '狼人' : '好人',
      tone: c.result === 'wolf' ? 'is-wolf' : 'is-good'
    })
  })
  if (gs.myDream) {
    checks.push({
      id: 'dream-' + gs.myDream.targetUserId,
      userId: gs.myDream.targetUserId,
      seatNo: gs.myDream.targetSeat !== null ? gs.myDream.targetSeat + 1 : '?',
      nickname: gs.myDream.targetNickname,
      // 摄梦人排在最后行动，选完当场就有结果
      text: gs.myDream.targetActed ? '行动过' : '没行动',
      tone: gs.myDream.targetActed ? 'is-wolf' : 'is-good'
    })
  }

  return {
    phase: gs.phase,
    dayText: dayText,
    phaseText: PHASE_TEXT[gs.phase] || gs.phase,
    roleCard: roleCard,
    showRoleCard: !!local.showRole,
    banner: banner,
    stage: stage,
    log: log,
    logAnchor: log.length ? log[log.length - 1].id : '',
    checks: checks,
    leftSeats: leftSeats,
    rightSeats: rightSeats,
    canPick: mode !== '',
    pickMode: mode
  }
}

Component({
  properties: {
    /** 房间对象（api.decorateRoom 产出） */
    room: {
      type: Object,
      value: null
    },
    isOwner: {
      type: Boolean,
      value: false
    },
    /**
     * 我给各人打的标签：[{ userId, name, camp }]。
     * 只存在本机（房间页负责存取），牌桌只管显示。
     */
    marks: {
      type: Array,
      value: []
    }
  },

  data: {
    view: null,
    /** 正忙（提交中），避免连点 */
    busy: false,
    /** 倒计时文本 */
    timerText: '',
    /** 当前选中的目标 userId */
    pickedUserId: null,
    /** 女巫：是不是正在选毒药目标 */
    witchPoison: false,
    /** 身份卡是不是摆出来了 */
    showRole: false
  },

  observers: {
    'room.gameState': function (gs) {
      this.syncFrom(gs)
    },
    marks: function () {
      this.rebuild()
    }
  },

  lifetimes: {
    attached: function () {
      this._stageKey = ''
      this._lastGuardTargetId = null
      this._timer = null
      this._roleTimer = null
      // 这一局的身份卡是不是已经自动摆过了（一次就够，之后点自己头像看）
      this._roleShown = false
    },
    detached: function () {
      this.stopTimer()
      this.stopRoleTimer()
    }
  },

  methods: {
    /* ============================ 状态同步 ============================ */

    /**
     * 每次轮询（3 秒）都会走到这里。只有「阶段/步骤」真的变了才清掉本地的选择，
     * 否则用户选到一半就被刷新冲掉了。
     */
    syncFrom: function (gs) {
      this._gs = gs

      if (!gs) {
        this.stopTimer()
        this.setData({ view: null, timerText: '' })
        return
      }

      const key = [
        gs.phase,
        gs.day,
        gs.night ? gs.night.stepAction : '',
        gs.night && gs.night.isMyTurn ? 1 : 0,
        gs.pendingShooterUserId || '',
        // 摄梦人定完梦语者之后是「另一步」，这里也要体现出来
        gs.night && gs.night.dreamPicked ? 1 : 0
      ].join('|')

      if (key !== this._stageKey) {
        // 玩家刚进牌桌（第一夜、还没天亮的这一局）→ 把身份卡摆出来看一眼
        const fresh = !this._roleShown && gs.phase === 'night' && (gs.day || 0) === 0
        if (fresh) {
          this._roleShown = true
          this.showRoleCard(ROLE_CARD_MS)
        }

        this._stageKey = key
        this.setData({ pickedUserId: null, witchPoison: false })
      }

      this.rebuild()
      this.startTimer(gs)
    },

    /** 用当前状态重建视图模型（选中/取消目标时复用） */
    rebuild: function () {
      const gs = this._gs
      if (!gs) return

      const view = buildView(gs, {
        pickedUserId: this.data.pickedUserId,
        witchPoison: this.data.witchPoison,
        lastGuardTargetId: this._lastGuardTargetId,
        marks: this.data.marks,
        showRole: this.data.showRole
      })

      this.setData({ view: view })
    },

    /* ============================ 身份卡 ============================ */

    /** 摆出身份卡；afterMs 之后自动收起（不给就是一直摆着，等用户点掉） */
    showRoleCard: function (afterMs) {
      const that = this
      this.stopRoleTimer()
      this.setData({ showRole: true })
      this.rebuild()

      if (afterMs) {
        this._roleTimer = setTimeout(function () {
          that._roleTimer = null
          that.hideRoleCard()
        }, afterMs)
      }
    },

    hideRoleCard: function () {
      this.stopRoleTimer()
      if (!this.data.showRole) return
      this.setData({ showRole: false })
      this.rebuild()
    },

    stopRoleTimer: function () {
      if (this._roleTimer) {
        clearTimeout(this._roleTimer)
        this._roleTimer = null
      }
    },

    onTapRoleMask: function () {
      this.hideRoleCard()
    },

    /** 身份卡本体上的点击：把事件吃掉，免得点卡片也把自己关了 */
    noop: function () {},

    /** 身份卡里的「知道了」 */
    onRoleOk: function () {
      this.hideRoleCard()
    },

    /* ============================ 倒计时 ============================ */

    /**
     * 剩余时间以服务端的 serverNow 为准，别用本机时间（手机时钟不一定准）。
     * 倒计时只是提示，到点了后端也不会强制推进。
     */
    startTimer: function (gs) {
      this.stopTimer()

      const duration = gs.phaseDuration || 0
      if (!duration || !gs.phaseStartedAt) {
        if (this.data.timerText) this.setData({ timerText: '' })
        return
      }

      const passed = Math.max(0, Math.round((gs.serverNow - gs.phaseStartedAt) / 1000))
      let remain = Math.max(0, duration - passed)
      const that = this

      const paint = function () {
        that.setData({ timerText: util.formatDuration(remain) })
      }
      paint()

      if (remain <= 0) return
      this._timer = setInterval(function () {
        remain -= 1
        if (remain <= 0) {
          remain = 0
          that.stopTimer()
        }
        paint()
      }, 1000)
    },

    stopTimer: function () {
      if (this._timer) {
        clearInterval(this._timer)
        this._timer = null
      }
    },

    /* ============================ 交互 ============================ */

    /**
     * 点座位。三种含义，按当前要干什么决定：
     *   1. 需要选人（夜晚 / 投票 / 开枪）→ 选他（投票是直接投）
     *   2. 点自己 → 看自己的身份卡
     *   3. 点别人 → 给他打标记（不弹窗的话，房间页只记在本机）
     */
    onTapSeat: function (e) {
      const view = this.data.view
      if (!view || this.data.busy) return

      const userId = Number(e.currentTarget.dataset.id)
      const row = this.findSeat(userId)
      if (!row) return

      if (row.canPick) {
        if (view.pickMode === 'vote') {
          this.confirmVote(row)
          return
        }
        // 夜晚 / 开枪：选中或取消
        const next = this.data.pickedUserId === userId ? null : userId
        this.setData({ pickedUserId: next })
        this.rebuild()
        return
      }

      // 不能选的时候：自己看身份，别人打标记
      if (row.isMe) {
        this.showRoleCard(0)
        return
      }
      if (!row.alive) {
        util.toast(row.lock || '已经出局了')
        return
      }
      this.askMark(row)
    },

    /** 长按座位：不管现在要不要选人，都按「打标记」处理（自己在的话看身份） */
    onLongPressSeat: function (e) {
      const row = this.findSeat(Number(e.currentTarget.dataset.id))
      if (!row || this.data.busy) return

      if (row.isMe) {
        this.showRoleCard(0)
        return
      }
      if (!row.alive) return
      this.askMark(row)
    },

    findSeat: function (userId) {
      const view = this.data.view
      if (!view) return null
      const all = (view.leftSeats || []).concat(view.rightSeats || [])
      return all.filter(function (r) {
        return r.userId === userId
      })[0] || null
    },

    /** 打标记这件事交给房间页（它管着本机的记录，也管着弹窗） */
    askMark: function (row) {
      this.triggerEvent('mark', {
        userId: row.userId,
        nickname: row.nickname
      })
    },

    /** 底部操作区上的按钮 */
    onStageButton: function (e) {
      if (this.data.busy) return

      const key = e.currentTarget.dataset.key
      const view = this.data.view
      const gs = this._gs
      if (!view || !gs) return

      // disabled 只是样式，点得动 —— 这里挡住
      const btn = (view.stage.buttons || []).filter(function (b) {
        return b.key === key
      })[0]
      if (!btn || btn.disabled) return

      if (key === 'advance') {
        this.submit('advance')
        return
      }
      if (key === 'abstain') {
        this.submit('vote', { targetUserId: null })
        return
      }
      if (key === 'skip') {
        // 女巫不用药 / 狼妃跳过 / 猎人放弃开枪
        if (gs.pendingShooterUserId) this.submit('act', { targetUserId: null })
        else this.submit('act', { targetUserId: null, skill: '' })
        return
      }
      if (key === 'save') {
        this.submit('act', { targetUserId: null, skill: 'save' })
        return
      }
      if (key === 'poison-mode') {
        this.setData({ witchPoison: true, pickedUserId: null })
        this.rebuild()
        return
      }
      if (key === 'cancel-poison') {
        this.setData({ witchPoison: false, pickedUserId: null })
        this.rebuild()
        return
      }
      if (key === 'poison') {
        this.submit('act', { targetUserId: this.data.pickedUserId, skill: 'poison' })
        return
      }
      if (key === 'shoot') {
        this.submit('act', { targetUserId: this.data.pickedUserId })
        return
      }
      // 摄梦人第二步：梦语者上一步就定好了，这里只是「就这样 / 让他出局」
      if (key === 'confirm-dream') {
        this.submit('act', { targetUserId: null })
        return
      }
      if (key === 'slay') {
        this.submit('act', { targetUserId: null, skill: 'slay' })
        return
      }
      if (key === 'act') {
        this.submit('act', { targetUserId: this.data.pickedUserId })
        return
      }
    },

    /** 投票（带一次确认，免得点错） */
    confirmVote: function (row) {
      const that = this
      util.confirm('确定投「' + row.nickname + '」吗？', '投票').then(function (ok) {
        if (!ok) return
        that.submit('vote', { targetUserId: row.userId })
      })
    },

    /**
     * 统一提交。成功后把最新的房间丢回页面；失败就把后端的原话弹出来
     * （后端的报错都是给用户看的中文）。
     */
    submit: function (kind, payload) {
      const room = this.data.room
      if (!room) return

      const that = this
      const data = Object.assign({ roomId: room.id }, payload || {})

      this.setData({ busy: true })
      util.loading(kind === 'vote' ? '投票中' : (kind === 'advance' ? '推进中' : '提交中'))

      let task
      if (kind === 'vote') task = api.werewolfVote(data)
      else if (kind === 'advance') task = api.werewolfAdvance({ roomId: data.roomId })
      else task = api.werewolfAct(data)

      task
        .then(function (next) {
          util.hideLoading()
          that.setData({ busy: false, pickedUserId: null, witchPoison: false })

          // 记住这一晚守卫守了谁（后端没下发，只能本机记）
          if (kind === 'act' && that._gs && that._gs.night && that._gs.night.stepAction === 'guard') {
            that._lastGuardTargetId = payload.targetUserId || null
          }

          if (next) that.triggerEvent('changed', { room: next })
        })
        .catch(function (err) {
          util.hideLoading()
          that.setData({ busy: false })
          util.toast(util.errText(err))
        })
    }
  }
})
