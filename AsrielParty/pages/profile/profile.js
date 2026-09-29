// pages/profile/profile.js
const api = require('../../utils/api')
const auth = require('../../utils/auth')
const store = require('../../utils/store')
const util = require('../../utils/util')

Page({
  data: {
    avatar: '',
    nickname: '',
    initial: '?',
    isLogged: false,
    saving: false,
    /** 用户本次是否换了头像（换了才需要上传） */
    avatarChanged: false,
    /** 是否是从「去登录」跳过来的 */
    fromLogin: false
  },

  onLoad: function (options) {
    this._unsubscribe = store.subscribe(this.syncFromStore.bind(this))
    this.syncFromStore()
    this.setData({ fromLogin: !!(options && options.mode === 'login') })
  },

  onUnload: function () {
    if (this._unsubscribe) this._unsubscribe()
  },

  syncFromStore: function () {
    const user = store.get().user
    const patch = { isLogged: auth.isLogged() }
    // 用户正在编辑时不要用 store 里的旧值覆盖输入框
    if (!this.data.avatarChanged) {
      patch.avatar = user ? user.avatar : ''
      patch.nickname = user ? user.nickname || '' : ''
      patch.initial = user && user.initial ? user.initial : '?'
    }
    this.setData(patch)
  },

  /** 头像昵称填写能力：用户选了微信头像后拿到本地临时路径 */
  onChooseAvatar: function (e) {
    const url = e.detail && e.detail.avatarUrl
    if (!url) return
    this.setData({ avatar: url, avatarChanged: true })
  },

  onNicknameInput: function (e) {
    this.setData({ nickname: e.detail.value })
  },

  onSave: function () {
    if (this.data.saving) return

    const nickname = (this.data.nickname || '').trim()
    if (nickname.length > 20) {
      util.toast('昵称最多 20 个字')
      return
    }

    const finalNickname = nickname || auth.defaultNickname()
    const that = this
    const needUpload = this.data.avatarChanged && !!this.data.avatar

    this.setData({ saving: true })
    util.loading('保存中')

    // 顺序：**先确保有登录态**（上传头像的接口要它），再传头像，最后写资料。
    // 头像传失败不算保存失败 —— 顶上本地路径就行（座位那边会回退成首字）。
    const ensureLogin = auth.isLogged()
      ? Promise.resolve()
      : auth.login({ nickname: finalNickname })

    ensureLogin
      .then(function () {
        if (!needUpload) return ''
        return api.uploadAvatar(that.data.avatar).catch(function (err) {
          console.warn('[profile] 头像没传上去，先用本地的顶着：', err && err.message)
          return that.data.avatar
        })
      })
      .then(function (avatarUrl) {
        const payload = { nickname: finalNickname }
        if (avatarUrl) payload.avatar = avatarUrl
        return auth.updateProfile(payload)
      })
      .then(function () {
        util.hideLoading()
        that.setData({ saving: false, avatarChanged: false })
        util.toast('保存成功', 'success')
        setTimeout(function () {
          const pages = getCurrentPages()
          if (pages.length > 1) {
            wx.navigateBack({ delta: 1 })
          } else {
            wx.reLaunch({ url: '/pages/index/index' })
          }
        }, 700)
      })
      .catch(function (err) {
        util.hideLoading()
        that.setData({ saving: false })
        util.toastError(err)
      })
  },

  onLogout: function () {
    const that = this
    util
      .confirm('退出后需要重新用微信登录，确定吗？', '退出登录')
      .then(function (ok) {
        if (!ok) return
        auth.logout()
        that.setData({ avatar: '', nickname: '', avatarChanged: false })
        that.syncFromStore()
        util.toast('已退出')
      })
  }
})
