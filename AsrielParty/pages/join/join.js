// pages/join/join.js
const api = require('../../utils/api')
const util = require('../../utils/util')
const roomView = require('../../utils/room-view')
const CONFIG = require('../../config/index')

Page({
  data: {
    code: '',
    codeLength: CONFIG.roomCodeLength,
    joining: false,
    rooms: [],
    loadingRooms: false
  },

  onLoad: function () {
    this.loadRooms()
  },

  onShow: function () {
    this.loadRooms()
  },

  onCodeInput: function (e) {
    // 房间号只可能是大写字母和数字，顺手清掉空格之类的干扰
    const raw = (e.detail.value || '').toUpperCase()
    this.setData({ code: raw.replace(/[^0-9A-Z]/g, '') })
  },

  onJoin: function () {
    if (this.data.joining) return

    const code = (this.data.code || '').trim().toUpperCase()
    if (code.length !== this.data.codeLength) {
      util.toast('请输入 ' + this.data.codeLength + ' 位房间号')
      return
    }

    const that = this
    this.setData({ joining: true })
    util.loading('加入中')

    api
      .joinRoom({ code: code })
      .then(function (room) {
        util.hideLoading()
        that.setData({ joining: false })
        wx.redirectTo({ url: '/pages/room/room?roomId=' + room.id })
      })
      .catch(function (err) {
        util.hideLoading()
        that.setData({ joining: false })
        util.toastError(err)
      })
  },

  enterRoom: function (e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/room/room?roomId=' + id })
  },

  loadRooms: function () {
    const that = this
    this.setData({ loadingRooms: true })
    return api
      .listMyRooms()
      .then(function (list) {
        that.setData({ rooms: list.map(roomView.toRoomItem) })
      })
      .catch(function (err) {
        console.warn('[join] 加载我的房间失败：', err && err.message)
        that.setData({ rooms: [] })
      })
      .then(function () {
        that.setData({ loadingRooms: false })
      })
  }
})
