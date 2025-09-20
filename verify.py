import requests
from bs4 import BeautifulSoup
from encrypt import encrypt_password
from urllib.parse import urlencode
from urllib.parse import parse_qs, urlparse
def verify(sid,pwd0,env):
    if env == 0:
        url = 'https://sso.bit.edu.cn/cas/login?service=https:%2F%2Fxk.bit.edu.cn%2Fxsxkapp%2Fsys%2Fxsxkapp%2FbitXsxkLogin%2FcasLogin.do'
    else:
        url = "https://webvpn.bit.edu.cn/https/77726476706e69737468656265737421e3e44ed225397c1e7b0c9ce29b5b/cas/login?service=https:%2F%2Fxk.bit.edu.cn%2Fxsxkapp%2Fsys%2Fxsxkapp%2FbitXsxkLogin%2FcasLogin.do"
    headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0', 'Content-Type':'application/x-www-form-urlencoded'}
    try:
        cookies = {}
        init = requests.get(url, headers=headers)
        for cookie in init.cookies:
            if cookie.path=='/' or cookie.path.strip('/') == 'cas':
                 cookies[cookie.name] = cookie.value
        soup = BeautifulSoup(init.text, 'html.parser')
        salt = soup.find('p', id="login-croypto").get_text()
        execution = soup.find('p', id="login-page-flowkey").get_text()
        pwd = encrypt_password(pwd0, salt)
        data = {
            "username": sid,
            "password": pwd,
            "execution": execution,
            'captcha_code': '',
            "_eventId": "submit",
            "type": "UsernamePassword",
            'geolocation': '',
            'croypto': salt,
            'Captcha_payload': '',
        }
        login=requests.post(url,headers=headers,cookies=cookies,data=urlencode(data))
        if login.status_code==200:
            print('统一身份认证成功！')
            if env == 0:
                cookies.clear()
            if login.history:
                for resp in login.history:
                    for cookie in resp.cookies:
                        if cookie.path=='/' or cookie.path.strip('/') == 'xsxkapp':
                            cookies[cookie.name] = cookie.value
            cookies.update(login.cookies.get_dict())
            params = parse_qs(urlparse(login.url).query)
            key = params['bitXsxkLogin'][0]
            if env == 0:
                reg_url ='https://xk.bit.edu.cn/xsxkapp/sys/xsxkapp/student/register.do?number={:s}'.format(key)
            else:
                reg_url = 'https://webvpn.bit.edu.cn/https/77726476706e69737468656265737421e8fc0f9e2e2426557a1dc7af96/xsxkapp/sys/xsxkapp/student/register.do?vpn-12-o2-xk.bit.edu.cn&number={:s}'.format(key)
            reg = requests.get(reg_url,headers=headers,cookies=cookies)
            cookies.update(reg.cookies.get_dict())
            name = reg.json()['data']['name']
            token = reg.json()['data']['token']
            print('选课网站登录成功，当前登录用户：{:s}'.format(name))
            return token,cookies
        elif login.status_code == 401:
            print('身份认证失败！请检查账号密码是否错误')
            return None
        else:
            print('网络错误，请重试！')
            return None
    except:
        print('网络错误，请重试！')
        return None
