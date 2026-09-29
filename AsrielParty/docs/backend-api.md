# AsrielParty 后端接口协议

小程序的接口调用全部集中在 `utils/api.js`，它有两种实现：

| 模式 | 触发条件 | 实现 |
|---|---|---|
| mock | `config/index.js` 里 `env = 'mock'`（默认） | `mock/handlers.js`，本地 `wx.storage` 模拟，无需后端 |
| 远程 | `env = 'dev'` 或 `'prod'` | `wx.request` 打下面这些接口 |

小程序侧已经按本协议把请求和返回结构都对齐好了；后端（`AsrielPartyBackend`）就是按这份文档实现的 —— **改接口时两边要一起改**，字段名和结构以本文档为准。

---

## 通用约定

- 所有请求/响应体为 JSON（头像上传除外，是 `multipart/form-data`）。
- 需要登录的接口带请求头 `Authorization: Bearer <token>`。
- 成功：HTTP 200/201，返回下面的「返回」结构。
- 失败：HTTP 4xx/5xx，返回 `{ "detail": "中文错误信息" }`，小程序会把它直接 toast 出来。
  - `401` / `403`：小程序会清除本地 token，视为未登录。
  - `404`：房间相关接口用它表示「房间不存在/已解散」，房间页据此自动退出。
- 房间号是 6 位，字符集为 `23456789ABCDEFGHJKMNPQRSTUVWXYZ`（去掉了易混的 `0O1IL`）。

> 根路径 `GET /` **不是接口**，是一个给人看的「服务在跑」状态页
> （列出接口清单和微信配置情况）。用浏览器打开后端地址看到这一页，说明服务正常 ——
> 后端没有网页界面，所以别再把它当成 404 了。

---

## 一、登录与用户

### 用户唯一标识用 openid

**后端 users 表的唯一键用 `openid`**，这是微信唯一提供给小程序的稳定用户标识。有三样东西是拿不到或不稳定的，别按它们做主键：

| 字段 | 能否拿到 | 说明 |
|---|---|---|
| 微信号（`wxid_...` 或自定义微信号） | ❌ | 微信从未开放给小程序，属敏感字段，官方明确禁止接口返回 |
| 手机号 | ❌（个人主体） | `getPhoneNumber` 只对**非个人主体且已完成认证**的小程序开放；且自 2023-08-28 起按次收费 0.03 元/次（共 1000 次免费体验额度） |
| 昵称 / 头像 | ⚠️ 需用户主动提供 | `wx.getUserProfile` 自 2022-10-25 起只返回「微信用户」+ 灰色默认头像，必须改用「头像昵称填写能力」由用户选择/填写 |

由此约定：

- **`openid`：users 表的主键/唯一键。** 用户在本小程序内的永久唯一标识，换头像、换昵称、换手机号都不影响。
- **`unionid`：预留一列（可空、可索引）。** 只有去微信开放平台绑定同主体账号后才会返回；绑定后可用于 App / 公众号 / H5 共享账号。不绑定就一直是 null。
- **`nickname` / `avatar`：普通可变字段**，用户可以随时改，不要拿它们做标识。
- **手机号：个人主体拿不到。** 真需要就让用户自己在资料页手填一个「联系方式」字段（不可验证但够用）；要微信验证过的手机号必须先升级到企业主体并完成认证。

### `POST /api/auth/login` （无需 token）

用微信 `code` 换 openid 并登录。

请求：
```json
{ "code": "wx.login 返回的 code", "nickname": "可选，首次登录一起带上", "avatar": "可选，头像 URL" }
```

返回：
```json
{ "token": "自定义登录态，建议 JWT 或随机串", "user": { "id": 1, "openid": "oXXXX", "unionid": null, "nickname": "小林", "avatar": "https://...", "createdAt": 1710000000000 } }
```

实现要点：用 `code` 调微信 `jscode2session` 换 `openid`（满足条件时还会带 `unionid`），按 `openid` 建用户（`openid` 上加唯一索引），签发 token。首次登录时 `nickname` / `avatar` 一并写入；用户没提供昵称的话给个默认值（小程序端传的是「聚会玩家」）。

### `GET /api/user/me`

返回：`{ "id": 1, "openid": "oXXXX", "unionid": null, "nickname": "小林", "avatar": "https://...", "createdAt": 1710000000000 }`

### `PATCH /api/user/me`

请求（字段都可选）：
```json
{ "nickname": "新昵称", "avatar": "https://..." }
```

返回：更新后的 user。

实现要点：改名后要同步更新该用户在所有房间成员里的展示信息。

### `POST /api/user/avatar` （`multipart/form-data`）

- 字段名：`avatar`
- 返回：`{ "url": "https://你的域名/media/avatars/xxx.jpg" }`

`chooseAvatar` 给的是本地临时文件，必须在这里换成长期可用的网络地址。

---

## 二、游戏

### `GET /api/games` （无需 token）

返回游戏列表，字段与小程序 `utils/games.js` 里的结构一致（小程序在后端返回空时会回落到本地静态数据）：

```json
[
  {
    "id": "werewolf",
    "name": "狼人杀",
    "emoji": "🐺",
    "color": "#7c5cff",
    "tagline": "找出藏在村民里的狼",
    "tags": ["推理", "发言", "阵营对抗"],
    "minPlayers": 6,
    "maxPlayers": 12,
    "recommend": "8 - 12 人",
    "duration": "20 - 40 分钟",
    "needHost": true,
    "intro": "……",
    "roles": [{ "emoji": "🐺", "name": "狼人", "camp": "狼人阵营", "desc": "……" }],
    "sections": [{ "title": "夜晚流程", "items": ["……"] }],
    "tips": ["……"]
  }
]
```

---

## 三、房间

房间对象（下面所有接口的返回都是这个结构）：

```json
{
  "id": "room_id",
  "code": "AB3K7M",
  "title": "周五晚上的局",
  "gameId": "werewolf",
  "maxPlayers": 10,
  "ownerId": 1,
  "status": "waiting",
  "createdAt": 1710000000000,
  "updatedAt": 1710000000000,
  "members": [
    { "userId": 1, "openid": "oXXXX", "nickname": "小林", "avatar": "https://...", "ready": false, "seat": 0, "online": true, "isBot": false }
  ]
}
```

约定：
- `seat` 是座位号，范围 `[0, maxPlayers)`，同一房间内唯一，加入时分配最小空闲座位。
- **`openid` 必须返回**：实时语音靠它匹配「谁在说话」，用来给对应座位加光圈。
- `status`：`waiting`（等人/准备中）| `playing`（游戏中）| `closed`（已解散）。
- 只有房主（`ownerId`）能：切换游戏、踢人、开始/结束游戏。

### `GET /api/rooms/mine`

当前用户参与的房间列表（按 `updatedAt` 倒序）。

### `POST /api/rooms/create`

请求：`{ "title": "可选", "gameId": "werewolf", "variantId": "newbie-6" }`

**版型（`variantId`）**：有些游戏（目前只有狼人杀）的座位数和角色配置由**版型**决定，房主不能自己乱设。

- 有版型的游戏：不传 `variantId` 就用默认版型；传了不存在的版型返回 400。座位数 = 版型的 `seats`。
- 没有版型的游戏：忽略 `variantId`，座位数固定用 `defaultSeats`（10）。
- 当前唯一的版型是狼人杀 `newbie-6`「6 人新手场」：6 个座位，配置 1 预言家 / 1 守卫 / 2 平民 / 2 狼人。
- 版型定义同时存在于两处，改的时候要一起改：小程序 `AsrielParty/utils/games.js` 的 `variants`，后端 `party/constants.py` 的 `VARIANTS`。
- 创建者自动成为房主并占 0 号座位。
- 返回：房间对象（含 `variantId`）。

> **房间不要求坐满**：`POST /api/rooms/start` 只校验当前人数 `>= 该游戏的 minPlayers`，人没到齐也能开局。

### 房间对象里的 `variantId`

房间对象会返回 `variantId`。小程序拿它做两件事：

1. 决定**房间布局** —— 狼人杀用「左右两列座位 + 中间进程记录」的专属布局，其他游戏用环形座位；
2. 决定**点头像能打的标签** —— 阵营标签（好人 / 狼人）+ 版型里的每个身份（预言家 / 守卫 / 平民 / 狼人）。

> 顺便说明：那些标签**只存在小程序本机**（`wx.storage`），不会发给服务器、也不会同步给别人，是纯粹的私人笔记。所以没有对应的接口。

### `GET /api/rooms/detail?roomId=xxx` 或 `?code=AB3K7M`

返回：房间对象。**房间不存在时返回 404**（房间页据此自动退出）。

### `POST /api/rooms/join`

请求：`{ "roomId": "xxx" }` 或 `{ "code": "AB3K7M" }`

- 已在房间内则幂等返回；房间满员返回 400 `{ "detail": "房间已满" }`。
- 返回：房间对象。

### `POST /api/rooms/leave`

请求：`{ "roomId": "xxx" }`

- 房主退出时把房主顺移给下一个成员；最后一人退出则删除房间。
- 返回：`{ "ok": true }`

### `PATCH /api/rooms/update` （仅房主）

请求（字段都可选）：
```json
{ "roomId": "xxx", "gameId": "undercover", "title": "新房间名", "status": "waiting" }
```

- 改 `gameId`：校验新游戏的 `minPlayers <= maxPlayers`，并把所有人的准备状态清零。
- `status: "waiting"`：用于「结束本局」，从 `playing` 回到准备状态并清空准备。**非房主必须拒绝。**

返回：房间对象。

### `POST /api/rooms/ready`

请求：`{ "roomId": "xxx", "ready": true }` → 返回房间对象。

### `POST /api/rooms/kick` （仅房主）

请求：`{ "roomId": "xxx", "userId": 2 }` → 返回房间对象。

### `POST /api/rooms/start` （仅房主）

- 校验人数 `>= 当前游戏的 minPlayers`，否则 400。
- 把 `status` 置为 `playing`。返回房间对象。
- **谁是卧底**：这一步同时开局发牌 —— 随机抽一组词、随机决定谁拿卧底词，把对局状态存进房间。见下一节。

### `POST /api/rooms/bots` （仅房主）

**人机** —— 一个人 + 一台手机也能把需要好几个人的游戏跑起来。

请求：`{ "roomId": "xxx", "count": 3 }`

- `count` 是「想要几个人机」，后端会把房间里的人机调到这个数量；
- 超出座位数会自动截断（人机也占座位），`count: 0` 就是清空；
- 只在 `status != 'playing'` 时允许改，否则 400（先「结束本局」）；
- 返回：房间对象。

人机是**后端自动驱动**的，注意这几点：

- **没有独立进程**：每次客户端轮询（`GET /api/rooms/detail`、`GET /api/rooms/draw/poll`）
  时顺手推它们一步。所以只要还有一个人开着页面，它们就会动 —— 对线下局永远成立。
- 每个机器人两次行动之间至少隔 2.5 秒，看起来像有人在慢慢想，而不是瞬移。
- 它们会做的事：你画我猜里选词 / 涂几笔 / 打字猜；狼人杀里选目标 / 说话 / 投票；
  谁是卧底里投票。
- 成员对象上的 `isBot: true` 就是它们，前端会在座位上标一个「人机」。

> 人机的目标是**让你一个人也能把流程走完**，不是「陪你好好玩一局」。
> 尤其是你画我猜：机器人画手只会随手涂几笔（它没法真画出个东西），
> 所以那一轮基本靠机器人自己猜完。

---

## 四、谁是卧底

对局状态挂在房间对象里，字段名 `gameState`。**它必须按请求者过滤后再返回**：只能带这个人自己的 `myWord` 与 `myRole`，别人的词和身份一个字都不能下发。

`gameState`（下发版本）结构：

```json
{
  "phase": "reveal",
  "round": 1,
  "undercoverCount": 1,
  "playerCount": 6,
  "aliveCount": 6,
  "votedCount": 0,
  "allVoted": false,
  "myUserId": 1,
  "myRole": "civilian",
  "myWord": "可乐",
  "myAlive": true,
  "myVotedTarget": null,
  "canVoteNow": true,
  "players": [
    { "userId": 1, "seat": 0, "nickname": "小林", "alive": true, "left": false, "isMe": true, "voted": false, "canVote": false }
  ],
  "roundResult": null,
  "winner": null
}
```

- `phase`：`reveal`（刚发牌）→ `voting`（有人投了）→ `result`（本轮已结算）→ `over`（整局结束）。
- `players[].voted` 只表示「投没投」，**不要暴露投给谁**；票型只在 `roundResult` 里公开。
- `roundResult`：`{ tie, outUserId, outNickname, outRole, outWord, tally: [{ userId, nickname, count }] }`，结算后才非空。
- `winner`：`"civilian"` | `"undercover"` | `null`。

### `POST /api/rooms/undercover-setup` （仅房主）

请求：`{ "roomId": "xxx", "undercoverCount": 2 }`

- 只在 `status != 'playing'` 时允许改，否则 400。
- 上限 `floor((人数 - 1) / 2)`（保证「卧底人数小于人数的一半」）；超出则按上限截断或报 400，二者取一但与客户端提示一致。
- 返回：房间对象（`undercoverCount` 已更新）。

### `POST /api/rooms/vote`

请求：`{ "roomId": "xxx", "targetUserId": 2 }`

- 校验：投票人还在场上、目标还在场上、不能投自己、当前不在 `result` / `over` 阶段。
- 允许改票（覆盖上一次）。
- **所有人都投完后自动结算本轮**：计票 → 票最多者出局（平票则本轮无人出局）→ 判定胜负 → 写 `roundResult`，`phase` 变 `result`；若已分出胜负则 `phase` 变 `over` 并写 `winner`。
- 返回：房间对象。

### `POST /api/rooms/next-round` （仅房主）

- 只在 `phase == 'result'` 时允许，否则 400。
- `round += 1`、清空所有人的 `votedTarget`、清空 `roundResult`、`phase` 回到 `reveal`。
- 返回：房间对象。

### 判定规则（务必与小程序端一致）

- 卧底人数上限：`floor((人数 - 1) / 2)`。
- 出局：本轮得票最多者；**平票则本轮无人出局**。
- 平民胜：场上卧底数为 0。
- 卧底胜：场上卧底数 `>=` 场上平民数。
- 有人中途退出房间或被踢：把他标记为出局（`left: true`），否则「等所有人投完」会永远卡住。

---

## 五、狼人杀

规则判定**全部在后端**（`party/werewolf.py`），小程序只负责展示和收集操作。
角色、夜晚顺序、阶段时长都在 `party/constants.py` 的 `ROLES` / `VARIANTS` 里配置 ——
加一个角色只要加 `ROLES` 一项 + 版型 `roles` 一行，夜晚的唤醒顺序会按 `night.order` 自动排出来。

当前「7 人最后幸存者」的唤醒顺序是：**狼妃封锁 → 狼人夜袭 → 女巫 → 守墓人 → 觉醒摄梦人**
（摄梦人特意排在最后：她选完梦语者就能当场读出结果，不用再让她睁一次眼）。

> 狼人杀**没有** mock 版实现：小程序在 `env = 'mock'` 下开局会直接报错，
> 提示切到 `dev` 并启动后端。这是刻意的 —— 免得两套规则慢慢跑偏。

### 阶段（`gameState.phase`）

| phase | 含义 | 怎么往下走 |
|---|---|---|
| `night` | 夜晚，按角色依次行动 | **自动**：轮到的人行动完就走下一步；全部完成自动结算并天亮 |
| `daybreak` | 天亮了，宣布昨夜情况 | 谁点一下「开始发言」都能推进 |
| `sheriff` | 警长竞选（预留，6 人场跳过） | —— |
| `speech` | 发言 | **只有当前发言人**能点「我说完了」；最后一位之后自动进入投票 |
| `vote` | 放逐投票 | **自动**：所有人表态完（弃票也算表态）就结算 |
| `pk_speech` | 平票后的 PK 发言 | 同 `speech` |
| `pk_vote` | PK 再投票 | 自动结算；PK 玩家本人不参与 |
| `result` | 本轮结算（谁出局） | 谁点一下都能进入下一夜 |
| `over` | 整局结束 | —— |

> **推进阶段不需要房主**，由「当前该操作的人」决定。
> 各阶段有倒计时（`phaseDuration`），但**只是提示**，到点不会自动推进。
> 客户端算剩余时间请用返回里的 `serverNow`，别用本机时间。
> 发言顺序里如果有人出局/退出会自动跳过；有人退出房间会被直接判出局，不会卡住流程。

出局规则：票最多者出局并留遗言；平票 → PK → PK 再平票则**本轮无人出局**。
胜负（屠边）：狼人全出局 → 好人胜；神职全出局或平民全出局 → 狼人胜。

### `gameState` 的身份可见规则（狼人杀的底线）

- 自己的 `myRoleId` / `myRoleName` 永远可见；
- **狼人能看到同伴**（那些 `players[].roleId` 会带出来）；
- 预言家的查验结果只在 `myChecks` 里，只有他自己拿得到；
- 觉醒摄梦人的读心结果在 `myDream` 里（只有她自己看得到）：她排在最后行动，
  所以一提交梦语者就立刻拿得到 `targetActed`，不用等到天亮；
- 其他人**只给编号和存活状态，不给身份**，直到 `phase === 'over'` 才全部公开；
- 白天出局的**是谁**写在 `roundResult` 里，但出局者的**身份不下发** ——
  `roundResult.outRoleId` 只在 `phase === 'over'` 时才带（`tests_werewolf.WerewolfDayTests.test_lynched_role_kept_secret_until_over` 守着这条）；
- 夜晚只暴露「现在轮到哪个角色」，不会泄漏别人选了什么（狼人之间互相可见，方便商量）。

### `gameState` 字段一览

| 字段 | 说明 |
|---|---|
| `phase` / `day` | 当前阶段 / 天数（`night` 时 `day` 表示「已经过去的白天数」，第一夜是 0） |
| `playerCount` / `aliveCount` | 总人数 / 存活人数 |
| `players[]` | `{ userId, seat, nickname, alive, left, isMe, voted, sheriff, isBot }`，身份字段见上面那条底线 |
| `players[].voted` | **只有自己那一行是真的**。投票进行中谁都不该知道别人投没投，后端对别人一律下发 `false`；票型要等 `roundResult.ballots` |
| `gameLog` | 牌桌中间那栏的**进程记录**（公开事件，所有人看到的是同一份）：`{ id, day, phase, kind, tone, text, tally?, ballots? }`。只写台面上的事（谁出局、票型、发言顺序），**身份不进这里** |
| `myRoleId` / `myRoleName` / `myRoleEmoji` / `myCamp` | 我的身份 |
| `myAlive` / `mySeat` | 我还活着吗 / 我的座位号（0 起） |
| `mySkills` | `{ healUsed, poisonUsed, shotUsed }`：我的一次性技能还剩什么 |
| `myChecks` | 预言家 / 守墓人的查验记录 `[{ userId, seat, nickname, result }]`，只有本人有 |
| `myDream` | 摄梦人的读心 `{ targetUserId, targetSeat, targetNickname, targetActed }`，只有本人有 |
| `myDisguised` | 隐狼专用：现在还在不在伪装 |
| `myBlock` | 狼妃专用：`{ broken, notified, knownBroken }` |
| `lastNightDeaths[]` / `peacefulNight` | 昨夜死讯（只给编号）/ 是不是平安夜 |
| `speechOrder[]` / `speechIndex` | 发言顺序 `[{ userId, seat, nickname }]` 与进行到第几位 |
| `currentSpeakerUserId` / `currentSpeakerSeat` | 当前轮到谁发言（不在发言阶段时为 `null`） |
| `pkUserIds` | 平票后进入 PK 的玩家 |
| `roundResult` | 本轮结算 `{ tie, outUserId, outSeat, outNickname, tally[], ballots[], pkUserIds[], note }`。`tally` 是票数，`ballots` 是**谁投给了谁**（`{ voterUserId, voterSeat, voterNickname, targetUserId, targetSeat, targetNickname }`，弃票的 target 为 `null`） |
| `winner` / `winReason` | 结局（`good` / `wolf`）与原因 |
| `pendingShooterUserId` / `pendingShooterSeat` / `canShoot` | 猎人等着开枪（`canShoot` 只对我自己为真） |
| `serverNow` / `phaseStartedAt` / `phaseDuration` | 倒计时基准与时长（**只是提示**，到点不会自动推进） |
| `night` | 只在 `phase === 'night'` 时存在，见下表 |

`night`（夜晚专属）：

| 字段 | 说明 |
|---|---|
| `stepAction` | 这一步是什么技能：`block` / `dream` / `kill` / `guard` / `check` / `grave` / `witch` |
| `stepRoleIds` / `stepRoleName` / `stepLabel` / `stepHint` | 现在轮到哪个角色、该干什么（直接拿去显示即可） |
| `isMyTurn` | 这一步是不是该我操作 |
| `actorCount` / `actedCount` | 这一步有几人要表态 / 已经表态了几个 |
| `myTargetSeat` | 我这一步已经选的人（没选是 `null`） |
| `wolfChoices[]` | 只有狼人看得到：`[{ userId, seat, targetSeat }]` 同伴各自选了谁 |
| `wolfTargetUserId` / `wolfTargetSeat` | 只有女巫行动时看得到：今晚谁被袭击了 |
| `dreamPicked` / `dreamConfirmed` | 摄梦人两步：梦语者有没有定下 / 有没有确认（定下之后 `myDream` 就有结果了） |
| `canSlay` / `lastTargetUserId` / `lastTargetSeat` | 只有摄梦人行动时看得到：能不能让梦语者出局 / 昨晚选的是谁 |
| `blockedUserId` / `blockedSeat` / `canBlock` | 只有狼妃行动时看得到：今晚封了谁 / 封锁还能不能用 |

### `POST /api/rooms/werewolf/act`

夜晚行动（查验 / 守护 / 刀人都走这一个接口）：

```json
{ "roomId": 1, "targetUserId": 3 }
```

- 只能在自己角色被唤醒的那一步操作，否则 400；
- 校验：不能查验自己、守卫不能守自己也不能连守同一人、狼人不能刀同伴；**觉醒摄梦人可以选自己**（等于自保）；
- 觉醒摄梦人是**两步**：先提交一次定下梦语者（此时就能读心），再提交一次确认（第二步可以带上 `{"skill":"slay"}` 直接让他出局，仅第二晚起）；
- 提交后**立刻生效** —— 预言家马上能在 `myChecks` 看到结果。

### `POST /api/rooms/werewolf/vote`

放逐投票 / PK 投票：

```json
{ "roomId": 1, "targetUserId": 3 }
```

- `targetUserId` 不传或为 `null` 即**弃票**（弃票也算「已表态」）；
- **可以投给自己**；
- PK 投票时：PK 玩家本人不能投，其余人只能投 PK 玩家；
- 所有人都表态后自动结算。

### `POST /api/rooms/werewolf/advance`

推进当前阶段，规则见上面那张表。**没有房主限制。**

---

## 六、你画我猜

规则判定全在后端（`party/draw_guess.py`）。

一局 = 房间里**每人轮流当一次画手**。每一轮：**选词 → 作画与猜词 → 本轮结算**。

> 和另外两个游戏不一样：**这里的倒计时是真的**。
> Django 里没有后台定时任务，所以「时间到」靠客户端轮询时顺手推进 ——
> `GET /api/rooms/draw/poll` 和 `GET /api/rooms/detail` 都会调一次
> `draw_guess.tick()`。只要还有一个人开着页面，这局就不会卡住。

### 阶段（`gameState.phase`）

| phase | 含义 | 怎么往下走 |
|---|---|---|
| `picking` | 画手挑词（15 秒） | 画手选完自动进 `drawing`；超时自动替他挑一个 |
| `drawing` | 作画 + 猜词（90 秒） | 所有人猜中、或时间到 → `result` |
| `result` | 本轮结算（20 秒） | 谁点一下「下一轮」都行；超时自动进下一轮 |
| `over` | 整局结束 | —— |

### 画板是怎么同步的

**没有 WebSocket，走的是增量轮询**：

- 画手在本地实时画（自己看着是跟手的），攒 0.6 秒左右把这几笔批量提交；
- 其他人每 0.7 秒拉一次「上次拿到的序号之后新增的笔画」，追加到画布上。

所以别人看到的画面大约滞后 1 秒 —— 对「看完再猜」够用了。

**坐标一律是 0~1 的归一化值**，这样谁用什么屏幕，看到的都是同一张画。

### `POST /api/rooms/draw/pick`

画手从候选词里挑一个：

```json
{ "roomId": 1, "wordIndex": 0 }
```

- 只有这一轮的画手能调，否则 400；
- `wordIndex` 是 `gameState.wordOptions` 的下标（那个字段只有画手拿得到）。

### `POST /api/rooms/draw/strokes`

画手提交一批笔画：

```json
{
  "roomId": 1,
  "strokes": [
    { "points": [[0.1, 0.2], [0.3, 0.4]], "color": "#f3f0ff", "width": 8 }
  ]
}
```

- 只有画手能调；
- 一笔里**至少要有 2 个点**（只有一个点的会被丢掉）；
- 坐标会被夹到 0~1，颜色必须是 `#rrggbb`，线宽限制在 2~24；
- 一次最多 40 笔、单笔最多 240 个点；
- 返回很轻：`{ "ok": true, "strokeSeq": 12 }` —— 画手会频繁调它，别返回整个房间对象。

### `POST /api/rooms/draw/clear`

画手擦掉重画。服务端会留下**一条点数空的「笔画」**当清屏信号（序号继续往下走），
客户端收到 `points` 为空就知道该清画布了 —— 这样不用额外加字段，也不会和增量序号打架。

### `POST /api/rooms/draw/guess`

```json
{ "roomId": 1, "text": "长颈鹿" }
```

- 画手不能猜；已经猜对的人不能再猜；
- 比对时忽略空格；
- **猜对**：猜的人 +10、画手 +5；这一条**不会**把答案写进流水（不然等于告诉其他人）；
- **猜错**：这句话会公开给所有人看；
- 所有人都猜中时这一轮直接结束，不用干等时间走完。

### `POST /api/rooms/draw/advance`

本轮结算看完，进入下一轮。**谁都能点**，不需要房主。

### `GET /api/rooms/draw/poll?roomId=1&sinceSeq=0`

画板的快速轮询（客户端大约每 0.7 秒一次）：

```json
{
  "strokes": [{ "seq": 3, "points": [[0.1, 0.1], [0.5, 0.5]], "color": "#ff0000", "width": 8 }],
  "seq": 3,
  "round": 2,
  "phase": "drawing",
  "remainSeconds": 45,
  "solvedUserIds": [1],
  "guesses": [{ "userId": 1, "nickname": "小林", "seat": 0, "text": "猜错了", "correct": false }],
  "mySolved": false,
  "myWord": "",
  "canPick": false,
  "canDraw": false,
  "canGuess": true,
  "canAdvance": false
}
```

- `strokes` 是 `sinceSeq` 之后**新增**的笔画；
- **换轮时服务端会把笔画清空、序号归零**，客户端要靠 `round` 变了发现「该清屏了」，
  然后把 `sinceSeq` 也重置成 0；
- 这个接口会顺手推进已经超时的阶段（见上面的说明）。

### `gameState` 字段一览

| 字段 | 说明 |
|---|---|
| `phase` / `round` / `totalRounds` | 阶段 / 第几轮 / 一共几轮（= 开局时的人数） |
| `drawerUserId` / `drawerSeat` / `drawerNickname` / `isDrawer` | 这一轮谁画 |
| `myWord` | 答案。**只有画手、已经猜对的人、以及本轮公布之后才有值** |
| `wordLength` / `wordCategory` | 字数和词类 —— 给猜的人当提示（选完词才有） |
| `wordOptions` | 候选词，**只有画手在 `picking` 阶段拿得到** |
| `solvedUserIds` / `solvedCount` / `guesserCount` / `mySolved` | 谁猜对了 / 猜对几个 / 一共几个要猜的 / 我猜对了吗 |
| `guesses[]` | 最近的猜测流水 `{ userId, nickname, seat, text, correct }`（猜对那条的 `text` 是空串） |
| `scores[]` | 所有人按分数排序 `{ userId, seat, nickname, score, isDrawer, solved, isMe }` |
| `myScore` | 我的分数 |
| `roundResult` | 本轮结算 `{ word, category, timeout, solvedUserIds, gain }` |
| `winnerUserIds` | 整局结束时分数最高的人（可能并列），只在 `over` 时有 |
| `canPick` / `canDraw` / `canGuess` / `canAdvance` | 这一步我能干什么 |
| `strokeSeq` | 画布上已经画了多少笔（客户端拿它对齐增量） |
| `serverNow` / `phaseStartedAt` / `phaseDuration` / `remainSeconds` | 倒计时（这一个**是真的会推进**） |

### 你画我猜的「底线」

- **答案只给画手、已经猜对的人，以及本轮公布之后** —— 其他人只能看到字数和词类；
- 候选词只给画手；
- 猜对的那条不写进流水（否则等于把答案告诉别人）。

---

## 七、实时语音

### `POST /api/rooms/voice-signature`

请求：`{ "roomId": "xxx", "code": "AB3K7M" }`

返回：
```json
{
  "groupId": "ap_room_AB3K7M",
  "signature": "……",
  "nonceStr": "……",
  "timeStamp": 1710000000
}
```

实现要点（这是**必须放在后端**的部分，appsecret 不能进小程序）：

1. 取 `groupId`（建议直接用房间号拼成固定串，同一房间的用户必须算出相同 `groupId`）。
2. 生成随机 `nonceStr` 和当前 `timeStamp`（秒）。
3. 把 `appId`、`groupId`、`nonceStr`、`timeStamp` 四个值**按字典序排序**后拼接成一个字符串，用 `appsecret` 做 HMAC-SHA256，得到 `signature`。

小程序侧 `utils/voice.js` 会把这些参数直接喂给 `wx.joinVoIPChat({ roomType: 'voice', ... })`。

> 注意：`wx.joinVoIPChat` 单个房间**最多 10 人**（微信硬限制）。房间座位上限可以开到 12，超过 10 人时房间页会给出降级提示，语音按钮不会发起连接。另外还需要在小程序管理后台「开发 → 接口设置」里开通实时语音接口权限。

---

## 八、实时同步（后续扩展）

目前房间页是**每 3 秒轮询** `GET /api/rooms/detail` 来同步（`config/index.js` 的 `roomPollInterval`）。

你画我猜的画板另有一套更快的轮询（`GET /api/rooms/draw/poll`，约 0.7 秒）—— 因为画一笔要等 3 秒才出现在别人屏幕上，那是没法玩的。如果哪天想上 WebSocket，画板是最值得先换的地方。

如果之后想换成 WebSocket（推荐用 Django Channels），建议：

- 地址：`config/index.js` 里的 `wsBaseUrl` + `/ws/rooms/{roomId}/?token=<token>`
- 服务端在房间有任何变化（成员进出、准备、切换游戏、开局、**投票、结算**）时，向该房间所有连接推送**最新的房间对象**（结构与上面的房间对象完全一致，`gameState` 同样要按接收者过滤）。
- 小程序侧收到后直接走 `pages/room/room.js` 里已有的 `render(room)` 渲染即可，轮询间隔可以调大或关掉。

---

## 九、后端实现要点

后端已经按这份协议实现了（见 `AsrielPartyBackend/party/`）。下面几条是踩过的点，改代码时别绕回去：

- 接口都是普通 JSON 接口，用纯 Django 视图写就够了（这个项目没引 DRF）。
- 房间成员单独一张表（`room_id`, `user_id`, `seat`, `ready`, `joined_at`），`seat` 有唯一约束。
- 座位分配在事务里做「查最小空闲号 + 插入」，避免并发抢座。
- 房间状态存在数据库即可（几个朋友用，量极小）；要抗并发再考虑 Redis。
- `updatedAt` 在成员进出 / 准备 / 切换游戏时更新，房间列表按它排序。
- **「结束本局」和「切换游戏」都要把该房间的对局记录清掉（两张表都要清）**，否则会残留上一局的 `gameState`。
