// pages/rule-detail/rule-detail.js
const api = require('../../utils/api')
const util = require('../../utils/util')

Page({
  data: {
    gameId: '',
    game: null,
    loading: true
  },

  onLoad: function (options) {
    const gameId = (options && options.gameId) || ''
    const that = this
    this.setData({ gameId: gameId })

    api
      .listGames()
      .then(function (list) {
        const game = list.filter(function (g) {
          return g.id === gameId
        })[0]

        if (!game) {
          that.setData({ loading: false })
          util.toast('没找到这个游戏')
          setTimeout(function () {
            that.goBack()
          }, 900)
          return
        }

        // 兜底：规则数据缺字段时不要让 WXML 报错
        that.setData({
          loading: false,
          game: {
            id: game.id,
            name: game.name,
            emoji: game.emoji,
            color: game.color,
            tagline: game.tagline,
            intro: game.intro,
            peopleText: game.peopleText,
            duration: game.duration,
            tags: game.tags || [],
            roles: game.roles || [],
            sections: game.sections || [],
            tips: game.tips || [],
            // 版型（狼人杀用）：字段逐个兜底，缺字段时 WXML 不会炸
            variants: (game.variants || []).map(function (v) {
              return {
                id: v.id,
                name: v.name,
                seats: v.seats,
                intro: v.intro || '',
                points: v.points || [],
                nightOrder: v.nightOrder || '',
                notes: v.notes || [],
                roles: v.roles || []
              }
            })
          }
        })
      })
      .catch(function (err) {
        that.setData({ loading: false })
        util.toastError(err)
      })
  },

  /** 带着这个游戏去创建房间 */
  createRoom: function () {
    if (!this.data.gameId) return
    wx.navigateTo({ url: '/pages/create/create?gameId=' + this.data.gameId })
  },

  goBack: function () {
    const pages = getCurrentPages()
    if (pages.length > 1) {
      wx.navigateBack({ delta: 1 })
    } else {
      wx.reLaunch({ url: '/pages/index/index' })
    }
  },

  onShareAppMessage: function () {
    const game = this.data.game
    if (!game) return { title: '游戏规则', path: '/pages/rules/rules' }
    return {
      title: game.name + ' 怎么玩？',
      path: '/pages/rule-detail/rule-detail?gameId=' + game.id
    }
  }
})
