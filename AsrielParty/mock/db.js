/**
 * mock 模式的本地「数据库」，用 wx.storage 持久化。
 *
 * 结构：
 *   {
 *     users: { [userId]: { id, openid, nickname, avatar, createdAt } },
 *     rooms: { [roomId]: Room },
 *     currentUserId: ''             // 当前设备上登录的用户（模拟 openid 的稳定性）
 *   }
 *
 * Room:
 *   {
 *     id, code, title, gameId, maxPlayers, ownerId,
 *     status: 'waiting' | 'playing' | 'closed',
 *     createdAt, updatedAt,
 *     members: [ { userId, nickname, avatar, ready, seat, joinedAt, online } ]
 *   }
 */

const CONFIG = require('../config/index')

const STORAGE_KEY = CONFIG.storageKeys.mockDb

function emptyDb() {
  return { users: {}, rooms: {}, currentUserId: '' }
}

function read() {
  try {
    const raw = wx.getStorageSync(STORAGE_KEY)
    if (!raw) return emptyDb()
    const db = typeof raw === 'string' ? JSON.parse(raw) : raw
    return {
      users: db.users || {},
      rooms: db.rooms || {},
      currentUserId: db.currentUserId || ''
    }
  } catch (e) {
    console.error('[mock db read]', e)
    return emptyDb()
  }
}

function write(db) {
  try {
    wx.setStorageSync(STORAGE_KEY, db)
  } catch (e) {
    console.error('[mock db write]', e)
  }
}

/** 清空本地假数据（开发者工具控制台里可以调 require('/mock/db').reset()） */
function reset() {
  write(emptyDb())
}

/** 把 storage 里的房间集合按「最后更新」排序返回 */
function roomsSorted(db) {
  return Object.keys(db.rooms)
    .map(function (k) {
      return db.rooms[k]
    })
    .sort(function (a, b) {
      return (b.updatedAt || 0) - (a.updatedAt || 0)
    })
}

module.exports = {
  STORAGE_KEY: STORAGE_KEY,
  read: read,
  write: write,
  reset: reset,
  roomsSorted: roomsSorted
}
