/**
 * 沉浸式自定义导航栏。
 *
 * 页面全局 navigationStyle 是 custom，所以每页顶部都要自己画导航栏。
 * 本组件负责：
 *   - 按状态栏高度 + 微信胶囊按钮位置算出精确高度（不同机型不会错位）
 *   - fixed 定位时自动输出占位，页面内容不会被盖住
 *   - 深色半透明背景 + 毛玻璃，配合整体派对风格
 */

Component({
  options: {
    multipleSlots: true
  },

  properties: {
    title: {
      type: String,
      value: ''
    },
    /** 是否显示返回按钮 */
    back: {
      type: Boolean,
      value: true
    },
    /** 是否固定在顶部（fixed 时组件会额外输出一个等高占位块） */
    fixed: {
      type: Boolean,
      value: true
    },
    /** 无背景无边框（首页那种沉浸式头部用） */
    transparent: {
      type: Boolean,
      value: false
    },
    extClass: {
      type: String,
      value: ''
    }
  },

  data: {
    statusBarHeight: 20,
    navBarHeight: 44,
    placeholderHeight: 64
  },

  lifetimes: {
    attached: function () {
      this.measure()
    }
  },

  methods: {
    measure: function () {
      let statusBarHeight = 20
      let navBarHeight = 44

      try {
        const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
        statusBarHeight = info.statusBarHeight || 20
      } catch (e) {
        console.warn('[nav-bar] 获取窗口信息失败', e)
      }

      try {
        const rect = wx.getMenuButtonBoundingClientRect()
        if (rect && rect.height && rect.top) {
          // 胶囊按钮上下留白对称，导航栏内容区高度 = 胶囊高度 + 上下留白
          const gap = Math.max(0, rect.top - statusBarHeight)
          navBarHeight = rect.height + gap * 2
        }
      } catch (e) {
        console.warn('[nav-bar] 获取胶囊位置失败', e)
      }

      this.setData({
        statusBarHeight: statusBarHeight,
        navBarHeight: navBarHeight,
        placeholderHeight: this.data.fixed ? statusBarHeight + navBarHeight : 0
      })
    },

    onBack: function () {
      this.triggerEvent('back', {})
      const pages = getCurrentPages()
      if (pages.length > 1) {
        wx.navigateBack({ delta: 1 })
      } else {
        // 直接进入的页面（比如分享卡片打开）没有上一页，回首页
        wx.reLaunch({ url: '/pages/index/index' })
      }
    }
  }
})
