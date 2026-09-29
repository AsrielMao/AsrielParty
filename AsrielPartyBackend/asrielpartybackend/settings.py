"""
Django settings for asrielpartybackend project.

配置说明：
  - 微信的 AppID / AppSecret 不写死在代码里，从环境变量读。
  - 本地开发没配 AppSecret 时会自动降级（假 openid + 假语音签名），
    这样不用真机、不用连微信服务器就能把整个流程跑通。
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def env(key, default=''):
    return os.environ.get(key, default)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env(
    'DJANGO_SECRET_KEY',
    'django-insecure-z&t2qrs-t_!*u!*8a@-#upc=l(j8u^&^d$zb383pj%4&cdb0u&'
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DJANGO_DEBUG', '1') == '1'

# 本地调试够用。上服务器时把域名 / IP 放进环境变量 DJANGO_ALLOWED_HOSTS（逗号分隔）
ALLOWED_HOSTS = [h for h in env('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if h]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'party',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'asrielpartybackend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'asrielpartybackend.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation（本项目的小程序用户没有密码，保留默认配置即可）

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = 'static/'

# 头像上传后存这里
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Email

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ---------------------------------------------------------------------------
# 微信小程序
# ---------------------------------------------------------------------------

# 小程序 AppID（公开信息，写默认值没关系）
WECHAT_APPID = env('WECHAT_APPID', 'wx0300ac9bfc690c14')

# 小程序 AppSecret：**不要提交到仓库**。本地放环境变量，服务器上放 systemd 环境或 .env
WECHAT_APPSECRET = env('WECHAT_APPSECRET', '')

# 没配 AppSecret 时走开发期降级：
#   - 登录：不请求微信，直接用 code 派生一个稳定的假 openid
#   - 语音签名：返回假签名（真机上 wx.joinVoIPChat 仍会失败，需要真 AppSecret）
# 配好 AppSecret 后自动走真实的微信接口，无需改代码。
WECHAT_DEV_FALLBACK = not WECHAT_APPSECRET
