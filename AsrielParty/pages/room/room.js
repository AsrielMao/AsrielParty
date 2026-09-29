// pages/room/room.js
const api = require('../../utils/api')
const util = require('../../utils/util')
const voice = require('../../utils/voice')
const roomView = require('../../utils/room-view')
const gameData = require('../../utils/games')
const tableNotes = require('../../utils/table-notes')
const CONFIG = require('../../config/index')

/** 谁是卧底的游戏 id（要和 utils/games.js 里的一致） */
const GAME_UNDERCOVER = 'undercover'
/** 狼人杀的游戏 id */
const GAME_WEREWOLF = 'werewolf'
/** 你画我猜的游戏 id */
const GAME_DRAW = 'draw-guess'

/** 房间是不是已经没了（被解散 / 不存在） */
function isRoomGone(err) {
  if (!err) return false
  if (err.statusCode === 404) return true
  return /不存在|解散|已结束/.test(err.message || '')
}

/**
 * 卧底人数上限：规则是「必须小于房间人数的一半」，即 2 * 卧底 < 人数。
 * 4 人最多 1 个、5 人最多 2 个、10 人最多 4 个。
 * 这份算法和 mock/undercover.js 里的 maxUndercover 必须保持一致（后端做最终校验）。
 */
function maxUndercoverOf(playerCount) {
  if (!playerCount || playerCount < 2) return 0
  return Math.floor((playerCount - 1) / 2)
}

Page({
  data: {
    roomId: '',
    room: null,
    slots: [],
    loading: true,
    failed: '',

    games: [],
    gamePickerVisible: false,

    voice: voice.getState(),
    voiceMax: CONFIG.voiceMaxPlayers,

    // 派生展示状态
    isOwner: false,
    myReady: false,
    readyCount: 0,
    startHint: '',
    mainBtnText: '准备',
    mainBtnDisabled: false,

    // 谁是卧底
    playing: false,
    showUndercoverSetup: false,
    maxUndercover: 0,

    // 狼人杀：左右两列座位（中间留空给背景，开局后变成进程记录）
    isWerewolf: false,
    variant: null,
    seatColumns: [],
    /** 牌桌里要显示的标记（只存本机） */
    wwMarks: [],
    /** 点头像后弹出的标签列表 */
    markOptions: [],
    markSheetVisible: false,
    /** 正在给谁打标签 { userId, nickname, markId } */
    markTarget: null
  },

  onLoad: function (options) {
    const opts = options || {}
    this._gone = false
    this._gamesLoaded = false

    this._voiceUnsub = voice.subscribe(this.onVoiceState.bind(this))
    this.setData({ voice: voice.getState() })

    this.enter(opts)
  },

  onUnload: function () {
    this.stopPolling()
    if (this._voiceUnsub) this._voiceUnsub()
    // 离开房间页就不再占着语音通道
    voice.leave()
  },

  onHide: function () {
    this.stopPolling()
  },

  onShow: function () {
    if (this.data.room) {
      this.startPolling()
      this.refresh(true)
    }
  },

  /* ============================ 进入房间 ============================ */

  enter: function (opts) {
    const that = this
    const roomId = opts.roomId || ''
    const code = opts.code || ''

    const task = roomId ? api.getRoom({ roomId: roomId }) : api.joinRoom({ code: code })

    task
      .then(function (room) {
        // 从分享卡片进来但还不是成员时，自动加入
        if (!room.me) return api.joinRoom({ roomId: room.id })
        return room
      })
      .then(function (room) {
        that.setData({ roomId: room.id, loading: false, failed: '' })
        that.render(room)
        that.startPolling()
        that.loadGames()
      })
      .catch(function (err) {
        that.setData({ loading: false, failed: (err && err.message) || '进入房间失败' })
      })
  },

  loadGames: function () {
    if (this._gamesLoaded) return
    this._gamesLoaded = true
    const that = this
    api
      .listGames()
      .then(function (list) {
        that.setData({ games: list })
      })
      .catch(function () {
        that._gamesLoaded = false
      })
  },

  /* ============================ 渲染 ============================ */

  render: function (room) {
    if (!room) return

    const slots = roomView.buildSeatSlots(room)
    const speakers = (this.data.voice && this.data.voice.speakerOpenids) || []
    slots.forEach(function (s) {
      s.speaking = !!(s.member && s.member.openid && speakers.indexOf(s.member.openid) > -1)
    })

    const game = room.game
    const me = room.me
    const isOwner = !!room.isOwner
    const myReady = !!(me && me.ready)
    const readyCount = room.members.filter(function (m) {
      return m.ready
    }).length
    const need = game ? game.minPlayers : 2
    const enough = room.memberCount >= need
    const playing = room.status === 'playing'

    let startHint = ''
    if (playing) {
      startHint = '游戏进行中'
    } else if (!enough) {
      startHint = '还差 ' + (need - room.memberCount) + ' 人才能开始'
      if (!room.botCount) {
        startHint += ' · 人不够可以加人机'
      }
    } else if (readyCount < room.memberCount) {
      startHint = readyCount + '/' + room.memberCount + ' 人已准备'
    } else {
      startHint = '全员已准备，可以开始了'
    }

    let mainBtnText = ''
    let mainBtnDisabled = false
    if (playing) {
      mainBtnText = isOwner ? '结束本局' : '游戏进行中'
      mainBtnDisabled = !isOwner
    } else if (isOwner) {
      mainBtnText = '开始游戏'
      mainBtnDisabled = !enough
    } else {
      mainBtnText = myReady ? '取消准备' : '准备'
      mainBtnDisabled = false
    }

    // 谁是卧底：开局前让房主看到（并能改）卧底人数
    const showUndercoverSetup = !playing && room.gameId === GAME_UNDERCOVER

    // 狼人杀：座位分左右两列，中间放记事板
    const isWerewolf = room.gameId === GAME_WEREWOLF
    const gameDef = gameData.getGame(room.gameId)
    const variant = isWerewolf ? gameData.getVariant(gameDef, room.variantId) : null
    const markOptions = isWerewolf ? gameData.marksOf(gameDef, room.variantId) : []

    const marks = this.ensureNotes(room)

    // 给每个座位挂上「我给他打的标签」（只在本机，别人看不到）
    const marked = slots.map(function (s) {
      if (!s.member) return s
      const markId = marks[s.member.userId]
      if (!markId) return s

      const mark = markOptions.filter(function (m) {
        return m.id === markId
      })[0]
      if (!mark) return s

      const copy = Object.assign({}, s)
      copy.mark = mark
      return copy
    })

    const half = Math.ceil(marked.length / 2)
    const seatColumns = isWerewolf
      ? [
          { side: 'left', seats: marked.slice(0, half) },
          { side: 'right', seats: marked.slice(half) }
        ]
      : []

    // 牌桌里也要显示这些标签（进程记录那栏大家看的是同一份，标签只在本机）
    const wwMarks = isWerewolf
      ? marked
          .filter(function (s) {
            return s.member && s.mark
          })
          .map(function (s) {
            return { userId: s.member.userId, name: s.mark.name, camp: s.mark.camp }
          })
      : []

    this.setData({
      room: room,
      slots: marked,
      isOwner: isOwner,
      myReady: myReady,
      readyCount: readyCount,
      startHint: startHint,
      mainBtnText: mainBtnText,
      mainBtnDisabled: mainBtnDisabled,
      playing: playing,
      showUndercoverSetup: showUndercoverSetup,
      maxUndercover: maxUndercoverOf(room.memberCount),

      isWerewolf: isWerewolf,
      variant: variant,
      seatColumns: seatColumns,
      wwMarks: wwMarks,
      markOptions: markOptions
    })
  },

  /* ============================ 狼人杀：本机标记 ============================ */

  /**
   * 首次渲染时把本机的私人记录（给各人打的标签）读出来。
   * 只存在本机：不上传、不同步、也不发给别人。
   */
  ensureNotes: function (room) {
    if (this._notes) return this._notes.marks

    const me = room.me
    const userId = me ? me.userId : ''
    this._notes = tableNotes.load(room.id, userId)
    this._notesRoomId = room.id
    this._notesUserId = userId

    return this._notes.marks
  },

  /** 把改动写回本机 */
  saveNotes: function () {
    if (!this._notes) return
    tableNotes.save(this._notesRoomId, this._notesUserId, this._notes)
  },

  /**
   * 牌桌里点了别人的头像：打开同一个标记弹窗。
   * 标记数据还是本机这一份（`this._notes.marks`），牌桌只是把它显示出来。
   */
  onPanelMark: function (e) {
    const detail = e.detail || {}
    const userId = detail.userId
    if (!userId) return

    const slot = this.data.slots.filter(function (s) {
      return s.member && s.member.userId === userId
    })[0]

    this.setData({
      markSheetVisible: true,
      markTarget: {
        userId: userId,
        nickname: (slot && slot.member.nickname) || detail.nickname || '',
        markId: (this._notes && this._notes.marks[userId]) || ''
      }
    })
  },

  /** 点座位：自己不用标，其他人可以打个标签帮自己记 */
  onWwSeatTap: function (e) {
    const index = Number(e.currentTarget.dataset.index)
    let slot = null

    this.data.seatColumns.forEach(function (col) {
      col.seats.forEach(function (s) {
        if (s.seatIndex === index) slot = s
      })
    })

    if (!slot || !slot.member) {
      util.toast('这是个空位，点「邀请」叫人来')
      return
    }
    if (slot.member.isSelf) {
      util.toast('不用给自己打标签')
      return
    }

    this.setData({
      markSheetVisible: true,
      markTarget: {
        userId: slot.member.userId,
        nickname: slot.member.nickname,
        markId: (this._notes && this._notes.marks[slot.member.userId]) || ''
      }
    })
  },

  closeMarkSheet: function () {
    this.setData({ markSheetVisible: false })
  },

  onPickMark: function (e) {
    const markId = e.currentTarget.dataset.id
    const target = this.data.markTarget
    if (!target || !this._notes) return

    this._notes.marks[target.userId] = markId
    this.saveNotes()
    this.setData({ markSheetVisible: false, markTarget: null })
    this.render(this.data.room)
  },

  onClearMark: function () {
    const target = this.data.markTarget
    if (!target || !this._notes) return

    delete this._notes.marks[target.userId]
    this.saveNotes()
    this.setData({ markSheetVisible: false, markTarget: null })
    this.render(this.data.room)
  },

  /* ============================ 轮询同步 ============================ */

  startPolling: function () {
    this.stopPolling()
    const that = this
    this._timer = setInterval(function () {
      that.refresh(true)
    }, CONFIG.roomPollInterval)
  },

  stopPolling: function () {
    if (this._timer) {
      clearInterval(this._timer)
      this._timer = null
    }
  },

  /**
   * 拉一次最新房间状态。
   * silent 用于轮询：出错时不要打断界面，只在房间消失时退出。
   * （后端接上 WebSocket 之后，这里可以退化成兜底刷新。）
   */
  refresh: function (silent) {
    const that = this
    if (!this.data.roomId || this._gone) return Promise.resolve()

    return api
      .getRoom({ roomId: this.data.roomId })
      .then(function (room) {
        if (!room.me) {
          that.handleGone('你已经被移出房间')
          return
        }
        that.render(room)
      })
      .catch(function (err) {
        if (silent) {
          if (isRoomGone(err)) that.handleGone('房间已解散')
          return
        }
        that.setData({ failed: (err && err.message) || '刷新房间失败' })
      })
  },

  handleGone: function (message) {
    if (this._gone) return
    this._gone = true
    this.stopPolling()
    voice.leave()
    util.toast(message || '房间已结束')
    const that = this
    setTimeout(function () {
      that.goBack()
    }, 1200)
  },

  /* ============================ 座位 ============================ */

  onSeatTap: function (e) {
    const detail = e.detail || {}
    const member = detail.member

    if (!member) {
      util.toast('这是个空位，点「邀请」叫人来')
      return
    }
    if (member.isSelf) return
    if (!this.data.isOwner) {
      util.toast(member.nickname + (member.ready ? '（已准备）' : '（未准备）'))
      return
    }

    const that = this
    util.confirm('把「' + member.nickname + '」移出房间？', '移出玩家').then(function (ok) {
      if (!ok) return
      api
        .kickMember({ roomId: that.data.roomId, userId: member.userId })
        .then(function (room) {
          that.render(room)
          util.toast('已移出')
        })
        .catch(util.toastError)
    })
  },

  /* ============================ 谁是卧底 ============================ */

  /** 房主改卧底人数。上限是「必须小于房间人数的一半」 */
  onTapUndercoverCount: function () {
    if (!this.data.isOwner) {
      util.toast('只有房主可以设置卧底人数')
      return
    }

    const room = this.data.room
    if (!room) return

    const cap = this.data.maxUndercover
    if (cap < 1) {
      util.toast('至少要 4 个人才能玩谁是卧底')
      return
    }

    const that = this
    const items = []
    for (let i = 1; i <= cap; i++) {
      items.push(i + ' 个卧底')
    }

    wx.showActionSheet({
      itemList: items,
      success: function (res) {
        const count = res.tapIndex + 1
        if (count === room.undercoverCount) return

        util.loading('设置中')
        api
          .setupUndercover({ roomId: room.id, undercoverCount: count })
          .then(function (next) {
            util.hideLoading()
            that.render(next)
            util.toast('卧底人数已设为 ' + count + ' 人')
          })
          .catch(function (err) {
            util.hideLoading()
            util.toastError(err)
          })
      },
      fail: function () {}
    })
  },

  /** 游戏面板里操作完（投票 / 开下一轮）后，把最新房间拿回来渲染 */
  onGameChanged: function (e) {
    const room = e.detail && e.detail.room
    if (room) {
      this.render(room)
      return
    }
    // 组件只喊了一声「状态变了」但没带房间（比如你画我猜的轮询发现换轮/结算了），
    // 就静默拉一次完整状态 —— 结算内容、总分这些只在 gameState 里
    this.refresh(true)
  },

  /* ============================ 主操作 ============================ */

  onMainAction: function () {
    if (this.data.mainBtnDisabled) return
    const room = this.data.room
    if (!room) return

    if (room.status === 'playing') {
      if (this.data.isOwner) this.onEndGame()
      return
    }
    if (this.data.isOwner) this.onStart()
    else this.toggleReady()
  },

  toggleReady: function () {
    const that = this
    api
      .setReady({ roomId: this.data.roomId, ready: !this.data.myReady })
      .then(function (room) {
        that.render(room)
      })
      .catch(util.toastError)
  },

  onStart: function () {
    const that = this
    api
      .startGame({ roomId: this.data.roomId })
      .then(function (room) {
        that.render(room)
        if (room.gameId === GAME_UNDERCOVER) {
          util.toast('开局！看看自己的词，别被旁边的人瞄到')
        } else if (room.gameId === GAME_WEREWOLF) {
          util.toast('开局！先看清楚自己的身份')
        } else if (room.gameId === GAME_DRAW) {
          util.toast('开局！等画手挑词')
        } else {
          util.toast('开局！这个游戏的玩法还在开发中')
        }
      })
      .catch(util.toastError)
  },

  onEndGame: function () {
    const that = this
    util.confirm('结束本局，回到准备状态？').then(function (ok) {
      if (!ok) return
      api
        .endGame({ roomId: that.data.roomId })
        .then(function (room) {
          that.render(room)
        })
        .catch(util.toastError)
    })
  },

  /* ============================ 切换游戏类型 ============================ */

  onHeadGameTap: function () {
    if (!this.data.isOwner) {
      util.toast('只有房主可以切换游戏类型')
      return
    }
    this.openGamePicker()
  },

  openGamePicker: function () {
    this.loadGames()
    this.setData({ gamePickerVisible: true })
  },

  closeGamePicker: function () {
    this.setData({ gamePickerVisible: false })
  },

  onPickGame: function (e) {
    const gameId = e.currentTarget.dataset.id
    const room = this.data.room
    if (!gameId || !room) return
    if (gameId === room.gameId) {
      this.closeGamePicker()
      return
    }

    // 有版型的游戏（狼人杀）切过去时带上默认版型 —— 座位数由版型决定
    const gameDef = gameData.getGame(gameId)
    const variant = gameData.getVariant(gameDef, '')

    const that = this
    util.loading('切换中')
    api
      .updateRoom({
        roomId: this.data.roomId,
        gameId: gameId,
        variantId: variant ? variant.id : ''
      })
      .then(function (next) {
        util.hideLoading()
        that.closeGamePicker()
        that.render(next)
        util.toast('已切换到「' + (next.gameName || gameId) + '」')
      })
      .catch(function (err) {
        util.hideLoading()
        util.toastError(err)
      })
  },

  /* ============================ 语音 ============================ */

  onVoiceState: function (state) {
    this.setData({ voice: state })
    this.syncSpeaking(state)
  },

  /** 把「谁在说话」映射到对应座位的光圈上 */
  syncSpeaking: function (state) {
    const slots = this.data.slots
    if (!slots || !slots.length) return

    const speakers = (state && state.speakerOpenids) || []
    let changed = false
    const next = slots.map(function (s) {
      const speaking = !!(s.member && s.member.openid && speakers.indexOf(s.member.openid) > -1)
      if (!!s.speaking === speaking) return s
      changed = true
      return Object.assign({}, s, { speaking: speaking })
    })

    if (changed) this.setData({ slots: next })
  },

  onVoiceTap: function () {
    // 已经连上就断开
    if (voice.isJoined()) {
      voice.leave()
      return
    }

    const room = this.data.room
    if (!room) return

    // wx.joinVoIPChat 单房间上限 10 人，超过就降级提示
    if (room.memberCount > CONFIG.voiceMaxPlayers) {
      wx.showModal({
        title: '语音开不了',
        content:
          '微信实时语音单个房间最多 ' +
          CONFIG.voiceMaxPlayers +
          ' 人，当前房间有 ' +
          room.memberCount +
          ' 人。可以先用微信群语音通话，小程序这边照样能玩。',
        showCancel: false,
        confirmText: '知道了',
        confirmColor: '#7c5cff'
      })
      return
    }

    const that = this
    voice
      .join(room)
      .then(function () {
        util.toast('语音已连接', 'success')
      })
      .catch(function (err) {
        if (err && err.needRecordSetting) {
          util.confirm('需要麦克风权限才能语音，去设置里打开？', '麦克风权限').then(function (ok) {
            if (ok) voice.openRecordSetting()
          })
          return
        }
        wx.showModal({
          title: '语音连接失败',
          content: (err && err.message) || '未知错误',
          showCancel: false,
          confirmText: '知道了',
          confirmColor: '#7c5cff'
        })
      })
  },

  /* ============================ 更多 / 退出 ============================ */

  onMore: function () {
    const isOwner = this.data.isOwner
    const items = isOwner
      ? ['切换游戏类型', '复制房间号', '加人机', '移出玩家', '解散房间']
      : ['复制房间号', '离开房间']

    const that = this
    wx.showActionSheet({
      itemList: items,
      success: function (res) {
        const label = items[res.tapIndex]
        if (label === '切换游戏类型') that.openGamePicker()
        else if (label === '复制房间号') that.copyCode()
        else if (label === '加人机') that.openBotPicker()
        else if (label === '移出玩家') that.openKickPicker()
        else if (label === '解散房间') that.confirmLeave(true)
        else if (label === '离开房间') that.confirmLeave(false)
      },
      fail: function () {}
    })
  },

  /**
   * 加减人机。
   *
   * 一个人 + 一台手机也能把 6 人狼人杀、4 人卧底跑起来 ——
   * 人机由后端自动驱动，轮到自己会行动（会选词、会画、会投票）。
   */
  openBotPicker: function () {
    const room = this.data.room
    if (!room) return

    if (room.status === 'playing') {
      util.toast('游戏进行中不能改人机，先结束本局')
      return
    }

    const members = room.members || []
    const botCount = members.filter(function (m) {
      return m.isBot
    }).length
    const humanCount = members.length - botCount
    const maxBots = Math.max(0, room.maxPlayers - humanCount)

    if (maxBots <= 0) {
      util.toast('座位已经坐满了')
      return
    }

    const items = [
      '不要人机',
      '加 1 个（现在 ' + botCount + ' 个）',
      '加 3 个',
      '补满座位（最多 ' + maxBots + ' 个）'
    ]
    const targets = [0, botCount + 1, botCount + 3, maxBots]

    const that = this
    wx.showActionSheet({
      itemList: items,
      success: function (res) {
        const want = Math.max(0, Math.min(maxBots, targets[res.tapIndex]))
        that.applyBots(want)
      },
      fail: function () {}
    })
  },

  applyBots: function (count) {
    const that = this
    if (!this.data.roomId) return

    util.loading('调整人机')
    api
      .setBots({ roomId: this.data.roomId, count: count })
      .then(function (room) {
        util.hideLoading()
        that.render(room)
        util.toast(count > 0 ? ('房间里现在有 ' + count + ' 个人机') : '人机已清空')
      })
      .catch(function (err) {
        util.hideLoading()
        util.toastError(err)
      })
  },

  openKickPicker: function () {
    const room = this.data.room
    if (!room) return
    const others = room.members.filter(function (m) {
      return !m.isSelf
    })
    if (!others.length) {
      util.toast('房间里还没有别的玩家')
      return
    }

    const that = this
    const shown = others.slice(0, 6)
    wx.showActionSheet({
      // showActionSheet 的 itemList 最多 6 项
      itemList: shown.map(function (m) {
        return m.nickname
      }),
      success: function (res) {
        const target = shown[res.tapIndex]
        if (!target) return
        api
          .kickMember({ roomId: that.data.roomId, userId: target.userId })
          .then(function (next) {
            that.render(next)
            util.toast('已移出')
          })
          .catch(util.toastError)
      },
      fail: function () {}
    })
  },

  copyCode: function () {
    const code = this.data.room && this.data.room.code
    if (!code) return
    wx.setClipboardData({
      data: code,
      success: function () {
        util.toast('房间号已复制：' + code)
      }
    })
  },

  confirmLeave: function (isDissolve) {
    const that = this
    util
      .confirm(isDissolve ? '解散后所有人都要退出，确定吗？' : '确定离开房间吗？', isDissolve ? '解散房间' : '离开房间')
      .then(function (ok) {
        if (!ok) return
        api
          .leaveRoom({ roomId: that.data.roomId })
          .then(function () {
            voice.leave()
            that.stopPolling()
            util.toast(isDissolve ? '房间已解散' : '已离开房间')
            setTimeout(function () {
              that.goBack()
            }, 700)
          })
          .catch(util.toastError)
      })
  },

  goBack: function () {
    const pages = getCurrentPages()
    if (pages.length > 1) {
      wx.navigateBack({ delta: 1 })
    } else {
      wx.reLaunch({ url: '/pages/index/index' })
    }
  },

  /* ============================ 分享 ============================ */

  onShareAppMessage: function () {
    const room = this.data.room
    if (!room) return { title: '一起来玩', path: '/pages/index/index' }
    return {
      title: '来「' + room.title + '」一起玩「' + room.gameName + '」，房间号 ' + room.code,
      path: '/pages/room/room?roomId=' + room.id + '&code=' + room.code
    }
  }
})
