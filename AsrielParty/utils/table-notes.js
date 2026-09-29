/**
 * 牌桌上的「私人记录」：给每个人打的标签（好人 / 狼人 / 具体身份）。
 *
 * 这些内容**只存在本机**（wx.storage），不上传、不同步、也不会发给别人 ——
 * 纯粹是帮自己记事用的，和服务器、和其他玩家都没有关系。
 *
 * key 按「房间 + 用户」分开存，所以同一台设备换个人登录也不会串。
 *
 * `note` 字段是早期那个「记事板」留下来的，界面上已经没有入口了，
 * 留着只是为了兼容本机已有的旧数据。
 */

const PREFIX = 'ap_table_'

function keyOf(roomId, userId) {
  return PREFIX + roomId + '_' + userId
}

/**
 * 读一条记录。
 * @returns {{ note: String, marks: Object }} marks 形如 { [userId]: 'wolf' }
 */
function load(roomId, userId) {
  const empty = { note: '', marks: {} }
  if (!roomId || !userId) return empty

  try {
    const raw = wx.getStorageSync(keyOf(roomId, userId))
    if (!raw) return empty

    const data = typeof raw === 'string' ? JSON.parse(raw) : raw
    return {
      note: typeof data.note === 'string' ? data.note : '',
      marks: data.marks && typeof data.marks === 'object' ? data.marks : {}
    }
  } catch (e) {
    console.error('[table-notes] 读取失败', e)
    return empty
  }
}

/** 覆盖保存整条记录 */
function save(roomId, userId, data) {
  if (!roomId || !userId) return
  try {
    wx.setStorageSync(keyOf(roomId, userId), {
      note: (data && data.note) || '',
      marks: (data && data.marks) || {}
    })
  } catch (e) {
    console.error('[table-notes] 保存失败', e)
  }
}

/** 离开房间时把这张桌的记录清掉（换房间就重新记） */
function clear(roomId, userId) {
  if (!roomId || !userId) return
  try {
    wx.removeStorageSync(keyOf(roomId, userId))
  } catch (e) {
    console.error('[table-notes] 清理失败', e)
  }
}

module.exports = {
  load: load,
  save: save,
  clear: clear
}
