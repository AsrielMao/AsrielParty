/**
 * 房间对象的展示层转换：列表项字段、环形座位坐标。
 * 首页 / 加入房间页 / 房间页共用，避免三处各写一份。
 */

const util = require('./util')

function round1(n) {
  return Math.round(n * 10) / 10
}

/** 房间 → 列表项展示字段 */
function toRoomItem(room) {
  return {
    id: room.id,
    code: room.code,
    title: room.title,
    gameName: room.gameName,
    gameEmoji: room.game ? room.game.emoji : '🎲',
    gameColor: room.game ? room.game.color : '#7c5cff',
    memberText: room.memberCount + ' / ' + room.maxPlayers,
    timeText: util.fromNow(room.updatedAt),
    isOwner: room.isOwner,
    statusText: room.status === 'playing' ? '游戏中' : '等人中'
  }
}

/**
 * 环形座位坐标（百分比，相对座位区容器）。
 *
 * 从正上方开始顺时针排布；用椭圆而不是正圆，是为了在竖屏里
 * 把 12 个座位拉开一点，不至于挤在一起。
 *
 * @param {Number} count 座位数
 * @param {Number} rx    横向半径（百分比）
 * @param {Number} ry    纵向半径（百分比）
 */
function calcSeatPositions(count, rx, ry) {
  const a = typeof rx === 'number' ? rx : 38
  const b = typeof ry === 'number' ? ry : 42
  const positions = []
  if (!count) return positions

  for (let i = 0; i < count; i++) {
    // -90° 是正上方，顺时针一圈
    const angle = ((-90 + (360 / count) * i) * Math.PI) / 180
    positions.push({
      left: round1(50 + a * Math.cos(angle)),
      top: round1(50 + b * Math.sin(angle))
    })
  }
  return positions
}

/**
 * 把成员铺到 maxPlayers 个座位槽位上。
 * 返回 [{ seatIndex, pos, member }]，member 为 null 表示空位。
 */
function buildSeatSlots(room) {
  if (!room) return []
  const count = room.maxPlayers || 0
  if (!count) return []

  const positions = calcSeatPositions(count)
  const bySeat = {}
  ;(room.members || []).forEach(function (m) {
    bySeat[m.seat] = m
  })

  const slots = []
  for (let i = 0; i < count; i++) {
    slots.push({
      seatIndex: i,
      pos: positions[i],
      member: bySeat[i] || null
    })
  }
  return slots
}

module.exports = {
  toRoomItem: toRoomItem,
  calcSeatPositions: calcSeatPositions,
  buildSeatSlots: buildSeatSlots
}
