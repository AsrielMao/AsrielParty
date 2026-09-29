/**
 * 登录与个人资料。
 *
 * 关于「直接获取微信资料」：
 * 微信从 2022-10-25 起回收了 wx.getUserProfile / wx.getUserInfo 返回真实头像昵称的能力，
 * 现在调用它们只会拿到灰色默认头像和「微信用户」。
 * 官方现行的唯一合规做法是「头像昵称填写能力」：
 *   - 头像：<button open-type="chooseAvatar" bindchooseavatar="...">
 *   - 昵称：<input type="nickname">
 * 本模块负责：wx.login 换 code → 后端换 openid（token），以及把用户填的头像昵称存下来。
 */

const api = require('./api')
const req = require('./request')
const store = require('./store')

/** 是否已经登录（本地有 token 且拿到过用户信息） */
function isLogged() {
  return !!req.getToken() && !!store.get().user
}

/** wx.login 的 Promise 版 */
function getLoginCode() {
  return new Promise(function (resolve, reject) {
    wx.login({
      success: function (res) {
        if (res && res.code) resolve(res.code)
        else reject(new Error('微信登录失败，请重试'))
      },
      fail: function () {
        reject(new Error('微信登录失败，请重试'))
      }
    })
  })
}

/**
 * 用微信身份登录。
 * @param {Object} profile 可选 { nickname, avatar }，用户在授权页填好的资料
 */
function login(profile) {
  const p = profile || {}
  return getLoginCode().then(function (code) {
    return api.login({
      code: code,
      nickname: p.nickname || '',
      avatar: p.avatar || ''
    })
  })
}

/**
 * 冷启动时恢复登录态：本地有 token 就拉一次最新用户信息。
 * 失败（token 过期 / 后端没起）就退回未登录，不阻塞启动。
 */
function restoreSession() {
  if (!req.getToken()) {
    store.patch({ logged: false })
    return Promise.resolve(null)
  }
  return api
    .getMe()
    .then(function (user) {
      return user
    })
    .catch(function (err) {
      console.warn('[auth restoreSession] 恢复登录态失败：', err && err.message)
      req.setToken('')
      store.patch({ user: null, logged: false })
      return null
    })
}

/** 保存资料（昵称 / 头像） */
function updateProfile(payload) {
  return api.updateMe(payload)
}

/** 退出登录（清本地登录态） */
function logout() {
  api.logout()
}

/** 当前用户（可能为 null） */
function currentUser() {
  return store.get().user
}

/**
 * 头像/昵称没填时的初始化默认值。
 * 这样用户一进来不至于顶着一个空名字。
 */
function defaultNickname() {
  return '聚会玩家'
}

module.exports = {
  isLogged: isLogged,
  login: login,
  restoreSession: restoreSession,
  updateProfile: updateProfile,
  logout: logout,
  currentUser: currentUser,
  defaultNickname: defaultNickname,
  getLoginCode: getLoginCode
}
