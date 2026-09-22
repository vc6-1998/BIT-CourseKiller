import csv
import json
import time
from getpass import getpass
from pathlib import Path

import requests

from time_msg import time_msg
from verify import (
    DIRECT_COURSE_BASE_URL,
    MODE_DIRECT,
    MODE_WEBVPN,
    USER_AGENT,
    VPN_COURSE_BASE_URL,
    course_base_url,
    verify,
)


BASE_URL = DIRECT_COURSE_BASE_URL
CONFIG_PATH = Path(__file__).with_name("courses.txt")
SESSION_PATH = Path(__file__).with_name("session.json")
SESSION_VERSION = 1
REQUEST_TIMEOUT = 15
COURSE_SCAN_INTERVAL = 1
PROCESS_POLL_INTERVAL = 1
PROCESS_POLL_LIMIT = 10

COURSE_TYPES = {
    "TJKC": {"label": "系统推荐课程", "endpoint": "recommendedCourse.do", "nested": True},
    "XGXK": {"label": "校公选课/拓展英语", "endpoint": "publicCourse.do", "nested": False},
    "TYKC": {"label": "体育课", "endpoint": "programCourse.do", "nested": True},
}


class LoginExpiredError(RuntimeError):
    pass


def course_type_info(teaching_class_type):
    try:
        return COURSE_TYPES[teaching_class_type]
    except KeyError as error:
        raise ValueError(f"不支持的课程类型：{teaching_class_type}") from error


def create_course_session(token, cookies):
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {"User-Agent": USER_AGENT, "token": token, "language": "zh_cn"}
    )
    session.cookies.update(cookies)
    return session


def _serialize_cookies(cookies):
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "expires": cookie.expires,
        }
        for cookie in cookies
    ]


def _deserialize_cookies(items):
    jar = requests.cookies.RequestsCookieJar()
    for item in items:
        jar.set_cookie(
            requests.cookies.create_cookie(
                name=str(item["name"]),
                value=str(item["value"]),
                domain=str(item.get("domain") or ""),
                path=str(item.get("path") or "/"),
                secure=bool(item.get("secure")),
                expires=item.get("expires"),
            )
        )
    return jar


def load_saved_session(mode):
    if not SESSION_PATH.exists():
        return None
    try:
        saved = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        if saved.get("version") != SESSION_VERSION:
            return None
        entry = (saved.get("sessions") or {}).get(mode)
        if not entry or not entry.get("user") or not entry.get("token"):
            return None
        return {
            "user": str(entry["user"]),
            "token": str(entry["token"]),
            "cookies": _deserialize_cookies(entry.get("cookies") or []),
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(time_msg(f"本地会话文件无法读取，将重新登录：{error}"))
        return None


def save_login_session(mode, user, token, cookies):
    saved = {"version": SESSION_VERSION, "sessions": {}}
    if SESSION_PATH.exists():
        try:
            existing = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            if existing.get("version") == SESSION_VERSION:
                saved = existing
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    saved.setdefault("sessions", {})[mode] = {
        "user": user,
        "token": token,
        "cookies": _serialize_cookies(cookies),
        "savedAt": int(time.time()),
    }
    temporary_path = SESSION_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_path.replace(SESSION_PATH)


def response_json(response):
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("网站返回了无法识别的数据")
    if str(data.get("code")) == "302":
        raise LoginExpiredError("登录状态已失效")
    return data


def check_net():
    global BASE_URL
    session = requests.Session()
    session.trust_env = False
    for mode, label in (
        (MODE_DIRECT, "校园网直连"),
        (MODE_WEBVPN, "WebVPN 校外线路"),
    ):
        base_url = course_base_url(mode)
        try:
            response = session.get(
                f"{base_url}/*default/index.do",
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            response.raise_for_status()
            BASE_URL = base_url
            print(time_msg(f"已连接：{label}"))
            return mode
        except requests.RequestException:
            continue
    raise RuntimeError("校园网直连和 WebVPN 均无法访问")


def _session_is_valid(session, user):
    response = session.get(
        f"{BASE_URL}/student/{user}.do",
        params={"timestamp": str(int(time.time() * 1000))},
        timeout=REQUEST_TIMEOUT,
    )
    data = response_json(response).get("data")
    return isinstance(data, dict) and bool(data)


def login(mode, expected_user=None, password=None):
    saved = load_saved_session(mode)
    if saved and (expected_user is None or saved["user"] == expected_user):
        user = saved["user"]
        session = create_course_session(saved["token"], saved["cookies"])
        try:
            if _session_is_valid(session, user):
                print(time_msg("已恢复本地保存的登录会话"))
                return user, password, session
        except (LoginExpiredError, ValueError, KeyError, json.JSONDecodeError):
            pass

        resumed = verify(mode=mode, cookies=saved["cookies"])
        if resumed:
            token, cookies = resumed
            save_login_session(mode, user, token, cookies)
            return user, password, create_course_session(token, cookies)

    user = expected_user or (saved["user"] if saved else None)
    if not user:
        user = input(time_msg("学号：")).strip()
    else:
        print(time_msg(f"学号：{user}（来自本地会话）"))
    while True:
        current_password = password or getpass(time_msg("密码："))
        login_result = verify(user, current_password, mode=mode)
        if login_result:
            token, cookies = login_result
            save_login_session(mode, user, token, cookies)
            return user, current_password, create_course_session(token, cookies)
        print(time_msg("本次认证未成功，请根据上方具体阶段检查后重试"))
        password = None


def get_info(session, user):
    response = session.get(
        f"{BASE_URL}/student/{user}.do",
        params={"timestamp": str(int(time.time() * 1000))},
        timeout=REQUEST_TIMEOUT,
    )
    student = response_json(response).get("data") or {}
    print(time_msg("个人信息如下："))
    print(time_msg("姓名：" + str(student.get("name", ""))))
    print(time_msg("学号：" + user))
    print(time_msg("年级：" + str(student.get("grade", ""))))
    print(time_msg("校区：" + str(student.get("campusName", ""))))
    print(time_msg("学院：" + str(student.get("collegeName", ""))))
    print(time_msg("专业：" + str(student.get("departmentName", ""))))
    print(time_msg("班级：" + str(student.get("schoolClass", ""))))

    campus_code = str(student.get("campus") or student.get("teachCampus") or "2")
    for batch in student.get("electiveBatchList", []):
        if str(batch.get("canSelect")) == "1":
            print(time_msg("当前阶段：" + batch["schoolTermName"] + " " + batch["name"]))
            print(time_msg("开始时间：" + batch["beginTime"]))
            print(time_msg("结束时间：" + batch["endTime"]))
            return batch["code"], campus_code
    raise RuntimeError("当前没有可选课的批次")


def build_query_setting(
    user,
    batch_code,
    campus_code,
    teaching_class_type,
    query_content,
    check_conflict,
    check_capacity,
):
    return {
        "data": {
            "studentCode": user,
            "campus": campus_code,
            "electiveBatchCode": batch_code,
            "isMajor": "1",
            "teachingClassType": teaching_class_type,
            "checkConflict": check_conflict,
            "checkCapacity": check_capacity,
            "queryContent": query_content,
        },
        "pageSize": "100",
        "pageNumber": "0",
        "order": "",
    }


def normalize_candidate(course, teaching_class, teaching_class_type):
    def field(name, default=""):
        value = teaching_class.get(name)
        if value is None or value == "":
            value = course.get(name, default)
        return "" if value is None else str(value)

    return {
        "type": teaching_class_type,
        "courseNumber": field("courseNumber"),
        "courseName": field("courseName"),
        "teachingClassId": field("teachingClassID"),
        "courseIndex": field("courseIndex"),
        "teacherName": field("teacherName"),
        "teachingPlace": field("teachingPlace"),
        "classCapacity": field("classCapacity", "0"),
        "numberOfSelected": field("numberOfSelected", "0"),
        "isFull": field("isFull", "0"),
        "isConflict": field("isConflict", "0"),
        "isChoose": field("isChoose", "0"),
    }


def query_course_candidates(
    session,
    user,
    batch_code,
    campus_code,
    teaching_class_type,
    query_content,
    check_conflict="2",
    check_capacity="2",
):
    type_info = course_type_info(teaching_class_type)
    setting = build_query_setting(
        user,
        batch_code,
        campus_code,
        teaching_class_type,
        query_content,
        check_conflict,
        check_capacity,
    )
    response = session.post(
        f"{BASE_URL}/elective/{type_info['endpoint']}",
        data={"querySetting": json.dumps(setting, ensure_ascii=False, separators=(",", ":"))},
        timeout=REQUEST_TIMEOUT,
    )
    payload = response_json(response)

    candidates = []
    seen_ids = set()
    for course in payload.get("dataList") or []:
        teaching_classes = (course.get("tcList") or []) if type_info["nested"] else [course]
        for teaching_class in teaching_classes:
            candidate = normalize_candidate(course, teaching_class, teaching_class_type)
            teaching_class_id = candidate["teachingClassId"]
            if not teaching_class_id or teaching_class_id in seen_ids:
                continue
            seen_ids.add(teaching_class_id)
            candidates.append(candidate)
    return candidates


def target_from_candidate(candidate):
    return {
        key: candidate[key]
        for key in (
            "type",
            "courseNumber",
            "courseName",
            "teachingClassId",
            "courseIndex",
            "teacherName",
            "teachingPlace",
        )
    }


def display_target(index, target):
    label = course_type_info(target["type"])["label"]
    print(
        f"[{index}] [{label}] {target['courseNumber']} {target['courseName']}\n"
        f"    教学班：{target['courseIndex'] or '-'} | "
        f"教师：{target['teacherName'] or '-'} | "
        f"时间地点：{target['teachingPlace'] or '-'}"
    )


def load_course_specs():
    if not CONFIG_PATH.exists():
        raise RuntimeError("未找到 courses.txt，请先按说明填写课程配置")

    specs = []
    seen = set()
    try:
        with CONFIG_PATH.open(encoding="utf-8-sig", newline="") as config_file:
            for line_number, row in enumerate(csv.reader(config_file), start=1):
                values = [value.strip() for value in row]
                if not values or not any(values) or values[0].startswith("#"):
                    continue
                if len(values) != 3:
                    raise ValueError(
                        f"courses.txt 第 {line_number} 行应为：类别,课程号,教学班号"
                    )

                teaching_class_type = values[0].upper()
                course_number = values[1]
                course_index = values[2]
                course_type_info(teaching_class_type)
                if not course_number or not course_index:
                    raise ValueError(
                        f"courses.txt 第 {line_number} 行的课程号和教学班号不能为空"
                    )

                key = (teaching_class_type, course_number, course_index)
                if key in seen:
                    raise ValueError(f"courses.txt 第 {line_number} 行配置重复")
                seen.add(key)
                specs.append(
                    {
                        "type": teaching_class_type,
                        "courseNumber": course_number,
                        "courseIndex": course_index,
                        "lineNumber": line_number,
                    }
                )
    except csv.Error as error:
        raise ValueError(f"courses.txt 格式错误：{error}") from error
    except OSError as error:
        raise RuntimeError(f"无法读取 courses.txt：{error}") from error

    if not specs:
        raise RuntimeError("courses.txt 中没有待抢课程")
    return specs


def resolve_course_targets(session, user, batch_code, campus_code, specs):
    query_cache = {}
    targets = []
    seen_ids = set()
    for spec in specs:
        course_key = (spec["type"], spec["courseNumber"])
        if course_key not in query_cache:
            query_cache[course_key] = query_course_candidates(
                session,
                user,
                batch_code,
                campus_code,
                spec["type"],
                spec["courseNumber"],
                check_conflict="2",
                check_capacity="2",
            )

        matches = [
            candidate
            for candidate in query_cache[course_key]
            if candidate["courseNumber"] == spec["courseNumber"]
            and candidate["courseIndex"] == spec["courseIndex"]
        ]
        if not matches:
            raise RuntimeError(
                f"courses.txt 第 {spec['lineNumber']} 行找不到对应教学班："
                f"{spec['type']},{spec['courseNumber']},{spec['courseIndex']}"
            )
        if len(matches) > 1:
            raise RuntimeError(
                f"courses.txt 第 {spec['lineNumber']} 行匹配到多个教学班，"
                "当前教学班号不足以唯一定位"
            )

        target = target_from_candidate(matches[0])
        if target["teachingClassId"] in seen_ids:
            raise RuntimeError(
                f"courses.txt 第 {spec['lineNumber']} 行与其他配置指向同一教学班"
            )
        seen_ids.add(target["teachingClassId"])
        targets.append(target)

    print(time_msg(f"已精确解析 {len(targets)} 个教学班："))
    for index, target in enumerate(targets, start=1):
        display_target(index, target)
    return targets


def wait_for_process_result(session, user):
    for _ in range(PROCESS_POLL_LIMIT):
        response = session.post(
            f"{BASE_URL}/elective/studentstatus.do",
            data={"studentCode": user},
            timeout=REQUEST_TIMEOUT,
        )
        payload = response_json(response)
        code = str(payload.get("code"))
        if code == "1":
            return True, payload.get("msg", "")
        if code == "-1":
            return False, payload.get("msg", "选课失败")
        time.sleep(PROCESS_POLL_INTERVAL)
    return False, "操作处理超时"


def find_target_candidate(
    session,
    user,
    batch_code,
    campus_code,
    target,
    check_conflict,
    check_capacity,
):
    candidates = query_course_candidates(
        session,
        user,
        batch_code,
        campus_code,
        target["type"],
        target["courseNumber"],
        check_conflict=check_conflict,
        check_capacity=check_capacity,
    )
    return next(
        (
            candidate
            for candidate in candidates
            if candidate["courseNumber"] == target["courseNumber"]
            and candidate["teachingClassId"] == target["teachingClassId"]
        ),
        None,
    )


def submit_target(session, user, batch_code, campus_code, target):
    candidate = find_target_candidate(
        session,
        user,
        batch_code,
        campus_code,
        target,
        check_conflict="0",
        check_capacity="0",
    )
    if candidate is None:
        return False

    add_setting = {
        "data": {
            "operationType": "1",
            "studentCode": user,
            "electiveBatchCode": batch_code,
            "teachingClassId": target["teachingClassId"],
            "isMajor": "1",
            "campus": campus_code,
            "teachingClassType": target["type"],
        }
    }
    response = session.post(
        f"{BASE_URL}/elective/volunteer.do",
        data={"addParam": json.dumps(add_setting, ensure_ascii=False, separators=(",", ":"))},
        timeout=REQUEST_TIMEOUT,
    )
    payload = response_json(response)
    if str(payload.get("code")) != "1":
        return False

    success, message = wait_for_process_result(session, user)
    if success:
        print(
            time_msg(
                f"已选中 [{target['courseNumber']}] {target['courseName']} "
                f"教学班 {target['courseIndex']}"
            )
        )
        return True
    print(time_msg(f"{target['courseName']} 提交失败：{message}"))
    return False


def remove_completed_target(config, completed_target):
    completed_key = (
        completed_target["type"],
        completed_target["courseNumber"],
    )
    config["courses"] = [
        target
        for target in config["courses"]
        if (target["type"], target["courseNumber"]) != completed_key
    ]


def validate_targets_online(session, user, batch_code, campus_code, config):
    invalid_targets = []
    completed_targets = []
    for target in config["courses"]:
        candidate = find_target_candidate(
            session,
            user,
            batch_code,
            campus_code,
            target,
            check_conflict="2",
            check_capacity="2",
        )
        if candidate is None:
            invalid_targets.append(target)
        elif candidate["isChoose"] == "1":
            completed_targets.append(target)

    completed_keys = {
        (target["type"], target["courseNumber"]) for target in completed_targets
    }
    invalid_targets = [
        target
        for target in invalid_targets
        if (target["type"], target["courseNumber"]) not in completed_keys
    ]

    removed_keys = set()
    for target in completed_targets:
        course_key = (target["type"], target["courseNumber"])
        if course_key in removed_keys:
            continue
        print(time_msg(f"{target['courseName']} 已选中，从待抢配置中移除"))
        remove_completed_target(config, target)
        removed_keys.add(course_key)

    if invalid_targets:
        print("以下教学班当前无法查询，请检查 courses.txt：")
        for index, target in enumerate(invalid_targets, start=1):
            display_target(index, target)
        raise RuntimeError("配置中存在无法查询的教学班")


def run_courses(mode, user, password, session, batch_code, campus_code):
    specs = load_course_specs()
    config = {"courses": []}
    needs_resolution = True
    needs_validation = True
    confirmed = False
    announced = False
    while True:
        try:
            if needs_resolution:
                config["courses"] = resolve_course_targets(
                    session, user, batch_code, campus_code, specs
                )
                needs_resolution = False
                needs_validation = True
            if needs_validation:
                validate_targets_online(session, user, batch_code, campus_code, config)
                needs_validation = False
                if not config["courses"]:
                    print(time_msg("配置中的课程均已选中，无需继续运行"))
                    return
            if not confirmed:
                answer = input(time_msg("是否开始抢课？(y/n)：")).strip().lower()
                if answer != "y":
                    print(time_msg("已取消抢课，程序已退出"))
                    return
                confirmed = True
            if not announced:
                print(time_msg("正在查询精确教学班...请保持程序运行（按 Ctrl+C 退出）"))
                announced = True
            for target in list(config["courses"]):
                if not any(
                    item["teachingClassId"] == target["teachingClassId"]
                    for item in config["courses"]
                ):
                    continue
                if submit_target(session, user, batch_code, campus_code, target):
                    remove_completed_target(config, target)
            if not config["courses"]:
                print(time_msg("全部目标课程均已选中，程序已退出"))
                return
            time.sleep(COURSE_SCAN_INTERVAL)
        except (LoginExpiredError, requests.RequestException) as error:
            print(time_msg(f"连接或登录状态异常：{error}"))
            print(time_msg("尝试恢复本地会话或重新登录..."))
            while True:
                try:
                    user, password, session = login(
                        mode, expected_user=user, password=password
                    )
                    needs_resolution = True
                    announced = False
                    break
                except requests.RequestException as login_error:
                    print(time_msg(f"登录线路仍不可用：{login_error}"))
                    time.sleep(3)

def main():
    print(time_msg("BIT 本科生抢课系统已启动"))
    try:
        mode = check_net()
        user, password, session = login(mode)
        batch_code, campus_code = get_info(session, user)
        run_courses(mode, user, password, session, batch_code, campus_code)
    except KeyboardInterrupt:
        print(time_msg("操作已取消，程序已退出"))
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(time_msg(f"程序停止：{error}"))
    except requests.RequestException as error:
        print(time_msg(f"网络请求失败：{error}"))


if __name__ == "__main__":
    main()
