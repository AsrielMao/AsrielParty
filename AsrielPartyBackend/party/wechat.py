"""
微信相关：code 换 openid、实时语音签名。

两件事都需要 AppSecret，而 AppSecret 只能待在服务端（放进小程序会被反编译拿走）。

本地开发没配 AppSecret 时走「开发期降级」：登录用 code 派生一个稳定的假 openid，
语音返回假签名 —— 这样不用真机、不用连微信服务器就能把整个流程跑顺。
配好 AppSecret 后自动走真实接口，代码不用改。
"""

import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request

from django.conf import settings


class WeChatError(Exception):
    """调微信接口失败。会被视图统一转成 { detail } 返回给小程序。"""


def code2session(code):
    """
    用 wx.login 的 code 换 openid。

    返回 {'openid': str, 'unionid': str}
    """
    if not code:
        raise WeChatError('缺少 code')

    if settings.WECHAT_DEV_FALLBACK:
        # 开发期：同一个 code 永远算出同一个 openid，模拟「同一个人再次登录还是他」
        digest = hashlib.sha256(('dev:' + str(code)).encode('utf-8')).hexdigest()[:28]
        return {'openid': 'dev_openid_' + digest, 'unionid': ''}

    params = urllib.parse.urlencode({
        'appid': settings.WECHAT_APPID,
        'secret': settings.WECHAT_APPSECRET,
        'js_code': code,
        'grant_type': 'authorization_code',
    })
    url = 'https://api.weixin.qq.com/sns/jscode2session?' + params

    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise WeChatError('请求微信登录接口失败：%s' % exc)

    if body.get('errcode'):
        raise WeChatError('微信登录失败：%s' % body.get('errmsg', '未知错误'))
    if not body.get('openid'):
        raise WeChatError('微信没有返回 openid')

    return {
        'openid': body['openid'],
        'unionid': body.get('unionid', '') or '',
    }


def voip_signature(group_id):
    """
    生成 wx.joinVoIPChat 需要的签名。

    官方算法：appId、groupId、nonceStr、timeStamp 四个值按字典序排序后拼成一个字符串，
    用 AppSecret 做 HMAC-SHA256，结果取十六进制。

    返回 {'groupId', 'signature', 'nonceStr', 'timeStamp'}
    """
    nonce = secrets.token_hex(16)
    timestamp = int(time.time())

    if settings.WECHAT_DEV_FALLBACK:
        # 开发期假签名：真机上微信会拒绝，但接口形状与真实一致，方便先跑通流程
        return {
            'mock': True,
            'groupId': group_id,
            'signature': 'dev_signature',
            'nonceStr': nonce,
            'timeStamp': timestamp,
        }

    raw = ''.join(sorted([settings.WECHAT_APPID, group_id, nonce, str(timestamp)]))
    signature = hmac.new(
        settings.WECHAT_APPSECRET.encode('utf-8'),
        raw.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    return {
        'groupId': group_id,
        'signature': signature,
        'nonceStr': nonce,
        'timeStamp': timestamp,
    }
