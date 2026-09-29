// app.js
const auth = require('./utils/auth')
const store = require('./utils/store')
const voice = require('./utils/voice')

App({
  globalData: {
    /** 小程序启动完成的时间戳，用于判断冷启动 */
    launchedAt: Date.now()
  },

  onLaunch() {
    // 恢复本地登录态（有 token 就拉一次用户信息，没有就等用户点「微信登录」）
    auth.restoreSession()
  },

  onShow() {
    // 切回前台时，语音连接会被系统断开，同步一下状态
    if (voice.isJoined() && !voice.isAlive()) {
      voice.markDisconnected()
      store.patch({ voiceState: voice.getState() })
    }
  },

  onError(err) {
    console.error('[app onError]', err)
  },

  onUnhandledRejection(res) {
    console.error('[app unhandledRejection]', res && res.reason)
  }
})
