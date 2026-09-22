import json
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES

from sso_client import BitSsoClient, SsoError
from time_msg import time_msg


MODE_DIRECT = "direct"
MODE_WEBVPN = "webvpn"
DIRECT_COURSE_BASE_URL = "https://xk.bit.edu.cn/xsxkapp/sys/xsxkapp"
VPN_COURSE_BASE_URL = (
    "https://webvpn.bit.edu.cn/https/"
    "77726476706e69737468656265737421e8fc0f9e2e2426557a1dc7af96"
    "/xsxkapp/sys/xsxkapp"
)
DIRECT_CAS_LOGIN_URL = "https://sso.bit.edu.cn/cas/login"
WEBVPN_LOGIN_URL = "https://webvpn.bit.edu.cn/login?cas_login=true"
COURSE_LOGIN_URL = f"{DIRECT_COURSE_BASE_URL}/bitXsxkLogin/casLogin.do"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def course_base_url(mode):
    if mode == MODE_DIRECT:
        return DIRECT_COURSE_BASE_URL
    if mode == MODE_WEBVPN:
        return VPN_COURSE_BASE_URL
    raise ValueError(f"未知网络模式：{mode}")


def _new_session(cookies=None):
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})
    if cookies:
        session.cookies.update(cookies)
    return session


def _extract_login_key(url):
    return parse_qs(urlparse(url).query).get("bitXsxkLogin", [None])[0]


def _extract_login_key_from_response(response):
    candidates = list(getattr(response, "history", ()) or ()) + [response]
    for item in candidates:
        urls = [str(getattr(item, "url", "") or "")]
        headers = getattr(item, "headers", {}) or {}
        location = headers.get("Location")
        if location:
            urls.append(urljoin(urls[0], str(location)))
        for url in urls:
            key = _extract_login_key(url)
            if key:
                return key
    return None


def _register(session, mode, login_key):
    response = session.get(
        f"{course_base_url(mode)}/student/register.do",
        params={"number": login_key},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("code")) != "1" or not data.get("data"):
        raise ValueError(data.get("msg") or "选课网站注册登录失败")
    token = data["data"].get("token")
    name = data["data"].get("name", "")
    if not token:
        raise ValueError("选课网站未返回 token")
    print(time_msg(f"选课网站登录成功，当前登录用户：{name}"))
    return token, session.cookies.copy()


def _encode_vpn_host(host):
    """WebVPN 使用的 AES-反馈编码，和 BIT-Login-Python 保持一致。"""
    key = b"wrdvpnisthebest!"
    iv = b"wrdvpnisthebest!"
    text_len = len(host)
    padded = host + "0" * ((16 - text_len % 16) % 16)
    cipher = AES.new(key, AES.MODE_ECB)
    feedback = bytearray(iv)
    output = bytearray()
    for offset in range(0, len(padded), 16):
        stream = cipher.encrypt(bytes(feedback))
        block = bytes(
            ord(padded[offset + index]) ^ stream[index]
            for index in range(min(16, len(padded) - offset))
        )
        output.extend(block)
        feedback = bytearray(block)
    return iv.hex() + bytes(output).hex()[: text_len * 2]


def _convert_to_webvpn_url(original_url):
    parsed = urlparse(original_url)
    if not parsed.hostname:
        return original_url
    encoded_host = _encode_vpn_host(parsed.hostname)
    path = f"/{parsed.scheme}/{encoded_host}{parsed.path}"
    return urlunparse(
        (
            "https",
            "webvpn.bit.edu.cn",
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _sms_callback(masked_phone):
    print(time_msg("统一身份认证已发送短信验证码"))
    while True:
        code = input(time_msg(f"短信验证码（{masked_phone or '绑定手机'}）：")).strip()
        if len(code) == 6 and code.isdigit():
            return code
        print(time_msg("请输入 6 位数字验证码"))


def _failure_message(response):
    if response is None:
        return "没有收到统一认证响应"
    soup = BeautifulSoup(response.text or "", "html.parser")
    for element_id in ("login-error-msg", "login-error-code", "errorMsg"):
        element = soup.find(id=element_id)
        if element is not None:
            message = element.get_text(" ", strip=True)
            if message:
                return message
    if soup.find(id="phone-number") is not None:
        return "检测到短信二次认证页面，但认证没有完成"
    if soup.find(id="login-croypto") is not None:
        return "统一身份认证仍停留在登录页，可能是密码错误、风控拦截或需要验证码"
    title = soup.title.get_text(" ", strip=True) if soup.title else "未知页面"
    return f"未识别统一身份认证返回页面：{title}"


def _probe_existing_session(session, mode):
    if mode == MODE_DIRECT:
        return session.get(
            DIRECT_CAS_LOGIN_URL,
            params={"service": COURSE_LOGIN_URL},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    return session.get(
        f"{VPN_COURSE_BASE_URL}/*default/index.do",
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )


def _login_direct(session, sid, password):
    client = BitSsoClient(session, timeout=REQUEST_TIMEOUT)
    return client.login_password(
        sid,
        password,
        service=COURSE_LOGIN_URL,
        sms_callback=_sms_callback,
        trust_device=False,
        follow_redirects=True,
    )


def _login_webvpn(session, sid, password):
    """先拿业务 ticket，再建立 WebVPN 网关会话，最后访问包装后的回调。"""
    client = BitSsoClient(session, timeout=REQUEST_TIMEOUT)
    target_response = client.login_password(
        sid,
        password,
        service=COURSE_LOGIN_URL,
        sms_callback=_sms_callback,
        trust_device=False,
        follow_redirects=False,
    )
    target_callback = client.callback_from_response(target_response, COURSE_LOGIN_URL)

    gateway_response = client.login_password(
        sid,
        password,
        service=WEBVPN_LOGIN_URL,
        sms_callback=_sms_callback,
        trust_device=False,
        follow_redirects=False,
    )
    gateway_callback = client.callback_from_response(gateway_response, WEBVPN_LOGIN_URL)
    session.get(gateway_callback, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    return session.get(
        _convert_to_webvpn_url(target_callback),
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )


def verify(sid=None, password=None, mode=MODE_DIRECT, cookies=None):
    session = _new_session(cookies)
    stage = "初始化统一身份认证"
    login_response = None
    try:
        init = _probe_existing_session(session, mode)
        login_key = _extract_login_key_from_response(init)
        if login_key:
            print(time_msg("已使用本地会话通过统一身份认证"))
            return _register(session, mode, login_key)
        if not sid or password is None:
            return None

        stage = "提交统一身份认证账号密码"
        if mode == MODE_DIRECT:
            login_response = _login_direct(session, sid, password)
        elif mode == MODE_WEBVPN:
            login_response = _login_webvpn(session, sid, password)
        else:
            raise ValueError(f"未知网络模式：{mode}")

        login_key = _extract_login_key_from_response(login_response)
        if not login_key:
            print(time_msg("身份认证失败：" + _failure_message(login_response)))
            return None

        print(time_msg("统一身份认证成功！"))
        stage = "注册选课系统登录状态"
        return _register(session, mode, login_key)
    except (requests.RequestException, SsoError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(time_msg(f"{stage}失败：{error}"))
        return None
