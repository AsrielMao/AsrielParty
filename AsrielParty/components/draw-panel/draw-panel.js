/**
 * 你画我猜 · 游戏面板
 *
 * 房间进入 playing 后替换掉座位区，变成一块能画的板子。
 * 规则判定全在后端（party/draw_guess.py），这里只负责「画」和「猜」。
 *
 * ## 畫板是怎么同步的
 *
 * 没用 WebSocket，走的是**增量轮询**：
 *
 * - 画手：手指一动就**在本地立刻画**（自己看着是跟手的），
 *   攒 0.6 秒左右把这几笔批量 POST 上去；
 * - 其他人：每 0.7 秒拉一次「上次拿到的序号之后新增的笔画」，往画布上追加。
 *
 * 所以别人看到的画面大约滞后 1 秒 —— 对「看完再猜」完全够用，而且不用给后端
 * 加 Channels / Redis 那一整套东西。
 *
 * 坐标一律用**归一化值**（0~1），这样谁的屏幕大谁的屏幕小，看到的都是同一张画。
 *
 * ## 换轮和擦除
 *
 * 服务端换轮时会把笔画清空、序号归零，客户端靠返回里的 `round` 发现该清屏了。
 * 「擦掉重画」则是发一条 **points 为空的笔画**当清屏信号 —— 收到就清画布。
 */

const api = require('../../utils/api')
const util = require('../../utils/util')

/** 画板轮询间隔（毫秒）。实时性全靠它，调大会明显变卡 */
const POLL_INTERVAL = 700
/** 画手把攒下的笔画提交上去的间隔 */
const FLUSH_INTERVAL = 600
/** 采样：手指至少移动这么多（归一化距离）才记一个点，不然一条线几百个点 */
const MIN_DIST = 0.005
/** 一次最多提交多少笔（后端也有同样的上限） */
const MAX_BATCH = 40
/** 画板底色（也是清屏时填的颜色） */
const BOARD_BG = '#1a1630'

const COLORS = ['#f3f0ff', '#ff5f6d', '#ffc861', '#35d6a4', '#7c5cff']
const WIDTHS = [4, 8, 14]

const PHASE_TITLE = {
  picking: '选词',
  drawing: '作画中',
  result: '本轮结束',
  over: '游戏结束'
}

/** 给流水列表补一个稳定的 key（小程序 wx:key 需要，而且不要动原对象） */
function withKeys(list) {
  return (list || []).map(function (item, index) {
    const row = {}
    Object.keys(item || {}).forEach(function (k) {
      row[k] = item[k]
    })
    row.key = index
    return row
  })
}

/** 最高分 —— 结束页用它给冠军那一行加高亮 */
function topScoreOf(scores) {
  let top = 0
  ;(scores || []).forEach(function (row) {
    if (row.score > top) top = row.score
  })
  return top
}

/** 把 gameState 转成这份组件自己用的快照 */
function snapshotOf(gs) {
  const copy = {}
  Object.keys(gs).forEach(function (k) {
    copy[k] = gs[k]
  })
  copy.guesses = withKeys(gs.guesses)
  return copy
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
    }
  },

  data: {
    /** 对局状态（来自 room.gameState，另外会被轮询结果局部更新） */
    gs: null,
    phaseTitle: '',
    timerText: '',
    /** 猜词输入框的内容 */
    guessText: '',
    /** 最高分（结束页高亮冠军用） */
    topScore: 0,
    /** 猜测流水最后一行的 id（让它自动滚到底） */
    lastGuessId: '',
    /** 画笔 */
    penColor: COLORS[0],
    penWidth: WIDTHS[1],
    colors: COLORS,
    widths: WIDTHS
  },

  observers: {
    'room.gameState': function (gs) {
      this.syncFrom(gs)
    }
  },

  lifetimes: {
    attached: function () {
      this._ctx = null
      this._canvasPromise = null
      this._w = 0
      this._h = 0
      // canvas 在页面里的位置（换算触摸坐标用）
      this._left = 0
      this._top = 0
      this._cur = null
      this._prev = null
      this._pending = []
      this._sinceSeq = 0
      this._round = null
      this._polling = false
      this._pollFailures = 0
      this._pollTimer = null
      this._flushTimer = null
    },
    detached: function () {
      this.flush()          // 走之前把没提交的笔画交上去
      this.stopTimers()
    }
  },

  /**
   * 页面被隐藏 / 重新显示。
   *
   * **这一条必须要有**：组件自己的定时器不会跟着页面的 `onHide` 停 ——
   * 少了它，退出房间之后轮询还在跑，日志就会一直刷 400。
   */
  pageLifetimes: {
    hide: function () {
      this.flush()
      this.stopTimers()
    },
    show: function () {
      const gs = this.data.gs
      const room = this.data.room
      const inGame = !!room && room.status === 'playing'
      if (inGame && gs && gs.phase !== 'over' && !this._pollTimer) this.startPoll()
      // 回来时布局可能已经变了，重新量一次
      this.syncCanvasSize()
    }
  },

  methods: {
    /* ============================ 状态同步 ============================ */

    syncFrom: function (gs) {
      if (!gs) {
        this.stopTimers()
        this.setData({ gs: null, phaseTitle: '', timerText: '', topScore: 0, lastGuessId: '' })
        return
      }

      const snapshot = snapshotOf(gs)

      this.setData({
        gs: snapshot,
        phaseTitle: PHASE_TITLE[gs.phase] || gs.phase,
        timerText: gs.remainSeconds > 0 ? util.formatDuration(gs.remainSeconds) : '',
        topScore: topScoreOf(gs.scores),
        lastGuessId: snapshot.guesses.length ? 'guess-' + (snapshot.guesses.length - 1) : ''
      })

      // 换了一轮（或者刚进来）：画布要重来
      if (gs.round !== this._round) {
        this._round = gs.round
        this._sinceSeq = 0
        this.clearCanvas()
      }

      // 只在「房间确实还在游戏中」时才轮询
      const room = this.data.room
      const inGame = !!room && room.status === 'playing'
      if (inGame && gs.phase !== 'over' && !this._pollTimer) this.startPoll()

      // 布局可能刚变过（切页面回来、内容变多…）：重新量一次画布。
      // 晚一两秒校准没关系，总比一直画歪强。
      this.syncCanvasSize()
    },

    /* ============================ 画板 ============================ */

    /**
     * 量一次画布。
     *
     * 尺寸**每次都重新量**：容器宽度一变，canvas 的 CSS 尺寸就跟着变，
     * 而「手指位置 → 归一化坐标」的基准也跟着变 —— 不重新量就会画歪
     * （表现就是笔迹和手指错位）。
     *
     * 但只有尺寸**真的变了**才重设 `canvas.width` —— 那一下会清空画布。
     */
    ensureCanvas: function (force) {
      if (this._ctx && !force) return Promise.resolve(this._ctx)
      if (this._canvasPromise) return this._canvasPromise

      const that = this
      this._canvasPromise = new Promise(function (resolve) {
        wx.createSelectorQuery()
          .in(that)
          .select('#board')
          .fields({ node: true, size: true, rect: true })
          .exec(function (res) {
            that._canvasPromise = null

            const item = res && res[0]
            if (!item || !item.node || !item.width || !item.height) {
              resolve(that._ctx || null)
              return
            }

            const canvas = item.node

            // 位置每次都更新：布局变了它就变
            that._left = item.left || 0
            that._top = item.top || 0

            const resized = !that._ctx || that._w !== item.width || that._h !== item.height
            if (resized) {
              const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
              const dpr = (info && info.pixelRatio) || 1
              const ctx = canvas.getContext('2d')

              canvas.width = item.width * dpr
              canvas.height = item.height * dpr
              ctx.scale(dpr, dpr)

              that._canvas = canvas
              that._ctx = ctx
              that._w = item.width
              that._h = item.height
            }

            resolve(that._ctx)
          })
      })

      return this._canvasPromise
    },

    /**
     * 容器尺寸可能变了：重新量一次，真的变了就把画布重置并从头重画。
     *
     * 由 `syncFrom`（房间每 3 秒轮询一次）和页面重新显示的时候调用 ——
     * 晚一两秒没关系，总比一直画歪好。
     */
    syncCanvasSize: function () {
      const oldW = this._w
      const oldH = this._h
      const that = this

      return this.ensureCanvas(true).then(function (ctx) {
        if (!ctx) return
        if (that._w === oldW && that._h === oldH) return

        // 尺寸变了，画布内容已经被重置：从头拉一遍笔画重画
        that._sinceSeq = 0
        that.clearCanvas().then(function () {
          that.pollOnce()
        })
      })
    },

    /** 清空画布（并填回底色） */
    clearCanvas: function () {
      const that = this
      return this.ensureCanvas().then(function (ctx) {
        if (!ctx) return
        ctx.clearRect(0, 0, that._w, that._h)
        ctx.fillStyle = BOARD_BG
        ctx.fillRect(0, 0, that._w, that._h)
      })
    },

    /** 把一条笔画画到画布上 */
    paintStroke: function (ctx, stroke) {
      const pts = stroke.points || []
      if (!pts.length) return
      if (pts.length < 2) return

      ctx.strokeStyle = stroke.color || COLORS[0]
      ctx.lineWidth = stroke.width || WIDTHS[1]
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.beginPath()
      ctx.moveTo(pts[0][0] * this._w, pts[0][1] * this._h)
      for (let i = 1; i < pts.length; i++) {
        ctx.lineTo(pts[i][0] * this._w, pts[i][1] * this._h)
      }
      ctx.stroke()
    },

    /**
     * 画一批笔画。`points` 为空数组的那条是**清屏信号** ——
     * 「擦掉重画」就是靠它同步给其他人的。
     */
    drawStrokes: function (strokes) {
      const that = this
      return this.ensureCanvas().then(function (ctx) {
        if (!ctx) return
        strokes.forEach(function (s) {
          if (!s.points || !s.points.length) {
            ctx.clearRect(0, 0, that._w, that._h)
            ctx.fillStyle = BOARD_BG
            ctx.fillRect(0, 0, that._w, that._h)
            return
          }
          that.paintStroke(ctx, s)
        })
      })
    },

    /* ============================ 画手作画 ============================ */

    onTouchStart: function (e) {
      const gs = this.data.gs
      if (!gs || !gs.canDraw) return

      // 画布还没量出来（布局刚变过）：先补量一次，这一笔就先算了
      if (!this._w || !this._h) {
        this.ensureCanvas(true)
        return
      }

      const p = this.toNorm(e.touches && e.touches[0])
      if (!p) return

      this._cur = { points: [p], color: this.data.penColor, width: this.data.penWidth }
      this._prev = p
    },

    onTouchMove: function (e) {
      const cur = this._cur
      if (!cur) return

      const p = this.toNorm(e.touches && e.touches[0])
      if (!p) return
      if (this._prev && this.dist(p, this._prev) < MIN_DIST) return

      cur.points.push(p)
      this._prev = p

      // 本地立刻画这一小段，自己看着是跟手的
      const that = this
      this.ensureCanvas().then(function (ctx) {
        if (!ctx) return
        const pts = cur.points
        const n = pts.length
        ctx.strokeStyle = cur.color
        ctx.lineWidth = cur.width
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        ctx.beginPath()
        ctx.moveTo(pts[n - 2][0] * that._w, pts[n - 2][1] * that._h)
        ctx.lineTo(pts[n - 1][0] * that._w, pts[n - 1][1] * that._h)
        ctx.stroke()
      })
    },

    onTouchEnd: function () {
      const cur = this._cur
      this._cur = null
      this._prev = null
      if (!cur || !cur.points.length) return

      // 只点了一下没有拖动：补一个极小的位移，让它成为一个「点」
      if (cur.points.length < 2) {
        const p = cur.points[0]
        cur.points.push([Math.min(1, p[0] + 0.004), p[1]])

        const that = this
        this.ensureCanvas().then(function (ctx) {
          if (!ctx) return
          ctx.fillStyle = cur.color
          ctx.beginPath()
          ctx.arc(p[0] * that._w, p[1] * that._h, cur.width / 2, 0, Math.PI * 2)
          ctx.fill()
        })
      }

      this._pending.push(cur)
      this.scheduleFlush()
    },

    /**
     * 手指位置 → 0~1 的归一化坐标。
     *
     * 用 pageX/pageY 减去 canvas 自己在页面里的位置（`rect: true` 量的），
     * 这样不管画板被放在哪、容器多宽多窄，算出来的都是画布内的相对位置。
     */
    toNorm: function (touch) {
      if (!touch || !this._w || !this._h) return null

      const px = typeof touch.pageX === 'number' ? touch.pageX : touch.clientX
      const py = typeof touch.pageY === 'number' ? touch.pageY : touch.clientY
      if (typeof px !== 'number' || typeof py !== 'number') return null

      const x = Math.min(1, Math.max(0, (px - (this._left || 0)) / this._w))
      const y = Math.min(1, Math.max(0, (py - (this._top || 0)) / this._h))
      return [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]
    },

    dist: function (a, b) {
      const dx = a[0] - b[0]
      const dy = a[1] - b[1]
      return Math.sqrt(dx * dx + dy * dy)
    },

    /** 攒一小会儿再提交，一次请求带上好几笔 */
    scheduleFlush: function () {
      if (this._flushTimer) return
      const that = this
      this._flushTimer = setTimeout(function () {
        that._flushTimer = null
        that.flush()
      }, FLUSH_INTERVAL)
    },

    flush: function () {
      const room = this.data.room
      if (!room || !this._pending.length) return

      const batch = this._pending.splice(0, MAX_BATCH)
      // 提交失败就算了，反正用户还在画，画面本来就是「尽力而为」
      api.drawStrokes({ roomId: room.id, strokes: batch }).catch(function () {})
    },

    /** 画手擦掉重画 */
    onClear: function () {
      const room = this.data.room
      const gs = this.data.gs
      if (!room || !gs || !gs.canDraw) return

      const that = this
      util.confirm('擦掉重画？', '清空画布').then(function (ok) {
        if (!ok) return
        that._pending = []
        api
          .drawClear({ roomId: room.id })
          .then(function (res) {
            that._sinceSeq = (res && res.strokeSeq) || 0
            that.clearCanvas()
          })
          .catch(util.toastError)
      })
    },

    onPickColor: function (e) {
      this.setData({ penColor: e.currentTarget.dataset.color })
    },

    onPickWidth: function (e) {
      this.setData({ penWidth: Number(e.currentTarget.dataset.width) })
    },

    /* ============================ 轮询 ============================ */

    startPoll: function () {
      this.stopPoll()
      const that = this
      this._pollTimer = setInterval(function () {
        that.pollOnce()
      }, POLL_INTERVAL)
      this.pollOnce()
    },

    stopPoll: function () {
      if (this._pollTimer) {
        clearInterval(this._pollTimer)
        this._pollTimer = null
      }
    },

    stopTimers: function () {
      this.stopPoll()
      if (this._flushTimer) {
        clearTimeout(this._flushTimer)
        this._flushTimer = null
      }
    },

    pollOnce: function () {
      const room = this.data.room
      const gs = this.data.gs
      if (!room || !gs || this._polling) return

      // 房间已经不在「游戏中」了（对局被结束、或者状态被改过）：
      // 后端只会一直回 400，别再问了。
      if (room.status && room.status !== 'playing') {
        this.stopPoll()
        return
      }

      if (gs.phase === 'over') {
        // 整局打完了，没什么可轮询的了
        this.stopPoll()
        return
      }

      this._polling = true
      const that = this

      api
        .drawPoll({ roomId: room.id, sinceSeq: this._sinceSeq || 0 })
        .then(function (res) {
          that._pollFailures = 0
          that.applyPoll(res)
        })
        .catch(function (err) {
          that.onPollFailed(err)
        })
        .then(function () {
          that._polling = false
        })
    },

    /**
     * 轮询失败。
     *
     * 「房间没了 / 这一局已经作废」这种**要直接停掉轮询** —— 一直问下去没有意义，
     * 而且退出房间后如果定时器还在跑，日志就会一直刷 400。
     */
    onPollFailed: function (err) {
      const code = err && err.statusCode
      if (code === 400 || code === 404) {
        this.stopPoll()
        // 顺手让页面去拉一次完整状态：多半已经不在这个房间了
        this.triggerEvent('changed', {})
        return
      }

      this._pollFailures = (this._pollFailures || 0) + 1
      // 网络一直不通也别无限重试，先停了；下次进页面会自己恢复
      if (this._pollFailures >= 5) this.stopPoll()
    },

    applyPoll: function (res) {
      if (!res || !this.data.gs) return

      // 换轮了：清画布、序号归零
      if (res.round !== this._round) {
        this._round = res.round
        this._sinceSeq = 0
        this.clearCanvas()
      }

      if (res.strokes && res.strokes.length) {
        this.drawStrokes(res.strokes)
      }
      this._sinceSeq = res.seq || 0

      const phaseChanged = this.data.gs.phase !== res.phase
      const guesses = withKeys(res.guesses)

      this.setData({
        'gs.phase': res.phase,
        'gs.remainSeconds': res.remainSeconds,
        'gs.solvedUserIds': res.solvedUserIds,
        'gs.solvedCount': (res.solvedUserIds || []).length,
        'gs.guesses': guesses,
        'gs.mySolved': res.mySolved,
        'gs.myWord': res.myWord,
        'gs.canPick': res.canPick,
        'gs.canDraw': res.canDraw,
        'gs.canGuess': res.canGuess,
        'gs.canAdvance': res.canAdvance,
        phaseTitle: PHASE_TITLE[res.phase] || res.phase,
        timerText: res.remainSeconds > 0 ? util.formatDuration(res.remainSeconds) : '',
        lastGuessId: guesses.length ? 'guess-' + (guesses.length - 1) : ''
      })

      // 阶段变了（比如时间到自动结算、下一轮开始）说明完整状态也变了，
      // 让页面去拉一次房间 —— 结算内容、总分这些只在 gameState 里
      if (phaseChanged) {
        this.triggerEvent('changed', {})
      }
    },

    /* ============================ 交互 ============================ */

    onPickWord: function (e) {
      const room = this.data.room
      if (!room) return

      const index = Number(e.currentTarget.dataset.index)
      const that = this

      util.loading('选好了')
      api
        .drawPick({ roomId: room.id, wordIndex: index })
        .then(function (next) {
          util.hideLoading()
          that.triggerEvent('changed', { room: next })
        })
        .catch(function (err) {
          util.hideLoading()
          util.toastError(err)
        })
    },

    onGuessInput: function (e) {
      this.setData({ guessText: e.detail.value })
    },

    onGuessSubmit: function () {
      const room = this.data.room
      const gs = this.data.gs
      if (!room || !gs || !gs.canGuess) return

      const text = (this.data.guessText || '').trim()
      if (!text) return

      this.setData({ guessText: '' })
      const that = this

      api
        .drawGuess({ roomId: room.id, text: text })
        .then(function (next) {
          that.triggerEvent('changed', { room: next })
        })
        .catch(util.toastError)
    },

    onAdvance: function () {
      const room = this.data.room
      if (!room) return

      const that = this
      api
        .drawAdvance({ roomId: room.id })
        .then(function (next) {
          that.triggerEvent('changed', { room: next })
        })
        .catch(util.toastError)
    }
  }
})
