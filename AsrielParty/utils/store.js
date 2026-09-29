/**
 * 极简全局状态：一个可订阅的对象。
 *
 * 用法：
 *   store.patch({ user: u })            // 更新
 *   store.get().user                    // 读取
 *   const off = store.subscribe(fn)     // 订阅，返回取消订阅函数
 */

const state = {
  /** 当前登录用户 { id, nickname, avatar, openid } */
  user: null,
  /** 是否已经拿到用户信息 */
  logged: false,
  /** 当前所在房间（房间页维护） */
  room: null,
  /** 语音状态，见 utils/voice.js */
  voiceState: null
}

const listeners = []

function get() {
  return state
}

function patch(partial) {
  if (!partial) return state
  Object.keys(partial).forEach(function (k) {
    state[k] = partial[k]
  })
  listeners.slice().forEach(function (fn) {
    try {
      fn(state)
    } catch (e) {
      console.error('[store listener error]', e)
    }
  })
  return state
}

function subscribe(fn) {
  if (typeof fn !== 'function') return function () {}
  listeners.push(fn)
  return function unsubscribe() {
    const i = listeners.indexOf(fn)
    if (i > -1) listeners.splice(i, 1)
  }
}

function clear() {
  patch({ user: null, logged: false, room: null })
}

module.exports = {
  get: get,
  patch: patch,
  subscribe: subscribe,
  clear: clear
}
