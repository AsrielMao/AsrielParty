/**
 * 全局配置。
 *
 * 默认跑在 mock 模式下：大部分接口由 mock 层在本地实现，用 wx.storage 持久化，
 * 不启动任何服务就能在开发者工具里跑通「登录 → 建房 → 谁是卧底」。
 *
 * 后端（AsrielPartyBackend，Django）已经写好了，切过去只需两步：
 *   1. 把 env 改成 'dev'（或 'prod'）；
 *   2. 在下面的 ENV 里填上你的服务器地址。
 * 接口协议见 docs/backend-api.md。
 *
 * 注意：**狼人杀没有 mock 实现**，env 保持 'mock' 时点「开始游戏」会提示切到 dev。
 */

// 'mock' 本地模拟 | 'dev' 连自建后端（开发） | 'prod' 连自建后端（正式）
const env = 'dev'

const ENV = {
  mock: {
    useMock: true,
    baseUrl: '',
    wsBaseUrl: ''
  },
  dev: {
    useMock: false,
    // 开发时填你云服务器上 Django 的地址，例如 http://1.2.3.4:8000
    baseUrl: 'http://127.0.0.1:8000',
    // Django Channels 的 websocket 地址，例如 ws://1.2.3.4:8000
    wsBaseUrl: 'ws://127.0.0.1:8000'
  },
  prod: {
    useMock: false,
    // 正式环境必须是 https / wss，且域名要加进小程序后台的「服务器域名」白名单
    baseUrl: 'https://party.example.com',
    wsBaseUrl: 'wss://party.example.com'
  }
}

const current = ENV[env]

module.exports = {
  env: env,
  useMock: current.useMock,
  baseUrl: current.baseUrl,
  wsBaseUrl: current.wsBaseUrl,

  appName: '私人聚会',

  /** 房间号长度 */
  roomCodeLength: 6,

  /**
   * 房间默认座位数。
   * 有「版型」的游戏（狼人杀）由版型决定座位数，其余游戏一律用这个。
   */
  defaultSeats: 10,

  /**
   * wx.joinVoIPChat（实时语音）单个房间最多 10 人，这是微信的硬限制。
   * 房间座位数允许开到 12，但超过这个人数时语音按钮会降级提示。
   */
  voiceMaxPlayers: 10,

  /** 房间信息轮询间隔（毫秒）。接上后端 WebSocket 后可以调大或关掉。 */
  roomPollInterval: 3000,

  /** 本地缓存 key */
  storageKeys: {
    token: 'ap_token',
    user: 'ap_user',
    mockDb: 'ap_mock_db'
  }
}
