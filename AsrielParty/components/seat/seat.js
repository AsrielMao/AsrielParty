/**
 * 房间内的一个座位。
 *
 * 组件只负责「长什么样」，位置由父页面通过 pos（百分比坐标）传进来，
 * 这样房间页可以自由地排成圆形、弧形或网格，不需要和组件耦合。
 */

Component({
  properties: {
    /** 成员对象（api.decorateRoom 产出的结构），null 表示空位 */
    member: {
      type: Object,
      value: null
    },
    /** 座位位置，百分比 { left, top }，相对座位环容器 */
    pos: {
      type: Object,
      value: { left: 50, top: 50 }
    },
    /** 座位序号，从 0 开始 */
    seatIndex: {
      type: Number,
      value: 0
    },
    /** 是否正在说话（实时语音状态） */
    speaking: {
      type: Boolean,
      value: false
    }
  },

  data: {
    avatarFailed: false
  },

  observers: {
    'member.avatar': function () {
      // 换人/换头像后重新给一次加载机会
      this.setData({ avatarFailed: false })
    }
  },

  methods: {
    onTap: function () {
      this.triggerEvent('tap', {
        member: this.data.member,
        seatIndex: this.data.seatIndex
      })
    },

    onAvatarError: function () {
      // 头像加载失败（临时链接过期等）时退回昵称首字，不要留一块空白
      this.setData({ avatarFailed: true })
    }
  }
})
