"""
请求处理的小工具：登录态、JSON 解析、统一错误格式。

小程序侧所有请求都带 `Authorization: Bearer <token>`；
所有失败都返回 `{ "detail": "中文错误信息" }`，小程序会直接把它 toast 出来。
"""

import json
import logging
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import User
from .wechat import WeChatError

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务错误。抛出后由 api_view 统一转成 { detail } 的 JSON。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def ok(data=None, status=200):
    return JsonResponse(
        data if data is not None else {},
        status=status,
        json_dumps_params={'ensure_ascii': False},
        safe=not isinstance(data, list),
    )


def current_user(request):
    """从请求头里取 token 对应的用户，取不到返回 None。"""
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None
    token = header[7:].strip()
    if not token:
        return None
    return User.objects.filter(token=token).first()


def require_user(request):
    user = current_user(request)
    if user is None:
        raise ApiError('尚未登录', status=401)
    return user


def payload(request):
    """
    解析请求参数。
    GET 走 query string，其余走 JSON body —— 与小程序 utils/request.js 的发送方式对应。
    """
    if request.method == 'GET':
        return request.GET.dict()

    if not request.body:
        return {}

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        raise ApiError('请求体不是合法的 JSON')

    if not isinstance(data, dict):
        raise ApiError('请求体必须是 JSON 对象')

    return data


def api_view(methods):
    """
    视图装饰器：
      - 校验 HTTP 方法
      - 免 CSRF（小程序不带 CSRF token）
      - 把 ApiError / WeChatError 转成 { detail }，其余异常兜底成 500 中文提示
        （不然小程序会收到一坨 HTML 报错页）
    """
    def decorator(fn):
        @csrf_exempt
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return ok({'detail': '不支持的请求方法：%s' % request.method}, status=405)
            try:
                result = fn(request, *args, **kwargs)
                if isinstance(result, JsonResponse):
                    return result
                return ok(result)
            except ApiError as exc:
                return ok({'detail': exc.message}, status=exc.status)
            except WeChatError as exc:
                return ok({'detail': str(exc)}, status=400)
            except Exception as exc:  # noqa: BLE001  兜底：别把 HTML 报错页丢给小程序
                logger.exception('未处理的异常')
                return ok({'detail': '服务器开小差了：%s' % exc}, status=500)

        # 记下来，根路径那个状态页要靠它列出接口清单（见 views.index）
        wrapper.allowed_methods = tuple(methods)
        return wrapper
    return decorator
