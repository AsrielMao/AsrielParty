/**
 * 实时语音适配层。
 *
 * 小程序里唯一免费、官方内置的多人实时语音能力是 wx.joinVoIPChat：
 *   - 基础库 2.7.0 起支持
 *   - 单个房间最多 10 人（微信硬限制，管你再大的房间）
 *   - 需要麦克风权限 scope.record
 *   - 签名 signature 必须由后端用 appsecret 计算，小程序里算不了
 *
 * 注意：正式使用还需要在小程序管理后台「开发 → 接口设置」里开通实时语音接口权限。
 * 没开通、或者个人主体审核未通过时，joinVoIPChat 会直接失败 —— 所以本模块
 * 把失败都收敛成可读的中文提示，并让房间页能降级到「用微信群语音通话」的引导。
 *
 * 界面层只依赖：join / leave / getState / subscribe，不关心底层是 mock 还是真机。
 */

const CONFIG = require('../config/index')
const api = require('./api')

const state = {
  /** 当前环境是否支持 wx.joinVoIPChat */
  supported: true,
  /** 是否已连上语音房间 */
  joined: false,
  /** 正在连接中 */
  connecting: false,
  /** 连接是否仍然有效（切后台会被系统断开） */
  alive: false,
  /** 语音房间对应的业务房间 id */
  roomId: '',
  /** 是否为 mock 模式下的模拟连接 */
  mock: false,
  /** 房间内成员的 openid 列表 */
  memberOpenids: [],
  /** 正在说话的成员 openid 列表 */
  speakerOpenids: [],
  /** 最近一次错误的可读描述 */
  error: ''
}

const listeners = []
let eventsBound = false

function emit() {
  const snapshot = getState()
  listeners.slice().forEach(function (fn) {
    try {
      fn(snapshot)
    } catch (e) {
      console.error('[voice listener error]', e)
    }
  })
}

function subscribe(fn) {
  if (typeof fn !== 'function') return function () {}
  listeners.push(fn)
  return function unsubscribe() {
    const i = listeners.indexOf(fn)
    if (i > -1) listeners.splice(i, 1)
  }
}

function getState() {
  return {
    supported: state.supported,
    joined: state.joined,
    connecting: state.connecting,
    alive: state.alive,
    roomId: state.roomId,
    mock: state.mock,
    memberOpenids: state.memberOpenids.slice(),
    speakerOpenids: state.speakerOpenids.slice(),
    memberCount: state.memberOpenids.length,
    error: state.error
  }
}

function isJoined() {
  return state.joined
}

function isAlive() {
  return state.alive
}

/** 切后台后系统会断开语音，这里同步状态 */
function markDisconnected() {
  state.joined = false
  state.alive = false
  state.memberOpenids = []
  state.speakerOpenids = []
  emit()
}

/** 检测当前环境有没有这个 API */
function checkSupported() {
  const ok = typeof wx.joinVoIPChat === 'function'
  state.supported = ok
  if (!ok) {
    state.error = '当前微信版本不支持实时语音（需要基础库 2.7.0 及以上）'
  }
  return ok
}

/* ============================ 事件绑定 ============================ */

function bindEvents() {
  if (eventsBound) return
  eventsBound = true

  if (typeof wx.onVoIPChatMembersChanged === 'function') {
    wx.onVoIPChatMembersChanged(function (res) {
      state.memberOpenids = (res && res.openIdList) || []
      emit()
    })
  }
  if (typeof wx.onVoIPChatSpeakersChanged === 'function') {
    wx.onVoIPChatSpeakersChanged(function (res) {
      state.speakerOpenids = (res && res.openIdList) || []
      emit()
    })
  }
  if (typeof wx.onVoIPChatInterrupted === 'function') {
    wx.onVoIPChatInterrupted(function () {
      state.joined = false
      state.alive = false
      state.error = '语音连接已中断（可能接入了电话，或微信把小程序切到后台）'
      emit()
    })
  }
}

function unbindEvents() {
  if (!eventsBound) return
  eventsBound = false
  if (typeof wx.offVoIPChatMembersChanged === 'function') wx.offVoIPChatMembersChanged()
  if (typeof wx.offVoIPChatSpeakersChanged === 'function') wx.offVoIPChatSpeakersChanged()
  if (typeof wx.offVoIPChatInterrupted === 'function') wx.offVoIPChatInterrupted()
}

/* ============================ 权限 ============================ */

/**
 * 确保拿到麦克风权限。
 * 用户之前明确拒绝过时，抛出 NEED_RECORD_SETTING，由界面引导去设置页重新打开。
 */
function ensureRecordAuth() {
  return new Promise(function (resolve, reject) {
    wx.getSetting({
      success: function (res) {
        const auth = (res && res.authSetting) || {}
        if (auth['scope.record'] === true) {
          resolve(true)
          return
        }
        if (auth['scope.record'] === false) {
          reject(new Error('NEED_RECORD_SETTING'))
          return
        }
        wx.authorize({
          scope: 'scope.record',
          success: function () {
            resolve(true)
          },
          fail: function () {
            reject(new Error('NEED_RECORD_SETTING'))
          }
        })
      },
      fail: function () {
        // 拿不到 setting 就直接试，让 joinVoIPChat 自己报错
        resolve(true)
      }
    })
  })
}

/** 引导用户去设置页打开麦克风权限 */
function openRecordSetting() {
  return new Promise(function (resolve) {
    wx.openSetting({
      success: function (res) {
        resolve(!!(res && res.authSetting && res.authSetting['scope.record']))
      },
      fail: function () {
        resolve(false)
      }
    })
  })
}

/* ============================ 错误码 ============================ */

function describeError(err) {
  const code = err && (err.errCode !== undefined ? err.errCode : err.errorCode)
  if (code === -1) return '你已经在语音房间里了'
  if (code === -2) return '录音设备被占用（正在微信语音通话或系统通话中）'
  if (code === -3) return '加入了通话期间退出了语音房间'
  if (code === -1000) return '语音服务出了点问题，稍后再试试'
  if (code === 10401) return '语音签名校验失败，请检查后端的 appsecret 配置'
  if (err && err.message === 'NEED_RECORD_SETTING') return '需要麦克风权限才能语音'
  if (err && err.errMsg) return String(err.errMsg)
  return (err && err.message) || '语音连接失败'
}

/* ============================ 加入 / 退出 ============================ */

/**
 * 加入语音房间。
 * @param {Object} room 已 decorate 过的房间对象
 */
function join(room) {
  if (state.joined) return Promise.resolve(getState())
  if (state.connecting) return Promise.reject(new Error('正在连接语音…'))
  if (!room || !room.id) return Promise.reject(new Error('房间信息不完整'))

  if (room.memberCount > CONFIG.voiceMaxPlayers) {
    return Promise.reject(
      new Error(
        '实时语音最多支持 ' + CONFIG.voiceMaxPlayers + ' 人，当前房间有 ' + room.memberCount + ' 人'
      )
    )
  }

  if (!checkSupported()) {
    return Promise.reject(new Error(state.error))
  }

  state.connecting = true
  state.error = ''
  state.roomId = room.id
  emit()

  // mock 模式：不真的连微信语音，只模拟一个「已连接」状态，方便把界面跑通
  if (CONFIG.useMock) {
    state.mock = true
    state.joined = true
    state.alive = true
    state.connecting = false
    state.memberOpenids = (room.members || []).map(function (m) {
      return m.openid || m.userId
    })
    state.speakerOpenids = []
    emit()
    return Promise.resolve(getState())
  }

  return ensureRecordAuth()
    .then(function () {
      return api.voiceSignature({ roomId: room.id, code: room.code })
    })
    .then(function (sig) {
      return new Promise(function (resolve, reject) {
        wx.joinVoIPChat({
          roomType: 'voice',
          groupId: sig.groupId,
          signature: sig.signature,
          nonceStr: sig.nonceStr,
          timeStamp: sig.timeStamp,
          success: resolve,
          fail: reject
        })
      })
    })
    .then(function () {
      state.mock = false
      state.joined = true
      state.alive = true
      state.connecting = false
      state.error = ''
      bindEvents()
      emit()
      return getState()
    })
    .catch(function (err) {
      state.connecting = false
      state.joined = false
      state.alive = false
      state.error = describeError(err)
      emit()
      const e = new Error(state.error)
      e.needRecordSetting = err && err.message === 'NEED_RECORD_SETTING'
      throw e
    })
}

/** 退出语音房间 */
function leave() {
  const wasJoined = state.joined
  const wasMock = state.mock

  state.joined = false
  state.alive = false
  state.connecting = false
  state.roomId = ''
  state.mock = false
  state.memberOpenids = []
  state.speakerOpenids = []

  if (!wasMock && wasJoined && typeof wx.exitVoIPChat === 'function') {
    wx.exitVoIPChat({
      complete: function () {}
    })
  }
  unbindEvents()
  emit()
  return Promise.resolve(getState())
}

/** 某个 openid 是否正在说话（页面用来给座位加光圈） */
function isSpeaking(openid) {
  if (!openid) return false
  return state.speakerOpenids.indexOf(openid) > -1
}

module.exports = {
  subscribe: subscribe,
  getState: getState,
  isJoined: isJoined,
  isAlive: isAlive,
  isSpeaking: isSpeaking,
  checkSupported: checkSupported,
  join: join,
  leave: leave,
  markDisconnected: markDisconnected,
  openRecordSetting: openRecordSetting,
  voiceMaxPlayers: CONFIG.voiceMaxPlayers
}
