/**
 * 通用小工具
 */

/** 不包含易混淆字符（0/O、1/I/L）的房间号字符集 */
const CODE_CHARS = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'

/**
 * 生成随机房间号（默认 6 位）。
 */
function randomRoomCode(len) {
  const n = len || 6
  let out = ''
  for (let i = 0; i < n; i++) {
    out += CODE_CHARS[Math.floor(Math.random() * CODE_CHARS.length)]
  }
  return out
}

/** 0 -> '00' 补齐 */
function pad2(n) {
  return n < 10 ? '0' + n : '' + n
}

/**
 * 格式化时长（秒）为 mm:ss
 */
function formatDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60)
}

/**
 * 相对时间：刚刚 / n 分钟前 / n 小时前 / MM-DD HH:mm
 */
function fromNow(ts) {
  if (!ts) return ''
  const t = typeof ts === 'number' ? ts : new Date(ts).getTime()
  const diff = Date.now() - t
  if (diff < 60 * 1000) return '刚刚'
  if (diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + ' 分钟前'
  if (diff < 24 * 60 * 60 * 1000) return Math.floor(diff / 3600000) + ' 小时前'
  const d = new Date(t)
  return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes())
}

/** 轻提示 */
function toast(title, icon) {
  wx.showToast({
    title: title,
    icon: icon || 'none',
    duration: 1800
  })
}

/** 错误提示（统一从 Error 里取 message） */
function toastError(err) {
  const msg = (err && err.message) || '操作失败，请重试'
  toast(msg)
}

/** 显示 loading */
function loading(title) {
  wx.showLoading({ title: title || '加载中', mask: true })
}

function hideLoading() {
  wx.hideLoading()
}

/** Promise 化的 confirm */
function confirm(content, title) {
  return new Promise(function (resolve) {
    wx.showModal({
      title: title || '提示',
      content: content,
      confirmColor: '#7c5cff',
      success: function (res) {
        resolve(!!res.confirm)
      },
      fail: function () {
        resolve(false)
      }
    })
  })
}

/**
 * 把 rpx 换算成 px（用于需要像素计算的场景，比如环形座位定位）
 */
function rpx2px(rpx) {
  const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
  return (rpx * info.windowWidth) / 750
}

/** 简单节流 */
function throttle(fn, wait) {
  let last = 0
  return function () {
    const now = Date.now()
    if (now - last < (wait || 300)) return
    last = now
    return fn.apply(this, arguments)
  }
}

/** 给头像/昵称做兜底显示 */
function displayName(user) {
  if (!user) return '未知玩家'
  return user.nickname || '微信用户'
}

/** 生成一个本地临时 id（mock 模式用） */
function localId(prefix) {
  return (prefix || 'id') + '_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}

module.exports = {
  randomRoomCode: randomRoomCode,
  formatDuration: formatDuration,
  fromNow: fromNow,
  toast: toast,
  toastError: toastError,
  loading: loading,
  hideLoading: hideLoading,
  confirm: confirm,
  rpx2px: rpx2px,
  throttle: throttle,
  displayName: displayName,
  localId: localId
}
