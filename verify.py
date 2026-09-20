import json
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from encrypt import encrypt_password
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
VPN_SSO_BASE_URL = (
    "https://webvpn.bit.edu.cn/https/"
    "77726476706e69737468656265737421e3e44ed225397c1e7b0c9ce29b5b"
)
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


def _cas_login_url(mode):
    return DIRECT_CAS_LOGIN_URL if mode == MODE_DIRECT else f"{VPN_SSO_BASE_URL}/cas/login"


def _sso_base_url(mode):
    return "https://sso.bit.edu.cn" if mode == MODE_DIRECT else VPN_SSO_BASE_URL


def _sso_query_suffix(mode):
    return "" if mode == MODE_DIRECT else "?vpn-12-o2-sso.bit.edu.cn"


def _fingerprint_url(mode):
    if mode == MODE_DIRECT:
        return "https://sso.bit.edu.cn/ustc-rba-front/fp"
    return f"{VPN_SSO_BASE_URL}/ustc-rba-front/fp?vpn-12-o2-sso.bit.edu.cn"


def _register_url(mode):
    return f"{course_base_url(mode)}/student/register.do"


def _new_session(cookies=None):
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})
    if cookies:
        session.cookies.update(cookies)
    return session


def _page_value(soup, element_id, default=None):
    element = soup.find(id=element_id)
    if element is None:
        if default is not None:
            return default
        raise ValueError(f"登录页缺少字段：{element_id}")
    value = element.get("value")
    if value is None:
        value = element.get_text()
    value = value.strip()
    if not value and default is None:
        raise ValueError(f"登录页字段为空：{element_id}")
    return value or default


def _get_risk_token(session, mode):
    fingerprint = {
        "fonts": "error",
        "deviceMemory": "error",
        "hardwareConcurrency": "error",
        "localgroupId": "error",
        "timezone": "error",
        "cpuClass": "error",
        "platform": "error",
        "language": "error",
        "screenResolution": "error",
        "fingerprint": "error",
        "cookieValue": "error",
        "userAgent": USER_AGENT,
        "platformAuthenticator": "error",
    }
    response = session.post(
        _fingerprint_url(mode), json=fingerprint, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    token = response.json().get("responsetoken")
    if not token:
        raise ValueError("风控接口未返回 responsetoken")
    return token


def _extract_login_key(url):
    return parse_qs(urlparse(url).query).get("bitXsxkLogin", [None])[0]


def _register(session, mode, login_key):
    response = session.get(
        _register_url(mode),
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


def _form_data(form):
    data = {}
    for element in form.find_all(["input", "button"]):
        name = element.get("name")
        if name:
            data[name] = element.get("value", "")
    return data


def _complete_sms_verification(session, login_response, mode):
    soup = BeautifulSoup(login_response.text, "html.parser")
    sms_form = soup.find(id="secondSmsLoginForm")
    if sms_form is None:
        return None

    phone = _page_value(soup, "phone-number")
    send_response = session.post(
        f"{_sso_base_url(mode)}/cas/api/protected/sms/publicNoToken/sendSmsCode"
        f"{_sso_query_suffix(mode)}",
        json={"phone": phone, "businessNo": "0008"},
        timeout=REQUEST_TIMEOUT,
    )
    send_response.raise_for_status()
    send_data = send_response.json()
    if str(send_data.get("code")) != "200":
        message = (send_data.get("data") or {}).get("errorMessage")
        raise ValueError(message or "短信验证码发送失败")

    print(time_msg("统一身份认证已发送短信验证码"))
    while True:
        sms_code = input(time_msg("短信验证码：")).strip()
        if len(sms_code) != 6 or not sms_code.isdigit():
            print(time_msg("请输入 6 位数字验证码"))
            continue
        check_response = session.post(
            f"{_sso_base_url(mode)}/cas/api/protected/sms/checkToken"
            f"{_sso_query_suffix(mode)}",
            json={
                "phone": phone,
                "token": sms_code,
                "delete": False,
                "trustDevice": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        check_response.raise_for_status()
        check_data = check_response.json()
        if str(check_data.get("code")) == "200":
            break
        message = (check_data.get("data") or {}).get("errorMessage")
        print(time_msg(message or "短信验证码错误，请重试"))

    data = _form_data(sms_form)
    data.update(
        {
            "password": sms_code,
            "type": "smsLogin",
            "_eventId": "submit",
            "geolocation": "",
            "trustDevice": "true",
        }
    )
    response = session.post(
        _cas_login_url(mode),
        data=data,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def verify(sid=None, password=None, mode=MODE_DIRECT, cookies=None):
    session = _new_session(cookies)
    try:
        init = session.get(
            _cas_login_url(mode),
            params={"service": COURSE_LOGIN_URL},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        init.raise_for_status()

        login_key = _extract_login_key(init.url)
        if login_key:
            print(time_msg("已使用本地会话通过统一身份认证"))
            return _register(session, mode, login_key)
        if not sid or password is None:
            return None

        soup = BeautifulSoup(init.text, "html.parser")
        salt = _page_value(soup, "login-croypto")
        execution = _page_value(soup, "login-page-flowkey")
        risk_engine = _page_value(soup, "riskSystemSwitch", "USTC")
        site_id = _page_value(soup, "siteId", "sourceId")
        target_system = _page_value(soup, "targetSystem", "sso")
        risk_token = _get_risk_token(session, mode)
        risk_data = json.dumps(
            {"token": risk_token, "groupId": ""},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        data = {
            "username": sid,
            "type": "UsernamePassword",
            "_eventId": "submit",
            "geolocation": "",
            "execution": execution,
            "captcha_code": "",
            "croypto": salt,
            "password": encrypt_password(password, salt),
            "captcha_payload": encrypt_password("{}", salt),
            "risk_payload": encrypt_password(risk_data, salt),
            "targetSystem": target_system,
            "siteId": site_id,
            "riskEngine": risk_engine,
        }
        login = session.post(
            init.url,
            data=data,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        login.raise_for_status()

        login_key = _extract_login_key(login.url)
        if not login_key:
            login = _complete_sms_verification(session, login, mode)
            login_key = _extract_login_key(login.url) if login else None
        if not login_key:
            print(time_msg("身份认证失败，请检查账号密码或额外验证要求"))
            return None

        print(time_msg("统一身份认证成功！"))
        return _register(session, mode, login_key)
    except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as error:
        print(time_msg(f"登录失败：{error}"))
        return None
