import requests
import time
from getpass import getpass
from verify import verify
from urllib.parse import urlencode
from sys import exit


pre = ['https://xk.bit.edu.cn/',
       'https://webvpn.bit.edu.cn/https/77726476706e69737468656265737421e8fc0f9e2e2426557a1dc7af96/']


def check_net():
    try:
        requests.get("https://xk.bit.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do")
        print('检测到当前为校内环境')
        return 0
    except:
        try:
            requests.get(
                "https://webvpn.bit.edu.cn/https/77726476706e69737468656265737421e8fc0f9e2e2426557a1dc7af96/xsxkapp/sys/xsxkapp/*default/index.do")
            print('检测到当前为校外环境')
            return 1
        except:
            print('当前未连接网络，程序已退出')
            exit(0)


def get_info(headers, cookies, env, user):
    time_stamp = str(int(time.time() * 1000))
    info_url = pre[env] + "xsxkapp/sys/xsxkapp/student/{:s}.do".format(user)
    info = requests.get(info_url, headers=headers, params=urlencode({'timestamp': time_stamp}), cookies=cookies)
    data = info.json()
    name = data['data']['name']
    grade = data['data']['grade']
    campus = data['data']['campusName']
    collage = data['data']['collegeName']
    department = data['data']['departmentName']
    school_class = data['data']['schoolClass']
    print('个人信息如下：')
    print('姓名：' + name)
    print('学号：' + user)
    print('年级：' + grade)
    print('校区：' + campus)
    print('学院：' + collage)
    print('专业：' + department)
    print('班级：' + school_class)
    for batch in data['data']['electiveBatchList']:
        if batch['canSelect'] == '1':
            batch_code = batch['code']
            print('当前阶段：' + batch['schoolTermName'] + ' ' + batch['name'])
            print('开始时间：' + batch['beginTime'])
            print('结束时间：' + batch['endTime'])
            return batch_code


def query_course(env, headers, cookies, search_name, batch_code, user):
    if search_name.startswith('体育'):
        url = pre[env] + 'xsxkapp/sys/xsxkapp/elective/programCourse.do'
        param = "querySetting={'data':{'studentCode':'%s','campus':'2','electiveBatchCode':'%s','isMajor':'1','teachingClassType':'TYKC','checkConflict':'0','checkCapacity':'2','queryContent':'%s'},'pageSize':'10','pageNumber':'0','order':''}" % (
            user, batch_code, search_name)
    else:
        url = pre[env] + 'xsxkapp/sys/xsxkapp/elective/publicCourse.do'
        param = "querySetting={'data':{'studentCode':'%s','campus':'2','electiveBatchCode':'%s','isMajor':'1','teachingClassType':'XGXK','checkConflict':'0','checkCapacity':'2','queryContent':'%s'},'pageSize':'10','pageNumber':'0','order':''}" % (
            user, batch_code, search_name)
    res = requests.post(url, headers=headers, cookies=cookies, params=param)
    if not res.json()['dataList']:
        return []
    idx = []
    for data in res.json()['dataList']:
        if search_name.startswith('体育'):
            for session in data['tcList']:
                idx.append([session['teachingClassID'], data['courseName']])
        else:
            idx.append([data['teachingClassID'], data['courseName']])
    return idx


def first_query(env, headers, cookies, batch_code, user):
    try:
        course_text = open('courses.txt', 'r', encoding='utf-8').readlines()
    except FileNotFoundError:
        print('course.txt 文件未找到！')
        exit(0)
    course_list = {}
    for search_name in course_text:
        search_name = search_name.strip()
        idx = query_course(env, headers, cookies, search_name, batch_code, user)
        for i in idx:
            course_list[i[0]] = i[1]
    print('当前不冲突的课程：')
    for id, name in course_list.items():
        print(id, name)
    return course_list


def choose_course(env, headers, cookies, batch_code, user, id, name):
    if name.startswith('体育'):
        query_url = pre[env] + 'xsxkapp/sys/xsxkapp/elective/programCourse.do'
        param = "querySetting={'data':{'studentCode':'%s','campus':'2','electiveBatchCode':'%s','isMajor':'1','teachingClassType':'TYKC','checkConflict':'0','checkCapacity':'0','queryContent':'%s'},'pageSize':'10','pageNumber':'0','order':''}" % (
            user, batch_code, name)
        addparam = "addParam={'data':{'operationType':'1','studentCode':'%s','electiveBatchCode':'%s','teachingClassId':'%s','isMajor':'1','campus':'2','teachingClassType':'TYKC'}}" % (
            user, batch_code, id)
    else:
        query_url = pre[env] + 'xsxkapp/sys/xsxkapp/elective/publicCourse.do'
        param = "querySetting={'data':{'studentCode':'%s','campus':'2','electiveBatchCode':'%s','isMajor':'1','teachingClassType':'XGXK','checkConflict':'0','checkCapacity':'0','queryContent':'%s'},'pageSize':'10','pageNumber':'0','order':''}" % (
            user, batch_code, name)
        addparam = "addParam={'data':{'operationType':'1','studentCode':'%s','electiveBatchCode':'%s','teachingClassId':'%s','isMajor':'1','campus':'2','teachingClassType':'XGXK'}}" % (
            user, batch_code, id)
    res = requests.post(query_url, headers=headers, cookies=cookies, params=param)
    if res.json()['dataList']:
        choose_url = pre[env] + 'xsxkapp/sys/xsxkapp/elective/volunteer.do'
        choose = requests.post(choose_url, headers=headers, cookies=cookies, params=addparam)
        if '成功' in choose.json()['msg']:
            print('已选中 [{:s}] {:s} 课程'.format(id, name))


def main():
    print("----- BIT 抢课系统 -----")
    print("请将意愿课程名加入同目录下 course.txt 文件（支持关键字搜索）")
    env = check_net()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0',
        'Content-Type': 'application/json'}
    cookies = {}
    while True:
        user = input('学号：')
        pwd0 = getpass('密码：')
        res = verify(user, pwd0, env)
        if res:
            token, cookies = res
            headers['Token'] = token
            break
    first = True
    course_list = {}
    while True:
        try:
            if first:
                batch_code = get_info(headers, cookies, env, user)
                course_list = first_query(env, headers, cookies, batch_code, user)
                flag = input('是否开始抢课？(y/n)')
                if flag != 'y' and flag != 'Y':
                    break
            else:
                token, cookies = verify(user, pwd0, env)
                headers['Token'] = token
            print('正在查询课程中...请保持程序运行（按Ctrl+C退出）')
            while True:
                for id, name in course_list.items():
                    choose_course(env, headers, cookies, batch_code, user, id, name)
        except KeyboardInterrupt:
            print('已停止查询课程')
            exit(0)
        except:
            print('尝试重新登录中...')
            first = False
            pass


if __name__ == '__main__':
    main()
