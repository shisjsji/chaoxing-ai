"""在独立进程里读考试列表，把结果打成一行 JSON 交给界面进程。

为什么要单开进程：界面进程里有 WebView2 和嵌入窗口的消息循环，Python 线程
经常被挤得跑不动，同样的请求在主进程里要几十秒、在这里只要一两秒。
"""

import json
import sys


def build() -> dict:
    from api.answer import DummyTiku
    from api.base import Account, Chaoxing, SessionManager
    from api.exam import list_exams

    chaoxing = Chaoxing(
        account=Account("", ""),
        tiku=DummyTiku(),
        query_delay=0,
        work_max_retries=0,
        work_redo_enabled=False,
    )
    state = chaoxing.login(login_with_cookies=True)
    if not state.get("status"):
        return {"ok": False, "error": "登录状态失效了：请点「登录学习通」重新登录一次"}

    session = SessionManager.get_session()
    courses = chaoxing.get_course_list()
    result = []
    for course in courses:
        try:
            items = list_exams(session, course)
        except Exception:
            items = []
        if items:
            result.append({
                "course": course.get("title", ""),
                "courseId": str(course.get("courseId", "")),
                "clazzId": str(course.get("clazzId", "")),
                "cpi": str(course.get("cpi", "")),
                "items": [
                    {"exam_id": it.exam_id, "enc_task": it.enc_task, "name": it.name,
                     "status": it.status, "expire": it.expire_time}
                    for it in items
                ],
            })
    return {"ok": True, "courses": result,
            "total": sum(len(c["items"]) for c in result)}


def main(out_path: str = "") -> int:
    try:
        payload = build()
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(payload, ensure_ascii=False)
    if out_path:
        # 打包版是窗口程序，没有 stdout，结果只能写文件带回去
        with open(out_path, "w", encoding="utf-8") as fp:
            fp.write(text)
    else:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))