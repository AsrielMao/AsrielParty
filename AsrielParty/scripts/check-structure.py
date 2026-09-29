# -*- coding: utf-8 -*-
"""
工程结构自检：页面注册、组件引用、事件绑定、JSON 合法性、JS 括号配平。

用法（在 AsrielParty 目录下）：
    python scripts/check-structure.py

改动页面/组件之后跑一下，能挡住「新增页面忘了在 app.json 注册」
「wxml 绑定了不存在的事件处理函数」「组件路径写错」这类低级错误。
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 脚本位于 <项目>/scripts/ 下，所以项目根是上一级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

errors = []
warnings = []


def rel(p):
    return os.path.relpath(p, ROOT).replace('\\', '/')


def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


BUILTIN = set('''view scroll-view swiper swiper-item movable-area movable-view cover-view cover-image
icon text rich-text progress button checkbox checkbox-group form input label picker picker-view
picker-view-column radio radio-group slider switch textarea navigator audio image video camera
live-player live-pusher map canvas open-data web-view ad official-account slot block template
import include wxs page-meta navigation-bar page-container share-element keyboard-accessory
match-media functional-page-navigator login editor root-portal sticky-section sticky-header
grid-view list-view snapshot double-tap-gesture-handler tap-gesture-handler pan-gesture-handler
scale-gesture-handler vertical-drag-gesture-handler horizontal-drag-gesture-handler voip-room
channel-live channel-video aria-component'''.split())


def walk(suffix):
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ('node_modules', '.git', 'scripts')]
        for fn in filenames:
            if fn.endswith(suffix):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


# ---------- 1. JSON 合法性 ----------
parsed = {}
for p in walk('.json'):
    try:
        parsed[p] = json.loads(read(p))
    except Exception as e:
        errors.append('JSON 解析失败 %s: %s' % (rel(p), e))

# ---------- 2. app.json 页面完整性 ----------
pages = []
app_path = os.path.join(ROOT, 'app.json')
if app_path not in parsed:
    errors.append('找不到或无法解析 app.json')
else:
    pages = parsed[app_path].get('pages') or []
    if not pages:
        errors.append('app.json 的 pages 为空')

for page in pages:
    base = os.path.join(ROOT, page.replace('/', os.sep))
    for ext in ('.js', '.json', '.wxml', '.wxss'):
        if not os.path.exists(base + ext):
            errors.append('页面缺文件：%s%s（来自 app.json）' % (page, ext))

pages_dir = os.path.join(ROOT, 'pages')
if os.path.isdir(pages_dir):
    for name in sorted(os.listdir(pages_dir)):
        d = os.path.join(pages_dir, name)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, name + '.js')):
            if ('pages/%s/%s' % (name, name)) not in pages:
                errors.append('页面 pages/%s/%s 存在但未在 app.json 注册' % (name, name))


# ---------- 3. usingComponents 路径可解析 ----------
def resolve_comp(base_dir, comp_path):
    if comp_path.startswith('/'):
        return os.path.join(ROOT, comp_path.lstrip('/').replace('/', os.sep))
    return os.path.normpath(os.path.join(base_dir, comp_path.replace('/', os.sep)))


for p, data in parsed.items():
    uc = (data or {}).get('usingComponents')
    if not uc:
        continue
    base_dir = os.path.dirname(p)
    for tag, comp_path in uc.items():
        target = resolve_comp(base_dir, comp_path)
        for ext in ('.js', '.json', '.wxml'):
            if not os.path.exists(target + ext):
                errors.append(
                    '%s 里组件 %s 指向 %s，缺少 %s 文件' % (rel(p), tag, comp_path, ext)
                )


# ---------- 4. wxml 标签注册 + 事件绑定 ----------
def js_defined_names(js_path):
    if not os.path.exists(js_path):
        return set()
    src = read(js_path)
    names = set(
        re.findall(r'(?:^|[\s,{])([A-Za-z_$][\w$]*)\s*:\s*(?:function\b|\()', src, re.M)
    )
    names |= set(re.findall(r'\bfunction\s+([A-Za-z_$][\w$]*)\s*\(', src))
    return names


for p in walk('.json'):
    wxml_path = p[:-5] + '.wxml'
    if not os.path.exists(wxml_path):
        continue
    src = read(wxml_path)
    data = parsed.get(p) or {}
    uc = data.get('usingComponents') or {}
    js_path = p[:-5] + '.js'

    tags = set(re.findall(r'<\s*/?\s*([a-zA-Z][\w-]*)', src))
    for t in sorted(tags):
        if t in BUILTIN or t in uc:
            continue
        errors.append('%s 使用了 <%s> 但未在 usingComponents 注册' % (rel(wxml_path), t))

    methods = js_defined_names(js_path)
    handlers = re.findall(
        r'(?:bind|catch|capture-bind|capture-catch)[:\-]?[a-zA-Z]+\s*=\s*"([^"]*)"', src
    )
    for h in handlers:
        h = h.strip()
        if not h or '{' in h or '"' in h:
            continue
        if h not in methods:
            errors.append(
                '%s 绑定了 %s，但 %s 里没有这个方法' % (rel(wxml_path), h, rel(js_path))
            )


# ---------- 5. js 括号配平（粗略语法体检） ----------
def strip_js(src):
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in '"\'':
            q = c
            i += 1
            while i < n and src[i] != q:
                if src[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue
        if c == '`':
            i += 1
            while i < n and src[i] != '`':
                if src[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue
        if src.startswith('//', i):
            while i < n and src[i] != '\n':
                i += 1
            continue
        if src.startswith('/*', i):
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


for p in walk('.js'):
    s = strip_js(read(p))
    for o, c in (('{', '}'), ('(', ')'), ('[', ']')):
        if s.count(o) != s.count(c):
            errors.append(
                '%s 括号不配平：%s 出现 %d 次，%s 出现 %d 次'
                % (rel(p), o, s.count(o), c, s.count(c))
            )


# ---------- 6. WXML 反模式：wx:else / wx:elif 不能和 wx:for 写在同一标签上 ----------
# 小程序的坑：同一标签上有 wx:for 时，该节点会先被当成循环节点，
# wx:else 就找不到前面的 wx:if 兄弟节点，编译直接报
#   Bad attr `wx:else` with message: `wx:if not found, then something must be wrong`
# 正确写法是用 <block wx:else> 把带 wx:for 的节点包起来。
for p in walk('.wxml'):
    src = read(p)
    for m in re.finditer(r'<[a-zA-Z][\w-]*((?:[^>"\']|"[^"]*"|\'[^\']*\')*)>', src, re.S):
        attrs = m.group(1)
        if 'wx:for' not in attrs:
            continue
        if 'wx:else' not in attrs and 'wx:elif' not in attrs:
            continue
        line = src[: m.start()].count('\n') + 1
        errors.append(
            '%s:%d 的标签同时写了 wx:for 与 wx:else/wx:elif，'
            '请改成 <block wx:else> 包住带 wx:for 的节点' % (rel(p), line)
        )


print('扫描目录：%s' % ROOT)
print('JSON 文件 %d 个，已注册页面 %d 个' % (len(parsed), len(pages)))
print('')
if warnings:
    print('提示 %d 条：' % len(warnings))
    for w in warnings:
        print('  ! %s' % w)
    print('')
if errors:
    print('发现 %d 个问题：' % len(errors))
    for e in errors:
        print('  x %s' % e)
    sys.exit(1)
print('OK：结构自洽，未发现问题')
