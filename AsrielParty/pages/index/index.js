// pages/index/index.js
const api = require('../../utils/api')
const auth = require('../../utils/auth')
const store = require('../../utils/store')
const roomView = require('../../utils/room-view')
const util = require('../../utils/util')

/** 房间列表项展示字段（与加入房间页共用同一份实现） */
const toRoomItem = roomView.toRoomItem

Page({
  data: {
    user: null,
    rooms: [],
    loadingRooms: false,

    // 一步登录：选头像 → 确认昵称 → 提交
    loginVisible: false,
    loginHint: '',
    pendingAvatar: '',
    pendingNickname: '',
    loggingIn: false
  },

  onLoad: function () {
    this._unsubscribe = store.subscribe(this.syncUser.bind(this))
    this.syncUser()
  },

  onShow: function () {
    this.syncUser()
    this.loadRooms()
  },

  onUnload: function () {
    if (this._unsubscribe) this._unsubscribe()
  },

  onPullDownRefresh: function () {
    this.loadRooms().then(function () {
      wx.stopPullDownRefresh()
    })
  },

  syncUser: function () {
    this.setData({ user: store.get().user })
  },

  loadRooms: function () {
    const that = this
    if (!auth.isLogged()) {
      this.setData({ rooms: [] })
      return Promise.resolve()
    }
    this.setData({ loadingRooms: true })
    return api
      .listMyRooms()
      .then(function (list) {
        that.setData({ rooms: list.map(toRoomItem) })
      })
      .catch(function (err) {
        console.warn('[index] 加载我的房间失败：', err && err.message)
        that.setData({ rooms: [] })
      })
      .then(function () {
        that.setData({ loadingRooms: false })
      })
  },

  /* ============================ 一步登录 ============================ */

  /**
   * 微信头像选择器的回调（按钮 open-type="chooseAvatar"）。
   * 微信不会自动把头像昵称给小程序，只能由用户在这个原生选择器里挑，
   * 默认就是「微信头像」，所以用户点一下就能用上自己的微信头像。
   */
  onQuickAvatar: function (e) {
    const url = (e.detail && e.detail.avatarUrl) || ''
    this.setData({
      pendingAvatar: url,
      loginHint: '',
      loginVisible: true
    })
  },

  /**
   * 把刚选的头像传到后端，再把拿到的地址写进用户资料。
   *
   * **这一步失败不算登录失败**：头像传不上去就先用本地临时路径顶着，
   * 座位组件会自动回退成昵称首字，不影响玩。
   */
  syncAvatar: function (user) {
    const path = this.data.pendingAvatar
    if (!path || !user) return Promise.resolve(user)

    return api
      .uploadAvatar(path)
      .then(function (url) {
        if (!url) return user
        return auth.updateProfile({ avatar: url })
      })
      .catch(function (err) {
        console.warn('[login] 头像没传上去，先用本地的顶着：', err && err.message)
        return user
      })
  },

  onNicknameInput: function (e) {
    this.setData({ pendingNickname: e.detail.value })
  },

  closeLogin: function () {
    if (this.data.loggingIn) return
    this.setData({ loginVisible: false })
  },

  onConfirmLogin: function () {
    if (this.data.loggingIn) return

    const nickname = (this.data.pendingNickname || '').trim() || auth.defaultNickname()
    const that = this

    this.setData({ loggingIn: true })
    util.loading('登录中')

    // 顺序很重要：**先登录拿到 token，再上传头像** —— 上传头像的接口是需要登录态的，
    // 反过来做会被后端 401 拦下，整个登录就卡在这一步了。
    // 而且头像只是锦上添花，传失败也不该把登录搞挂（见 syncAvatar）。
    auth
      .login({ nickname: nickname })
      .then(function (user) {
        return that.syncAvatar(user)
      })
      .then(function () {
        util.hideLoading()
        that.setData({
          loggingIn: false,
          loginVisible: false,
          loginHint: '',
          pendingAvatar: '',
          pendingNickname: ''
        })
        util.toast('登录成功', 'success')
        that.loadRooms()
      })
      .catch(function (err) {
        util.hideLoading()
        that.setData({ loggingIn: false })
        util.toastError(err)
      })
  },

  /* ============================ 导航 ============================ */

  goProfile: function () {
    wx.navigateTo({ url: '/pages/profile/profile' })
  },

  goCreate: function () {
    if (!this.ensureLogin()) return
    wx.navigateTo({ url: '/pages/create/create' })
  },

  goJoin: function () {
    if (!this.ensureLogin()) return
    wx.navigateTo({ url: '/pages/join/join' })
  },

  goRules: function () {
    wx.navigateTo({ url: '/pages/rules/rules' })
  },

  enterRoom: function (e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: '/pages/room/room?roomId=' + id })
  },

  /** 创建/加入房间前先确认登录，没登录就直接把登录弹层推上来 */
  ensureLogin: function () {
    if (auth.isLogged()) return true
    this.setData({ loginVisible: true, loginHint: '登录后就能创建和加入房间' })
    return false
  }
})
