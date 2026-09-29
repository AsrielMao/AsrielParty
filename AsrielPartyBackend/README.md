# 私人聚会 · 后端

小程序「私人聚会」的后端服务。Django 6.1 + SQLite，**不依赖任何第三方包**（连 DRF 都没用）。

接口协议见 `../AsrielParty/docs/backend-api.md` —— 那份文档和小程序端的 `utils/api.js` 是对齐的。

---

## 跑起来

```bash
cd AsrielPartyBackend

# 1. 用项目里的虚拟环境
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 建表（**每次拉到新代码都先跑一遍这个** —— 加了字段没迁移的话，接口会直接 500）
.venv/Scripts/python.exe manage.py migrate

# 3. 把卧底词库导进数据库（可重复执行）
.venv/Scripts/python.exe manage.py seed_words

# 4. 起服务
.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000
```

起来之后用浏览器打开 <http://127.0.0.1:8000>，会看到一个**状态页**：
告诉你服务正常、微信 AppSecret 配没配、以及所有接口的清单。

> 后端只提供 `/api/...` 接口，没有网页界面 —— 所以根路径正常就是这一页。
> 如果看到的是 Django 的 `Page not found (404)`，那说明服务是旧版本（或者端口被别的东西占了）。

手机真机连过来时用 `runserver 0.0.0.0:8000`，并把小程序里的 `baseUrl` 填成电脑的**局域网 IP**。

跑测试：

```bash
.venv/Scripts/python.exe manage.py test party
```

---

## 配微信 AppSecret

`AppSecret` **不要写进代码、不要提交到仓库**。用环境变量传：

```bash
# Windows (PowerShell)
$env:WECHAT_APPSECRET = "你的 appsecret"

# Linux / macOS
export WECHAT_APPSECRET="你的 appsecret"
```

AppID 已经有默认值（`wx0300ac9bfc690c14`，写死没关系，它不是秘密）。

### 没配 AppSecret 时会怎样（开发期降级）

代码里有个开关 `WECHAT_DEV_FALLBACK`，**没配 AppSecret 时自动开启**：

| 能力 | 降级后的行为 |
|---|---|
| 登录 | 不请求微信，用 `code` 派生一个稳定的假 openid（同一个 code 永远得到同一个人），本机就能把整个流程跑通 |
| 实时语音签名 | 返回假签名。接口形状与真实一致，但**真机上 `wx.joinVoIPChat` 仍会失败**，因为微信校验不过 |

配好 AppSecret 后**自动切到真实微信接口**，代码不用改。

---

## 和小程序对接

小程序那边只需要改 `AsrielParty/config/index.js` 两个值：

```js
const env = 'dev'                          // 从 'mock' 改成 'dev'
// 再把 ENV.dev.baseUrl 改成服务器地址，例如：
baseUrl: 'http://127.0.0.1:8000'
```

然后：

- **本地联调**：微信开发者工具 → 详情 → 本地设置 → 勾上**「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**（否则 `wx.request` 会被拦，而且报错会误导成"服务没启动"）。
  项目里的 `project.private.config.json` 已经把 `urlCheck` 设成 `false` 了，正常不用手动再点；
  如果还是被拦，去界面上确认一下这个开关（改完重新编译一次）。
- **真机 / 线上**：必须是 `https`，且域名要在小程序后台「开发管理 → 开发设置 → 服务器域名」里加白名单。语音还需要 wss 域名。

改完之后页面代码一行都不用动 —— `utils/api.js` 会自动从 mock 切到真实请求。

---

## 部署到云服务器

```bash
# 服务器上
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_words

# 环境变量
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="换成随机长字符串"
export DJANGO_ALLOWED_HOSTS="你的域名或IP"
export WECHAT_APPSECRET="你的 appsecret"

python manage.py runserver 0.0.0.0:8000     # 正式环境请用 gunicorn + nginx
```

正式环境还要注意：

- `DEBUG=0` 之后 Django 不再伺服上传的头像，要让 Nginx 把 `MEDIA_ROOT`（默认 `media/`）挂成 `/media/` 静态目录。
- 建议加一层 Nginx 反代并配好 HTTPS 证书。

---

## 代码结构

```
AsrielPartyBackend/
├── asrielpartybackend/      Django 项目（settings / urls）
├── party/                   唯一的业务 app
│   ├── models.py            用户、房间、成员、三个游戏的对局、词库
│   ├── views.py             全部 27 个接口
│   ├── urls.py              路由（与小程序 ROUTES 一一对应）
│   ├── auth.py              token 认证 + JSON 解析 + 统一错误格式
│   ├── wechat.py            code 换 openid、语音签名
│   ├── bots.py              人机（增删 + 在客户端轮询时顺手驱动它们）
│   ├── undercover.py        谁是卧底的规则（发牌 / 投票 / 计票 / 胜负）
│   ├── werewolf.py          狼人杀的规则与流程（夜晚唤醒顺序 / 技能结算 / 白天投票 / 胜负）
│   ├── draw_guess.py        你画我猜的规则与流程（选词 / 笔画增量 / 猜词计分 / 超时推进）
│   ├── draw_words.py        你画我猜的词库（分类 + 抽候选词）
│   ├── constants.py         与小程序对齐的常量 + 角色表 ROLES + 版型 VARIANTS
│   ├── presenters.py        模型 → 小程序 JSON（camelCase）
│   ├── words.py             卧底词库（60 组）
│   ├── tests.py             房间 / 用户 / 卧底的集成测试
│   ├── tests_werewolf.py    6 人新手场：发牌 / 夜晚 / 白天 / 胜负
│   ├── tests_survivor.py    7 人最后幸存者：隐狼 / 狼妃封锁 / 觉醒摄梦 / 守墓人 / 屠边
│   ├── tests_draw.py        你画我猜：选词 / 笔画 / 猜词 / 计分 / 超时自动推进
│   ├── tests_bots.py        人机：增删 / 权限 / 三个游戏都能被它推着走
│   └── management/commands/seed_words.py
└── db.sqlite3               开发数据库
```

### 数据表

| 表 | 说明 |
|---|---|
| `ap_user` | 用户。唯一键是 `openid`，另有 `token` 作为小程序登录态 |
| `ap_room` | 房间。`code` 是 6 位房间号（唯一） |
| `ap_room_member` | 房间成员。有 `(room, seat)` 和 `(room, user)` 两个唯一约束 |
| `ap_undercover_game` | 一局谁是卧底 |
| `ap_undercover_player` | 这一局里每个玩家的身份、词、是否出局、投给谁 |
| `ap_undercover_word` | 词库 |
| `ap_werewolf_game` | 一局狼人杀（阶段 / 夜晚步骤 / 发言顺序 / 结算 / 胜负） |
| `ap_werewolf_player` | 这一局里每个玩家的身份、存活、一次性技能（解药 / 毒药 / 枪）、查验记录 |
| `ap_draw_game` | 一局你画我猜（阶段 / 画手顺序 / 本轮答案 / 得分 / 猜测流水 / 笔画序号） |
| `ap_draw_stroke` | 画布上的笔画（按 `seq` 增量下发；`points` 为空表示「清屏」） |

---

## 几个设计上的取舍

**为什么不用 DRF**：接口都是简单 JSON 接口，纯 Django 够了；而且当前 Django 6.1 比较新，第三方包的兼容性没保证。零依赖也意味着部署时不会踩版本坑。

**为什么没有用 Django 自带的 `auth.User`**：小程序用户本质上只是「一个 openid」，没有密码、没有后台登录需求，单独一套轻量模型更直接。想在 admin 后台看数据的话，照常 `admin.site.register` 即可。

**为什么 `GET /api/games` 返回空数组**：游戏规则文案（介绍 / 角色 / 分节规则 / 小贴士 / 每个版型的规则与补充说明）是静态内容，留在小程序里就行，没必要每次开局都拉一遍；小程序拿到空数组会自动回落本地那份数据（`utils/games.js`）。后端自己用 `party/constants.py` 的 `GAMES` 做人数校验。

**三个游戏的隐藏信息都不整包下发**：`presenters.room_to_dict()` 按 `room.game_id` 调对应的 `public_state(game, me)`，只放**请求者自己**看得到的东西 ——

- 谁是卧底：别人的条目里连 `word` / `role` 字段都不存在；投票只暴露「投没投」，不暴露投给谁（票型只在结算后的 `roundResult` 里公开）。`tests.py` 有专门的断言守着。
- 狼人杀：身份只在「是自己 / 是狼队友 / 整局已结束」时才带 `roleId`；预言家的查验在 `myChecks`、摄梦人的读心在 `myDream` 里，各人只看得到自己的；**白天出局的人不公布身份**（`roundResult.outRoleId` 只在 `phase === 'over'` 时才下发）；夜晚只暴露「现在轮到哪个角色」，不泄漏别人选了什么。`tests_werewolf.py` / `tests_survivor.py` 守着这些。
- 你画我猜：**答案只给画手、已经猜对的人，以及本轮公布之后**（其他人只看到字数和词类）；候选词只给画手；猜对的那条不写进猜测流水（否则等于把答案告诉别人）。`tests_draw.py` 守着这些。
