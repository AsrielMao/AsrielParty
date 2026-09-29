"""
你画我猜的词库。

每条 = 词 + 分类。分类会作为提示显示给猜的人（"动物 · 3 个字"），
所以**分类要写得准**，不然提示反而误导。

加词就往 `WORDS` 里加一行，不用改任何逻辑 ——
每轮开局会用 `pick_options()` 从里面随机抽几个给画手挑。
选词的原则：**画得出来**。抽象词（"自由""思念"）不要往里放。
"""

import random

# 按分类排在一起，方便维护（查找时不影响，抽词是全局随机的）
WORDS = [
    # ---- 动物 ----
    {'word': '长颈鹿', 'category': '动物'},
    {'word': '大象', 'category': '动物'},
    {'word': '企鹅', 'category': '动物'},
    {'word': '熊猫', 'category': '动物'},
    {'word': '章鱼', 'category': '动物'},
    {'word': '蜗牛', 'category': '动物'},
    {'word': '猫头鹰', 'category': '动物'},
    {'word': '袋鼠', 'category': '动物'},
    {'word': '海豚', 'category': '动物'},
    {'word': '刺猬', 'category': '动物'},
    {'word': '蝴蝶', 'category': '动物'},
    {'word': '螃蟹', 'category': '动物'},
    {'word': '鲸鱼', 'category': '动物'},
    {'word': '公鸡', 'category': '动物'},
    {'word': '骆驼', 'category': '动物'},
    {'word': '乌龟', 'category': '动物'},
    {'word': '蝙蝠', 'category': '动物'},
    {'word': '松鼠', 'category': '动物'},
    {'word': '孔雀', 'category': '动物'},
    {'word': '蚂蚁', 'category': '动物'},
    {'word': '鲨鱼', 'category': '动物'},
    {'word': '鹦鹉', 'category': '动物'},
    {'word': '青蛙', 'category': '动物'},
    {'word': '斑马', 'category': '动物'},
    {'word': '水母', 'category': '动物'},

    # ---- 食物 ----
    {'word': '火锅', 'category': '食物'},
    {'word': '西瓜', 'category': '食物'},
    {'word': '汉堡', 'category': '食物'},
    {'word': '蛋糕', 'category': '食物'},
    {'word': '冰淇淋', 'category': '食物'},
    {'word': '饺子', 'category': '食物'},
    {'word': '棒棒糖', 'category': '食物'},
    {'word': '披萨', 'category': '食物'},
    {'word': '寿司', 'category': '食物'},
    {'word': '菠萝', 'category': '食物'},
    {'word': '爆米花', 'category': '食物'},
    {'word': '甜甜圈', 'category': '食物'},
    {'word': '热狗', 'category': '食物'},
    {'word': '三明治', 'category': '食物'},
    {'word': '粽子', 'category': '食物'},
    {'word': '月饼', 'category': '食物'},
    {'word': '糖葫芦', 'category': '食物'},
    {'word': '煎蛋', 'category': '食物'},
    {'word': '拉面', 'category': '食物'},
    {'word': '棉花糖', 'category': '食物'},

    # ---- 日常用品 ----
    {'word': '雨伞', 'category': '日常用品'},
    {'word': '台灯', 'category': '日常用品'},
    {'word': '牙刷', 'category': '日常用品'},
    {'word': '眼镜', 'category': '日常用品'},
    {'word': '钥匙', 'category': '日常用品'},
    {'word': '闹钟', 'category': '日常用品'},
    {'word': '吹风机', 'category': '日常用品'},
    {'word': '水壶', 'category': '日常用品'},
    {'word': '扫帚', 'category': '日常用品'},
    {'word': '背包', 'category': '日常用品'},
    {'word': '剪刀', 'category': '日常用品'},
    {'word': '沙发', 'category': '日常用品'},
    {'word': '冰箱', 'category': '日常用品'},
    {'word': '洗衣机', 'category': '日常用品'},
    {'word': '垃圾桶', 'category': '日常用品'},
    {'word': '订书机', 'category': '日常用品'},
    {'word': '指甲刀', 'category': '日常用品'},
    {'word': '耳机', 'category': '日常用品'},
    {'word': '钱包', 'category': '日常用品'},
    {'word': '镜子', 'category': '日常用品'},

    # ---- 自然 ----
    {'word': '彩虹', 'category': '自然'},
    {'word': '闪电', 'category': '自然'},
    {'word': '龙卷风', 'category': '自然'},
    {'word': '火山', 'category': '自然'},
    {'word': '瀑布', 'category': '自然'},
    {'word': '流星', 'category': '自然'},
    {'word': '仙人掌', 'category': '自然'},
    {'word': '向日葵', 'category': '自然'},
    {'word': '枫叶', 'category': '自然'},
    {'word': '雪人', 'category': '自然'},
    {'word': '沙漠', 'category': '自然'},
    {'word': '珊瑚', 'category': '自然'},
    {'word': '蘑菇', 'category': '自然'},
    {'word': '竹子', 'category': '自然'},
    {'word': '蒲公英', 'category': '自然'},

    # ---- 运动 ----
    {'word': '篮球', 'category': '运动'},
    {'word': '跳绳', 'category': '运动'},
    {'word': '游泳', 'category': '运动'},
    {'word': '滑雪', 'category': '运动'},
    {'word': '冲浪', 'category': '运动'},
    {'word': '射箭', 'category': '运动'},
    {'word': '乒乓球', 'category': '运动'},
    {'word': '溜冰', 'category': '运动'},
    {'word': '瑜伽', 'category': '运动'},
    {'word': '拳击', 'category': '运动'},

    # ---- 职业 ----
    {'word': '医生', 'category': '职业'},
    {'word': '消防员', 'category': '职业'},
    {'word': '警察', 'category': '职业'},
    {'word': '厨师', 'category': '职业'},
    {'word': '宇航员', 'category': '职业'},
    {'word': '理发师', 'category': '职业'},
    {'word': '摄影师', 'category': '职业'},
    {'word': '农民', 'category': '职业'},
    {'word': '教师', 'category': '职业'},
    {'word': '魔术师', 'category': '职业'},

    # ---- 交通 ----
    {'word': '热气球', 'category': '交通'},
    {'word': '潜水艇', 'category': '交通'},
    {'word': '消防车', 'category': '交通'},
    {'word': '摩托车', 'category': '交通'},
    {'word': '直升机', 'category': '交通'},
    {'word': '火车', 'category': '交通'},
    {'word': '帆船', 'category': '交通'},
    {'word': '滑板', 'category': '交通'},
    {'word': '独轮车', 'category': '交通'},
    {'word': '缆车', 'category': '交通'},

    # ---- 建筑与地点 ----
    {'word': '金字塔', 'category': '建筑'},
    {'word': '长城', 'category': '建筑'},
    {'word': '灯塔', 'category': '建筑'},
    {'word': '摩天轮', 'category': '建筑'},
    {'word': '城堡', 'category': '建筑'},
    {'word': '风车', 'category': '建筑'},
    {'word': '桥梁', 'category': '建筑'},
    {'word': '帐篷', 'category': '建筑'},
    {'word': '地铁站', 'category': '建筑'},
    {'word': '游泳池', 'category': '建筑'},

    # ---- 角色与虚构形象 ----
    {'word': '圣诞老人', 'category': '角色'},
    {'word': '美人鱼', 'category': '角色'},
    {'word': '木乃伊', 'category': '角色'},
    {'word': '超人', 'category': '角色'},
    {'word': '忍者', 'category': '角色'},
    {'word': '小丑', 'category': '角色'},
    {'word': '海盗', 'category': '角色'},
    {'word': '机器人', 'category': '角色'},
    {'word': '幽灵', 'category': '角色'},

    # ---- 动作与情景 ----
    {'word': '打喷嚏', 'category': '动作'},
    {'word': '刷牙', 'category': '动作'},
    {'word': '系鞋带', 'category': '动作'},
    {'word': '放风筝', 'category': '动作'},
    {'word': '钓鱼', 'category': '动作'},
    {'word': '烤火', 'category': '动作'},
    {'word': '搬家', 'category': '动作'},
    {'word': '堵车', 'category': '动作'},
    {'word': '打哈欠', 'category': '动作'},
    {'word': '拍照', 'category': '动作'},
]

WORDS_BY_VALUE = {item['word']: item for item in WORDS}


def pick_options(count=3, exclude=()):
    """
    随机抽 count 个词给画手挑，尽量避开这一局已经用过的（exclude）。

    候选词之间**分类尽量不重复** —— 三个都是动物的话，提示就等于白给了。
    词库被掏空时允许重复，反正不能让画手没词可选。
    """
    skip = set(exclude or [])
    pool = [w for w in WORDS if w['word'] not in skip]
    if len(pool) < count:
        pool = list(WORDS)

    random.shuffle(pool)

    picked = []
    used_categories = set()
    leftovers = []

    for item in pool:
        if len(picked) >= count:
            break
        if item['category'] in used_categories:
            leftovers.append(item)
            continue
        used_categories.add(item['category'])
        picked.append(item)

    # 分类不够凑数时，用落选的补齐
    for item in leftovers:
        if len(picked) >= count:
            break
        picked.append(item)

    random.shuffle(picked)
    return picked


def category_of(word):
    item = WORDS_BY_VALUE.get(word)
    return item['category'] if item else ''
