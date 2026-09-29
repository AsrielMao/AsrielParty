// pages/create/create.js
const api = require('../../utils/api')
const util = require('../../utils/util')
const gameData = require('../../utils/games')
const CONFIG = require('../../config/index')

/** 这个游戏的版型列表（没有版型的游戏返回空数组） */
function variantsOf(game) {
  return (game && game.variants) || []
}

Page({
  data: {
    title: '',
    games: [],
    gameId: '',

    /** 当前游戏的版型列表（目前只有狼人杀有） */
    variants: [],
    /** 选中的版型 id */
    variantId: '',
    /** 选中的版型详情，用来展示角色配置 */
    variant: null,

    /** 房间座位数：有版型时跟版型走，否则用默认值 */
    maxPlayers: CONFIG.defaultSeats,

    loadingGames: true,
    creating: false
  },

  onLoad: function (options) {
    // 从规则详情页跳过来时会带上想玩的游戏
    const preset = (options && options.gameId) || ''
    const that = this

    api
      .listGames()
      .then(function (list) {
        const hasPreset =
          preset &&
          list.some(function (g) {
            return g.id === preset
          })

        const gameId = hasPreset ? preset : list.length ? list[0].id : ''
        const patch = that.patchForGame(list, gameId)
        patch.games = list
        patch.loadingGames = false
        that.setData(patch)
      })
      .catch(function (err) {
        that.setData({ loadingGames: false })
        util.toastError(err)
      })
  },

  /**
   * 换游戏时要一并同步的字段：版型列表、选中的版型、座位数。
   * 有版型的游戏（狼人杀）座位数由版型决定，其余游戏用默认座位数。
   */
  patchForGame: function (list, gameId) {
    const game =
      (list || []).filter(function (g) {
        return g.id === gameId
      })[0] || gameData.getGame(gameId)

    const variants = variantsOf(game)
    const variant = variants.length ? variants[0] : null

    return {
      gameId: gameId,
      variants: variants,
      variantId: variant ? variant.id : '',
      variant: variant,
      maxPlayers: variant ? variant.seats : CONFIG.defaultSeats
    }
  },

  onTitleInput: function (e) {
    this.setData({ title: e.detail.value })
  },

  onPickGame: function (e) {
    const gameId = e.currentTarget.dataset.id
    if (!gameId || gameId === this.data.gameId) return
    this.setData(this.patchForGame(this.data.games, gameId))
  },

  onPickVariant: function (e) {
    const variantId = e.currentTarget.dataset.id
    const variant =
      this.data.variants.filter(function (v) {
        return v.id === variantId
      })[0] || null

    if (!variant) return
    this.setData({
      variantId: variant.id,
      variant: variant,
      maxPlayers: variant.seats
    })
  },

  onCreate: function () {
    if (this.data.creating) return

    if (!this.data.gameId) {
      util.toast('先选一个游戏类型')
      return
    }

    const that = this
    this.setData({ creating: true })
    util.loading('创建中')

    api
      .createRoom({
        title: this.data.title,
        gameId: this.data.gameId,
        variantId: this.data.variantId,
        maxPlayers: this.data.maxPlayers
      })
      .then(function (room) {
        util.hideLoading()
        that.setData({ creating: false })
        // 用 redirectTo：创建完不该再退回到创建页
        wx.redirectTo({ url: '/pages/room/room?roomId=' + room.id })
      })
      .catch(function (err) {
        util.hideLoading()
        that.setData({ creating: false })
        util.toastError(err)
      })
  }
})
