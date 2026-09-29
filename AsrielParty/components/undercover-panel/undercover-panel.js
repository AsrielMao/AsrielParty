/**
 * 谁是卧底 · 游戏面板
 *
 * 房间进入 playing 后用它替换座位环。它只负责「显示 + 操作」：
 *   - 看自己的词（默认藏着，点一下翻出来）
 *   - 看轮次 / 场上人数 / 投票进度
 *   - 点人投票（投完自动结算）
 *   - 结算后显示谁出局、什么身份、票型
 *   - 房主开下一轮
 *
 * 所有改动都通过 triggerEvent('changed', { room }) 把最新房间丢回页面，
 * 由页面统一渲染，避免两处各存一份状态。
 */

const api = require('../../utils/api')
const util = require('../../utils/util')

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
    /** 自己的词是否翻开。默认藏着，免得被旁边的人看到 */
    wordVisible: false,
    busy: false
  },

  observers: {
    'room.gameState.round': function (round) {
      // 进新的一轮就把词重新藏好
      if (round) this.setData({ wordVisible: false })
    }
  },

  methods: {
    /** 翻开 / 收起自己的词 */
    onToggleWord: function () {
      const gs = this.gs()
      if (!gs) return
      this.setData({ wordVisible: !this.data.wordVisible })
    },

    /** 点某个玩家 = 投票给他 */
    onTapPlayer: function (e) {
      const gs = this.gs()
      if (!gs) return

      if (gs.winner) {
        util.toast('这局已经结束了')
        return
      }
      if (gs.phase === 'result') {
        util.toast('本轮已经结算，等房主开下一轮')
        return
      }
      if (!gs.myAlive) {
        util.toast('你已经出局了，只能围观')
        return
      }

      const targetId = e.currentTarget.dataset.id
      const target = gs.players.filter(function (p) {
        return p.userId === targetId
      })[0]

      if (!target) return
      if (target.isMe) {
        util.toast('不能投自己')
        return
      }
      if (!target.alive) {
        util.toast('这个人已经出局了')
        return
      }

      const that = this
      util.confirm('确定投给「' + target.nickname + '」吗？', '投票').then(function (ok) {
        if (!ok) return
        that.doVote(target.userId)
      })
    },

    doVote: function (targetUserId) {
      const that = this
      const room = this.data.room
      if (!room) return

      this.setData({ busy: true })
      util.loading('投票中')

      api
        .castVote({ roomId: room.id, targetUserId: targetUserId })
        .then(function (next) {
          util.hideLoading()
          that.setData({ busy: false })
          that.triggerEvent('changed', { room: next })
        })
        .catch(function (err) {
          util.hideLoading()
          that.setData({ busy: false })
          util.toastError(err)
        })
    },

    /** 房主开下一轮 */
    onNextRound: function () {
      const that = this
      const room = this.data.room
      if (!room) return

      this.setData({ busy: true })
      util.loading('准备下一轮')

      api
        .nextRound({ roomId: room.id })
        .then(function (next) {
          util.hideLoading()
          that.setData({ busy: false, wordVisible: false })
          that.triggerEvent('changed', { room: next })
        })
        .catch(function (err) {
          util.hideLoading()
          that.setData({ busy: false })
          util.toastError(err)
        })
    },

    /** 取当前的游戏状态 */
    gs: function () {
      const room = this.data.room
      return room ? room.gameState : null
    }
  }
})
