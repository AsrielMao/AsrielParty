// pages/rules/rules.js
const api = require('../../utils/api')
const util = require('../../utils/util')

Page({
  data: {
    games: [],
    loading: true
  },

  onLoad: function () {
    const that = this
    api
      .listGames()
      .then(function (list) {
        that.setData({ games: list, loading: false })
      })
      .catch(function (err) {
        that.setData({ loading: false })
        util.toastError(err)
      })
  },

  openRule: function (e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/rule-detail/rule-detail?gameId=' + id })
  }
})
