/**
 * wx.request 的 Promise 封装。
 * 统一处理：baseUrl 拼接、token 注入、HTTP 错误码、网络失败。
 */

const CONFIG = require('../config/index')

function getToken() {
  try {
    return wx.getStorageSync(CONFIG.storageKeys.token) || ''
  } catch (e) {
    return ''
  }
}

function setToken(token) {
  try {
    if (token) {
      wx.setStorageSync(CONFIG.storageKeys.token, token)
    } else {
      wx.removeStorageSync(CONFIG.storageKeys.token)
    }
  } catch (e) {
    console.error('[request setToken]', e)
  }
}

/** 后端约定的错误体：{ detail: '...' } 或 { message: '...' } 或 { errors: {...} } */
function pickErrorMessage(body, statusCode) {
  if (!body) return '请求失败（' + statusCode + '）'
  if (typeof body === 'string') return body
  if (body.detail) return body.detail
  if (body.message) return body.message
  if (body.errors && typeof body.errors === 'object') {
    const keys = Object.keys(body.errors)
    if (keys.length) {
      const first = body.errors[keys[0]]
      return Array.isArray(first) ? first[0] : String(first)
    }
  }
  return '请求失败（' + statusCode + '）'
}

/** 401/403 时清掉登录态，并通知调用方 */
let onUnauthorized = null
function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

/**
 * 把 wx.request 的 fail 翻译成人话。
 *
 * 最容易踩的坑是**开发者工具里的「合法域名校验」**：后端跑在 http://127.0.0.1:8000
 * 这种非 https 地址上时，请求压根没发出去，但报错长得像「服务没启动」，
 * 特别容易带着人往错的方向查（所以这里要分开说）。
 */
function networkError(err) {
  const msg = (err && err.errMsg) || ''
  let text = '连不上后端（' + CONFIG.baseUrl + '），服务启动了吗？'

  if (msg.indexOf('not in domain list') >= 0) {
    console.error(
      '[request] 这个地址被合法域名校验拦下了：' + CONFIG.baseUrl + '\n' +
      '请在开发者工具里勾上：详情 → 本地设置 → 「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」'
    )
    text = '域名校验没过：请在开发者工具「详情 → 本地设置」里勾上「不校验合法域名」'
  } else if (msg.indexOf('timeout') >= 0) {
    text = '请求超时了，看看后端服务和网络'
  }

  const e = new Error(text)
  e.statusCode = 0
  return e
}

/**
 * @param {Object} options
 *   url      {String} 以 / 开头的路径，例如 '/api/rooms'
 *   method   {String} GET / POST / PATCH / DELETE
 *   data     {Object}
 *   auth     {Boolean} 是否带 token，默认 true
 */
function request(options) {
  const opts = options || {}

  return new Promise(function (resolve, reject) {
    if (!CONFIG.baseUrl) {
      reject(new Error('未配置后端地址，请在 config/index.js 中设置 baseUrl'))
      return
    }

    const header = { 'content-type': 'application/json' }
    if (opts.auth !== false) {
      const token = getToken()
      if (token) header.Authorization = 'Bearer ' + token
    }

    wx.request({
      url: CONFIG.baseUrl + opts.url,
      method: opts.method || 'GET',
      data: opts.data || {},
      header: header,
      timeout: opts.timeout || 15000,
      success: function (res) {
        const status = res.statusCode
        if (status >= 200 && status < 300) {
          resolve(res.data)
          return
        }
        if (status === 401 || status === 403) {
          setToken('')
          if (onUnauthorized) onUnauthorized()
        }
        const httpErr = new Error(pickErrorMessage(res.data, status))
        // 把状态码带出去，调用方据此区分「房间不存在」这类业务情况
        httpErr.statusCode = status
        reject(httpErr)
      },
      fail: function (err) {
        console.error('[request fail]', opts.url, err)
        reject(networkError(err))
      }
    })
  })
}

module.exports = {
  request: request,
  getToken: getToken,
  setToken: setToken,
  setUnauthorizedHandler: setUnauthorizedHandler
}
