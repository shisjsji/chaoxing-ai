"""考试自动答题。

流程：解析考试封面 → 开始 → 逐题拉取（服务端渲染的 HTML）→ 解析
→ 先查题库、未命中再问模型 → 逐题提交 → 交卷。

人脸验证：检测到就抛 FaceRequired，由界面提示使用者自己完成，
程序不参与、不伪造任何活体信息。
"""

import configparser
import json
import math
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from api.library import Library, fingerprint
from api.logger import logger

COVER_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/phone/task-exam"
START_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/phone/start"
QUESTION_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew"
SUBMIT_URL = "https://mooc1.chaoxing.com/exam-ans/exam/test/reVersionSubmitTestNew"
SHEET_URL = "https://mooc1-api.chaoxing.com/exam-ans/exam/phone/loadAnswerStatic"
EXAM_LIST_URL = "https://mooc1-api.chaoxing.com/exam/phone/task-list"

TYPE_MAP = {
    "0": "single",
    "1": "multiple",
    "2": "completion",
    "3": "judgement",
    "4": "shortanswer",
    "5": "term",
    "6": "essay",
    "7": "calculation",
    "8": "other",
}

STATUS_WORDS = ("未开始", "待做", "未交", "进行中", "已完成", "已批阅", "已过期")

TYPE_NAME = {
    "0": "单选题", "1": "多选题", "2": "填空题", "3": "判断题",
    "4": "简答题", "5": "名词解释", "6": "论述题", "7": "计算题", "8": "其他",
}

# 这些题型考试接口的表单结构尚未验证，暂不自动作答
SUBJECTIVE_TYPES = {"shortanswer", "term", "essay", "calculation", "other"}

# 主观题的参考答案写在这里，供使用者复制粘贴
SUBJECTIVE_FILE = Path("主观题参考答案.txt")

CONFIG_PATH = Path("config.ini")


class ExamError(Exception):
    """考试流程异常。"""


class FaceRequired(ExamError):
    """本场考试要求人脸验证，需要使用者自行完成后继续。"""


class ExamNotStart(ExamError):
    """考试尚未开始。"""


class ExamCompleted(ExamError):
    """考试已完成或已提交。"""


@dataclass
class ExamQuestion:
    index: int
    qid: str
    qtype: str
    title: str
    options: dict = field(default_factory=dict)
    saved_answer: str = ""
    type_code: str = "0"
    type_name: str = ""

    @property
    def options_text(self) -> str:
        if not self.options:
            return ""
        lines = []
        for key, value in self.options.items():
            # 选项文本里通常已含字母前缀，避免出现 "A A 战斗机"
            lines.append(value if value.upper().startswith(key) else f"{key} {value}")
        return "\n".join(lines)


def _now_ts() -> str:
    return str(int(time.time() * 1000))


def _status_ok(data: dict) -> bool:
    """平台的成功标记有时是 "success" 字符串，有时是布尔值，统一判断。"""
    status = data.get("status")
    if isinstance(status, bool):
        return status
    return str(status).strip().lower() in ("success", "1", "true", "ok")


def exam_signature(uid: str, qid: str, x: int, y: int) -> dict:
    """考试提交接口的签名参数组。纯本地计算，不需要服务端密钥。"""
    ts = _now_ts()
    r1 = random.randrange(0, 9)
    r2 = random.randrange(0, 9)
    a = f"{secrets.token_hex(16)}{ts[4:]}{r1}{r2}{qid or ''}"
    temp = 0
    for ch in a:
        temp = (temp << 5) - temp + ord(ch)
    salt = f"{r1}{r2}{(0x7FFFFFFF & temp) % 10}"
    enc_val = f"{uid}"
    if qid:
        enc_val += f"_{qid}"
    enc_val += f"|{salt}"
    enc_val2 = "".join(str(ord(c)) for c in enc_val)
    b = len(enc_val2) // 5
    c = int(enc_val2[b] + enc_val2[2 * b] + enc_val2[3 * b] + enc_val2[4 * b])
    d = len(enc_val) // 2 + 1
    e = (c * int(enc_val2[:10]) + d) % 0x7FFFFFFF
    pos = f"({x}|{y})"
    result = ""
    for ch in pos:
        temp = ord(ch) ^ math.floor(e / 0x7FFFFFFF * 0xFF)
        result += f"{temp:02x}"
        e = (c * e + d) % 0x7FFFFFFF
    return {
        "pos": f"{result}{secrets.token_hex(4)}",
        "rd": random.random(),
        "value": pos,
        "_edt": f"{ts}{salt}",
    }


def parse_question(node: Tag) -> ExamQuestion:
    """解析一道题。兼容单题页与整卷预览两种结构。"""
    qid = ""
    qid_node = node.select_one("input[name='questionId']")
    if qid_node and qid_node.get("value"):
        qid = str(qid_node["value"])

    type_node = node.select_one("input[name^='type']")
    type_code = str(type_node["value"]) if type_node and type_node.get("value") else "0"
    qtype = TYPE_MAP.get(type_code, "other")
    type_name = TYPE_NAME.get(type_code, "")

    # 题干：先取整段文本，再剥掉题型标题、序号和分值前缀
    title = ""
    tit = node.select_one("div.tit")
    if tit:
        raw = tit.get_text(" ", strip=True)
        # 题干前缀可能套好几层：题型标题、（共40题，40.0分）、(单选题, 1.0 分)、序号
        for _ in range(4):
            before = raw
            raw = re.sub(r"^[一二三四五六七八九十]+、\s*", "", raw)
            raw = re.sub(r"^(单选题|多选题|判断题|填空题|简答题|名词解释|论述题|计算题)"
                         r"\s*[（(][^)）]*[)）]\s*", "", raw)
            raw = re.sub(r"^[（(]\s*(单选题|多选题|判断题|填空题|简答题|名词解释|论述题|计算题)"
                         r"\s*[,，][^)）]*[)）]\s*", "", raw)
            raw = re.sub(r"^\d+\s*[.、]\s*", "", raw)
            raw = re.sub(r"[（(]\s*\d+(\.\d+)?\s*分\s*[)）]", "", raw)
            raw = raw.strip()
            if raw == before:
                break
        title = re.sub(r"\s+", " ", raw).strip()

    # 选项
    options: dict = {}
    for opt in node.select("div.answerList.radioList"):
        key = opt.get("name") or ""
        cc = opt.select_one("cc")
        value = "".join(s.strip() for s in cc.strings) if cc else opt.get_text()
        if key:
            options[key.upper()] = re.sub(r"\s+", " ", value).strip()

    saved = ""
    answer_node = node.select_one("input[id^='answer']")
    if answer_node and answer_node.get("value"):
        saved = str(answer_node["value"])

    return ExamQuestion(index=0, qid=qid, qtype=qtype, title=title,
                        options=options, saved_answer=saved,
                        type_code=type_code, type_name=type_name)


class ExamSession:
    """一场考试的会话。"""

    def __init__(self, session: requests.Session, course: dict, exam_id, enc_task):
        self.session = session
        self.course = course
        self.exam_id = str(exam_id)
        self.enc_task = str(enc_task)

        self.course_id = str(course.get("courseId", ""))
        self.class_id = str(course.get("clazzId", ""))
        self.cpi = str(course.get("cpi", ""))

        self.exam_answer_id: Optional[int] = None
        self.title = ""
        self.need_face = False
        self.need_code = False
        self.enc = ""
        self.enc_remain_time = ""
        self.last_update_time = ""
        self.remain_time = "0"
        self.uid = ""
        self.question_count = 0
        self.last_html = ""
        self.last_url = ""

    # ---------- 封面 ----------
    def open(self, reset: bool = False) -> None:
        """拉取考试封面，初始化必要参数。

        reset=True 用于重考：平台要求 restartOp 之后再带 reset=true 请求一次，
        考试才真正回到可作答状态，否则封面仍然指向上一份答卷。
        """
        params = {
            "redo": 1,
            "taskrefId": self.exam_id,
            "courseId": self.course_id,
            "classId": self.class_id,
            "role": "",
            "source": 0,
            "enc_task": self.enc_task,
            "cpi": self.cpi,
            "vx": 0,
            "examsignal": 1,
        }
        if reset:
            params.update({
                "examAnswerId": self.exam_answer_id or "",
                "ut": "s",
                "code": "",
                "reset": "true",
                "protocol_v": 1,
            })

        resp = self.session.get(COVER_URL, params=params,
                                allow_redirects=False, timeout=20)

        if resp.status_code == 302:
            location = resp.headers.get("Location", "")
            if reset:
                # 重考复位后会跳一次，跟过去就是可作答的考试页
                followed = self.session.get(
                    location if location.startswith("http")
                    else "https://mooc1.chaoxing.com" + location,
                    timeout=20)
                self.last_html = followed.text
                self.last_url = followed.url
                self._parse_cover(followed.text)
                return
            if "/exam-ans/exam/phone/look" in location:
                raise ExamCompleted("考试已完成")
            raise ExamError("考试入口跳转异常")

        self.last_html = resp.text
        self.last_url = resp.url
        self._parse_cover(resp.text)

    def _parse_cover(self, html: str) -> None:
        """从考试封面页解析出作答必需的信息。"""
        soup = BeautifulSoup(html, "lxml")
        warn = soup.select_one("h2.color6.fs36.textCenter.marBom60.line64")
        if warn:
            text = warn.get_text().strip()
            if "尚未开始" in text:
                raise ExamNotStart(text)
            raise ExamError(text)

        node = soup.select_one("input#testUserRelationId")
        if not node or not node.get("value"):
            raise ExamError(
                f"未能解析考试信息（页面 {len(html)} 字节；"
                f"courseId={self.course_id or '空'} classId={self.class_id or '空'} "
                f"cpi={self.cpi or '空'}）"
            )
        self.exam_answer_id = int(node["value"])

        title_node = soup.select_one("span.overHidden2")
        self.title = title_node.get_text().strip() if title_node else f"考试 {self.exam_id}"

        face_node = soup.select_one("input#faceRecognitionCompare")
        self.need_face = bool(face_node and face_node.get("value") not in (None, "", "0"))

        script = soup.select_one("body script")
        if script:
            matched = re.search(r"var\s*needcode\s*=\s*(\d+);", script.get_text())
            if matched:
                self.need_code = bool(int(matched.group(1)))

        self.uid = self.session.cookies.get("_uid") or ""
        logger.info(f"考试信息读取成功：{self.title}"
                    f"（{'需要人脸验证' if self.need_face else '无需人脸'}）")

        if self.need_face:
            raise FaceRequired("本场考试要求人脸验证")

    # ---------- 重考 ----------
    def restart(self) -> None:
        """对允许重考的场次发起重考，让考试回到可作答状态。

        顺序照平台网页的来：先 restartOp 复位，再带 reset=true 重新进封面。
        """
        resp = self.session.get(
            COVER_URL,
            params={
                "redo": 1, "taskrefId": self.exam_id,
                "courseId": self.course_id, "classId": self.class_id,
                "role": "", "source": 0, "enc_task": self.enc_task,
                "cpi": self.cpi, "vx": 0, "examsignal": 1,
            },
            allow_redirects=False,
            timeout=20,
        )
        if resp.status_code == 302:
            query = parse_qs(urlparse(resp.headers.get("Location", "")).query)
            found = (query.get("examAnswerId") or [""])[0]
            if found:
                try:
                    self.exam_answer_id = int(found)
                except ValueError:
                    self.exam_answer_id = found

        if not self.exam_answer_id:
            raise ExamError("拿不到 examAnswerId，无法重考")

        r = self.session.get(
            "https://mooc1.chaoxing.com/exam-ans/exam/phone/restartOp",
            params={"examId": self.exam_id, "examAnswerId": self.exam_answer_id,
                    "courseId": self.course_id, "classId": self.class_id},
            timeout=20,
        )
        try:
            data = r.json()
        except Exception:
            raise ExamError("重考请求返回异常")
        if not _status_ok(data):
            raise ExamError(f"重考失败：{data.get('msg') or data}")
        logger.info(f"已发起重考：{data.get('msg') or 'ok'}")

    # ---------- 接管已开始的考试 ----------
    def attach(self) -> None:
        """接管一场已经开始（或正在进行）的考试。

        使用者在平台上自己点了开始、自己过了验证码或人脸，这里只把签名参数
        从试卷页捡回来，然后接着自动答题。不需要调用 start。
        """
        resp = self.session.get(
            COVER_URL,
            params={
                "redo": 1,
                "taskrefId": self.exam_id,
                "courseId": self.course_id,
                "classId": self.class_id,
                "role": "",
                "source": 0,
                "enc_task": self.enc_task,
                "cpi": self.cpi,
                "vx": 0,
                "examsignal": 1,
            },
            allow_redirects=True,
            timeout=25,
        )

        # 跳转后的地址里通常带着 examAnswerId，这是提交时必须的
        query = parse_qs(urlparse(resp.url).query)
        if (query.get("examAnswerId") or [""])[0]:
            try:
                self.exam_answer_id = int(query["examAnswerId"][0])
            except ValueError:
                pass

        soup = BeautifulSoup(resp.text, "lxml")
        form = soup.select_one("form#submitTest")
        if form is None:
            tip = soup.select_one("p.blankTips, h2.color6")
            text = tip.get_text().strip() if tip else ""
            if "尚未开始" in text:
                raise ExamNotStart(text)
            raise ExamError(
                text or "没有读到正在进行的试卷页。请先在平台上点开这场考试，"
                        "停留在答题页面，再点「接管」")

        self._sync_from_form(form)

        if not self.exam_answer_id:
            node = soup.select_one("input#testUserRelationId")
            if node and node.get("value"):
                try:
                    self.exam_answer_id = int(node["value"])
                except ValueError:
                    pass
        if not self.exam_answer_id:
            raise ExamError("拿不到 examAnswerId，无法接管。请在平台上重新进入这场考试后再试")

        self.title = self.title or f"考试 {self.exam_id}"
        logger.info(f"已接管进行中的考试：{self.title}")

    # ---------- 开始 ----------
    def start(self) -> None:
        """开始考试。成功时平台返回 302，enc 签名参数藏在跳转地址里。"""
        resp = self.session.get(
            START_URL,
            params={
                "courseId": self.course_id,
                "classId": self.class_id,
                "examId": self.exam_id,
                "source": 0,
                "examAnswerId": self.exam_answer_id,
                "cpi": self.cpi,
                "keyboardDisplayRequiresUserAction": 1,
                "faceDetection": 1 if self.need_face else 0,
            },
            allow_redirects=False,
            timeout=20,
        )

        if resp.status_code == 302:
            location = resp.headers.get("Location", "")
            enc = (parse_qs(urlparse(location).query).get("enc") or [""])[0]
            if enc:
                self.enc = enc
            logger.info("考试已开始")
            return

        # 200 一般是考试码错误、人脸未通过之类的提示页
        soup = BeautifulSoup(resp.text, "lxml")
        tip = soup.select_one("p.blankTips, li.msg")
        text = tip.get_text().strip() if tip else f"HTTP {resp.status_code}"
        raise ExamError(f"开始考试失败：{text}")

    # ---------- 计时与签名参数 ----------
    def _sync_from_form(self, form) -> None:
        """每拉一题，平台都会下发新的计时与签名参数。

        不同步的话后续请求带的还是旧值，答案会被判超时或直接不收。
        """
        for field, attr in (("enc", "enc"),
                            ("encRemainTime", "enc_remain_time"),
                            ("remainTime", "remain_time"),
                            ("encLastUpdateTime", "last_update_time")):
            node = form.select_one(f"input#{field}")
            if node is not None and node.get("value") is not None:
                setattr(self, attr, str(node["value"]))

    def _sync_from_data(self, raw) -> None:
        """提交成功后 data 是「更新时间|剩余秒数|enc」三段。"""
        if not isinstance(raw, str):
            return
        parts = raw.split("|")
        if len(parts) < 3:
            return
        if parts[0]:
            self.last_update_time = parts[0]
        if parts[1]:
            self.enc_remain_time = parts[1]
        if parts[2]:
            self.enc = parts[2]

    # ---------- 拉题 ----------
    def fetch(self, index: int) -> ExamQuestion:
        resp = self.session.get(
            QUESTION_URL,
            params={
                "courseId": self.course_id,
                "classId": self.class_id,
                "tId": self.exam_id,
                "id": self.exam_answer_id,
                "source": 0,
                "p": 1,
                "isphone": "true",
                "tag": int(self.enc_remain_time == "" or self.enc_remain_time == "0"),
                "cpi": self.cpi,
                "start": index,
                "enc": self.enc,
                "keyboardDisplayRequiresUserAction": 1,
                "monitorStatus": 0,
                "monitorOp": -1,
                "remainTimeParam": self.enc_remain_time,
                "relationAnswerLastUpdateTime": self.last_update_time,
            },
            timeout=25,
        )

        soup = BeautifulSoup(resp.text, "lxml")
        # 留下原始页面：提交时要按页面上的表单字段原样回传
        self.last_html = resp.text
        self.last_url = resp.url
        tip = soup.select_one("p.blankTips")
        if tip:
            text = tip.get_text().strip()
            if "已经提交" in text:
                raise ExamCompleted(text)
            raise ExamError(text)

        form = soup.select_one("form#submitTest")
        if form is not None:
            self._sync_from_form(form)

        node = soup.select_one("div.allAnswerList.ans-cc-exam, div.answerMain.ans-cc-exam")
        if node is None:
            raise ExamError("未找到题目节点（可能页面结构变化）")

        question = parse_question(node)
        question.index = index
        return question

    # ---------- 提交 ----------
    def build_answer_fields(self, question: ExamQuestion, answer: str) -> dict:
        """把答案文本转成考试接口要求的表单字段。

        选择题要选项字母（多选键名是 answers 负数），判断题要 true/false，
        填空题按空拆分。转不出来就抛异常，避免提交一个无效答案。
        """
        from api.base import (best_option_by_similarity, clean_res,
                              match_option_exact, multi_cut)

        qid = question.qid
        qtype = question.qtype
        raw = (answer or "").strip()
        if not raw:
            raise ExamError("答案为空")

        if qtype in ("single", "multiple"):
            option_list = [f"{k} {v}" for k, v in question.options.items()]
            if not option_list:
                raise ExamError("该题没有解析到选项")

            # 纯字母答案（题库里常见）直接采用，跳过内容匹配
            if re.fullmatch(r"[A-Za-z]+", raw):
                letters = "".join(dict.fromkeys(ch.upper() for ch in raw
                                                if ch.upper() in question.options))
                if letters:
                    key = f"answer{qid}" if qtype == "single" else f"answers{qid}"
                    return {key: letters[0] if qtype == "single" else "".join(sorted(letters))}

            if qtype == "single":
                letter = match_option_exact(raw, option_list)
                if not letter:
                    raise ExamError(f"答案无法匹配到选项：{raw[:30]}")
                return {f"answer{qid}": letter}

            pieces = multi_cut(raw) or [raw]
            letters = ""
            for piece in clean_res(pieces):
                letter = match_option_exact(piece, option_list)
                if letter:
                    letters += letter
            if not letters:
                letter = best_option_by_similarity(raw, option_list)
                letters = letter or ""
            if not letters:
                raise ExamError(f"答案无法匹配到选项：{raw[:30]}")
            return {f"answers{qid}": "".join(sorted(set(letters)))}

        if qtype == "judgement":
            from api.answer_check import check_judgement

            value = check_judgement(raw, ["正确", "对", "√", "是"], ["错误", "错", "×", "否"])
            if value == -1:
                raise ExamError(f"无法判断对错：{raw[:30]}")
            return {f"answer{qid}": "true" if value == 1 else "false"}

        if qtype == "completion":
            pieces = multi_cut(raw) or [raw]
            fields = {}
            numbers = ""
            for i, piece in enumerate(pieces, 1):
                fields[f"answer{qid}{i}"] = piece
                numbers += f"{i},"
            fields[f"blankNum{qid}"] = numbers
            return fields

        raise ExamError("该题型暂不支持自动提交")

    def submit(self, question: ExamQuestion, answer: str, final: bool = False,
               page_fields: Optional[dict] = None) -> dict:
        payload = {
            "courseId": self.course_id,
            "testPaperId": self.exam_id,
            "testUserRelationId": self.exam_answer_id,
            "classId": self.class_id,
            "type": 0,
            "isphone": "true",
            "subCount": "",
            "remainTime": self.remain_time,
            "tempSave": "false" if final else "true",
            "timeOver": "false",
            "encRemainTime": self.enc_remain_time,
            "encLastUpdateTime": self.last_update_time,
            "enc": self.enc,
            "userId": self.uid,
            "source": 0,
            "start": question.index,
            "enterPageTime": self.last_update_time,
            "monitorforcesubmit": 0,
            "answeredView": 0,
            "hidetext": "",
            "version": 1,
        }
        if page_fields:
            # 试卷页上的结构性字段（paperId、openc 等）必须原样带上，
            # 少一个平台就回「无效操作」。
            payload.update(page_fields)
            # 下面这些由程序自己决定，不能被页面里的默认值覆盖：
            # 尤其 tempSave，页面上是 false（等于交卷），照抄会把整卷交掉。
            payload["tempSave"] = "false" if final else "true"
            payload["timeOver"] = "false"
            payload["subCount"] = ""
            payload["remainTime"] = self.remain_time
            payload["encRemainTime"] = self.enc_remain_time
            payload["encLastUpdateTime"] = self.last_update_time
            payload["enc"] = self.enc
            payload["userId"] = self.uid
            payload["start"] = question.index
            payload["monitorforcesubmit"] = 0
            payload["answeredView"] = 0

        if question.qid:
            payload["questionId"] = question.qid
            payload[f"type{question.qid}"] = question.type_code
            payload[f"typeName{question.qid}"] = question.type_name
            payload.update(self.build_answer_fields(question, answer))

        resp = self.session.post(
            SUBMIT_URL,
            params={
                "classId": self.class_id,
                "courseId": self.course_id,
                "cpi": self.cpi,
                "testPaperId": self.exam_id,
                "testUserRelationId": self.exam_answer_id,
                "tempSave": "false" if final else "true",
                **exam_signature(self.uid, question.qid, random.randint(100, 1000),
                                 random.randint(100, 1000)),
                "qid": question.qid,
                "version": 1,
            },
            data=payload,
            timeout=25,
        )
        try:
            data = resp.json()
        except Exception:
            raise ExamError("提交返回非 JSON")
        if not _status_ok(data):
            message = str(data.get("msg") or data)
            if "考试时间已用完" in message:
                raise ExamError("考试时间已用完，无法再提交")
            raise ExamError(f"提交失败：{message}")
        if not final:
            self._sync_from_data(data.get("data"))
        return data

    def final_submit(self) -> dict:
        """直接交卷（不提交具体题目）。"""
        blank = ExamQuestion(index=0, qid="", qtype="other", title="")
        return self.submit(blank, "", final=True)


# ---------- 作答 ----------
_AI_CONF_CACHE: dict = {}
_AI_CONF_MTIME: float = -1.0


def _ai_conf() -> dict:
    """读取模型配置。config.ini 被改过就重新读，否则界面里换了模型/密钥不会生效。"""
    global _AI_CONF_MTIME, _AI_CONF_CACHE
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        _AI_CONF_CACHE, _AI_CONF_MTIME = {}, -1.0
        return {}
    if _AI_CONF_CACHE and mtime == _AI_CONF_MTIME:
        return _AI_CONF_CACHE

    _AI_CONF_CACHE = {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(CONFIG_PATH, encoding="utf-8")
    if "tiku" in parser:
        section = parser["tiku"]
        key = section.get("key", "").strip()
        if not key:
            # 界面保存的 Key 走 DPAPI 加密存在 secret.dat，config.ini 里是空的
            try:
                from api.dpapi import read_saved_secrets

                key = read_saved_secrets().get("api_key", "")
            except Exception:
                key = ""
        _AI_CONF_CACHE.update({
            "endpoint": section.get("endpoint", "").strip(),
            "key": key,
            "model": section.get("model", "deepseek-chat").strip(),
            "reasoning_effort": (section.get("reasoning_effort", "") or "").strip(),
        })
    _AI_CONF_MTIME = mtime
    return _AI_CONF_CACHE


_PROMPTS = {
    "single": "本题为单选题，只有一个选项完全符合题意",
    "multiple": "本题为多选题，选出所有符合题意的选项",
    "judgement": "本题为判断题，只回答“正确”或“错误”",
    "completion": "本题为填空题，直接给出应填入的内容",
    "shortanswer": "本题为简答题，紧扣题目要求作答",
    "term": "本题为名词解释，用一到三句话给出准确简洁的定义",
    "essay": "本题为论述题，先给核心观点再分条展开，条理清晰",
    "calculation": "本题为计算题，先写公式再逐步代入计算，最后给出结果",
}

# 当前在考哪门课。考试流程是单线程的，用一个模块变量带下去比
# 顺着五层调用一路传参省事得多。
_COURSE_NAME = ""


def set_course_name(name: str) -> None:
    global _COURSE_NAME
    _COURSE_NAME = str(name or "").strip()


def _role_prefix() -> str:
    if _COURSE_NAME:
        return (f"你是「{_COURSE_NAME}」这门课的资深命题教师，"
                "熟悉这门课的教材表述和常见考点。")
    return "你是这门课的资深命题教师，熟悉教材表述和常见考点。"


# 客观题才值得双答比对；主观题两次措辞必然不同，比了也没意义
OBJECTIVE_TYPES = {"single", "multiple", "judgement"}

_SECOND_PASS = (
    "这是独立复核：请完全重新判读题目，不要假定任何既有答案。"
    "先在内部逐项比对选项、排除干扰项，再给出最终结果。"
)

# 逐项分析。多选特别强调不能漏，因为学习通多选少选错选都是 0 分。
_ANALYZE = ("答题前先在内部逐个分析每个选项：它符合教材表述还是背离，错在哪一步。"
            "结论里不要写分析过程。")


def _same_answer(question: "ExamQuestion", a: str, b: str) -> bool:
    """两次答案是不是同一个。归一化后比较，避开写法差异。"""
    from api.library import normalize_answer

    key_a = normalize_answer(a, question.qtype, question.options_text)
    key_b = normalize_answer(b, question.qtype, question.options_text)
    return bool(key_a) and key_a == key_b


def ask_ai(question: ExamQuestion, variant: int = 0) -> Optional[str]:
    """问模型。带中等思考强度，模型不支持时自动降级重试。

    variant=1 是「独立复核」那一遍：换一套说法、不透露上一次结果，
    这样两次答案才真的有比对价值。
    """
    conf = _ai_conf()
    if not (conf.get("endpoint") and conf.get("key")):
        return None

    try:
        from openai import OpenAI
    except Exception:
        return None

    client = OpenAI(base_url=conf["endpoint"], api_key=conf["key"], timeout=90)
    prompt = _PROMPTS.get(question.qtype, _PROMPTS["shortanswer"])
    if variant:
        prompt = _SECOND_PASS + prompt
    if question.qtype in OBJECTIVE_TYPES:
        prompt = prompt + "。" + _ANALYZE
        if question.qtype == "multiple":
            prompt += ("凡是有教材依据、成立的要全部选出，不要因为「没把握」就漏掉——"
                       "多选题少选和错选同样不得分。但也不要把明显背离题意的选项凑数。")
    user_text = f"题目：{question.title}"
    if question.options:
        user_text += f"\n选项：\n{question.options_text}"
    system_text = (_role_prefix() + prompt + "。"
                   "以 json 格式输出，示例：{\"Answer\": [\"答案内容\"]}。"
                   "除此之外不要输出任何多余内容，也不要使用 MD 语法。")

    kwargs = {
        "model": conf["model"],
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
    }

    completion = None
    effort = conf.get("reasoning_effort", "")
    if effort in ("low", "medium", "high"):
        try:
            completion = client.chat.completions.create(**kwargs, reasoning_effort=effort)
        except Exception as exc:
            logger.warning(f"模型不接受 reasoning_effort，改为普通调用：{exc}")
            completion = None
    if completion is None:
        try:
            completion = client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error(f"模型调用失败：{exc}")
            return None

    content = completion.choices[0].message.content or ""
    if not content:
        content = getattr(completion.choices[0].message, "reasoning_content", "") or ""

    text = content.strip()
    matched = re.search(r"\{.*\}", text, re.S)
    if matched:
        try:
            data = json.loads(matched.group(0))
            items = data.get("Answer") or data.get("answer")
            if isinstance(items, str):
                items = [items]
            if items:
                return "\n".join(str(x) for x in items).strip()
        except Exception:
            pass
    return text[:2000] if text else None


_PACE_DEFAULTS = {"gap": 3.0, "min_minutes": 0.0, "hold": 0.0, "accuracy": 100}


def exam_pace() -> dict:
    """考试节奏：每题间隔、整卷最少用时、交卷前停顿、目标正确率。

    这几个值只影响考试路径，读不到配置就按默认走，绝不因此中断考试。
    """
    pace = dict(_PACE_DEFAULTS)
    try:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(CONFIG_PATH, encoding="utf-8")
        if "common" not in parser:
            return pace
        section = parser["common"]

        def num(name, default, low, high):
            try:
                return max(low, min(high, float(section.get(name, default) or default)))
            except (TypeError, ValueError):
                return default

        pace["gap"] = num("exam_answer_gap", 3.0, 0.0, 30.0)
        pace["min_minutes"] = num("exam_min_minutes", 0.0, 0.0, 180.0)
        pace["hold"] = num("exam_hold_seconds", 0.0, 0.0, 120.0)
        pace["accuracy"] = num("target_accuracy", 100.0, 60.0, 100.0)
    except Exception:
        pass
    return pace


def ask_with_check(question: ExamQuestion, log=print) -> tuple[str, bool]:
    """问两次独立比对。返回 (答案, 两次是否一致)。

    一致才算「双答确认」；不一致时再问一次取多数，仍然不一致就只当待验证，
    绝不把它当可信答案。
    """
    first = ask_ai(question)
    if not first:
        return "", False
    if question.qtype not in OBJECTIVE_TYPES:
        return first, False

    second = ask_ai(question, variant=1)
    if not second:
        return first, False
    if _same_answer(question, first, second):
        log("    双答一致，记为已确认")
        return first, True

    third = ask_ai(question)
    if third and _same_answer(question, first, third):
        log("    双答不一致，第三次支持第一答案，采用")
        return first, True
    if third and _same_answer(question, second, third):
        log("    双答不一致，第三次支持第二答案，采用")
        return second, True
    log("    双答不一致且无法定论，本题答案只作待验证")
    return first, False


def resolve_answer(question: ExamQuestion, library: Library) -> tuple[str, str]:
    """先查题库，未命中再问模型。返回 (答案, 来源)。"""
    fp = fingerprint(question.title, question.options_text, question.qtype)
    # 只复用「已确认」的答案。待验证的答案当初也是模型猜的、从没人核对过，
    # 拿它直接交卷会把过去的错误原样带上卷子（实测正确率明显偏低），
    # 所以宁可在考场上重新问一次。
    cached = library.lookup(fp)
    if cached:
        logger.info(f"题库命中（已确认）：{question.title[:32]} -> {cached}")
        return cached, "library"

    answer = ask_ai(question)
    if answer:
        try:
            library.upsert(fp, question.title, question.options_text,
                           question.qtype, answer, source="exam")
        except Exception:
            pass
        return answer, "ai"
    return "", "none"

# ---------- 考试列表 ----------
@dataclass
class ExamItem:
    exam_id: str
    enc_task: str
    name: str
    status: str = ""
    expire_time: str = ""


def list_exams(session: requests.Session, course: dict) -> list:
    """拉取一门课程的考试列表。"""
    from urllib.parse import parse_qs, urlparse

    resp = session.get(
        EXAM_LIST_URL,
        params={
            "courseId": str(course.get("courseId", "")),
            "classId": str(course.get("clazzId", "")),
            "cpi": str(course.get("cpi", "")),
        },
        timeout=20,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    items: list = []
    for li in soup.select("ul.nav li"):
        data = li.get("data") or ""
        if not data:
            continue
        query = parse_qs(urlparse(data).query)
        exam_id = (query.get("taskrefId") or [""])[0]
        enc_task = (query.get("enc_task") or [""])[0]
        if not exam_id:
            continue

        name_node = li.select_one("p")
        name = name_node.get_text().strip() if name_node else f"考试 {exam_id}"

        status = ""
        for span in li.select("span"):
            text = span.get_text().strip()
            if text in STATUS_WORDS:
                status = text
                break

        expire_node = li.select_one("span.fr")
        expire = expire_node.get_text().strip() if expire_node else ""

        items.append(ExamItem(exam_id=exam_id, enc_task=enc_task, name=name,
                              status=status, expire_time=expire))

    return items


def form_payload(html: str) -> dict:
    """从试卷页表单里取出结构性字段（paperId、openc、questionScore 等）。

    这些字段由服务端渲染，程序拼不出来，缺了平台就回「无效操作」。
    与答案、计时、暂存状态相关的字段剔除掉，由 submit 自己决定。
    """
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one("form#submitTest")
    if form is None:
        return {}

    own = {"tempSave", "timeOver", "subCount", "remainTime", "encRemainTime",
           "encLastUpdateTime", "enc", "userId", "start", "monitorforcesubmit",
           "answeredView", "hidetext", "version", "type", "questionId"}
    fields = {}
    for inp in form.select("input, textarea, select"):
        name = inp.get("name")
        if not name or name in own:
            continue
        if name.startswith("answer") or name.startswith("answers"):
            continue
        if name.startswith("type") or name.startswith("typeName"):
            continue
        fields[name] = inp.get("value") or ""
    return fields


def _save_subjective(items: list) -> Optional[Path]:
    """把主观题参考答案写成文本，方便复制粘贴到考试页面。"""
    lines = ["主观题参考答案", "",
             "程序不会替你提交这些题（提交格式未验证，怕填错反而害你丢分）。",
             "请对照题号自行作答后交卷。", ""]
    for no, title, answer in items:
        lines.append(f"【第 {no} 题】{title}")
        lines.append(answer)
        lines.append("")
    try:
        SUBJECTIVE_FILE.write_text("\n".join(lines), encoding="utf-8")
        return SUBJECTIVE_FILE.resolve()
    except OSError:
        return None


# ---------- 执行编排 ----------
def run_exam(session: requests.Session, course: dict, exam_id, enc_task,
             log=print, wait_face=None, delay: int = 15, max_questions: int = 500,
             on_progress=None, on_finish=None, attach: bool = False,
             retake: bool = False, enc: str = "", answer_id=None) -> int:
    """完整跑一场考试，返回已提交的题目数。

    wait_face(session) 在需要人脸时被调用，返回 True 表示使用者已自行完成验证。
    on_finish(submitted) 在结束时回调，submitted=False 表示有主观题没作答、故意没交卷。
    attach=True 时不自己开始考试，而是接管使用者已经点开的那场。
    retake=True 时先发起重考，再走正常流程。
    enc/answer_id 有值时直接用它进入答题（enc 由内嵌浏览器抓取，验证码已由浏览器过掉）。
    """
    sess = ExamSession(session, course, exam_id, enc_task)
    set_course_name((course or {}).get("title", ""))

    if enc:
        # 浏览器已经把验证码和签名都过掉了，这里直接用它的会话参数答题
        sess.enc = enc
        if answer_id:
            try:
                sess.exam_answer_id = int(answer_id)
            except (TypeError, ValueError):
                sess.exam_answer_id = answer_id
        sess.uid = session.cookies.get("_uid") or ""
        log("已从浏览器取得考场参数，开始答题")
    elif attach:
        sess.attach()
    else:
        if retake:
            log("正在发起重考…")
            sess.restart()
            log("重考已复位，准备进入考试")

        try:
            sess.open(reset=retake)
        except FaceRequired:
            if wait_face is None or not wait_face(sess):
                log("已暂停：本场考试需要人脸验证，请自行完成后重试")
                return 0
            log("已确认人脸验证完成，继续执行")
            sess.need_face = False

        try:
            sess.start()
        except ExamCompleted:
            raise
        except ExamError as exc:
            # 考试已经在作答中时，平台不给 302 而是回「继续考试」页，
            # 这时改用接管方式拿参数，不需要使用者在浏览器里做任何事。
            log(f"直接开始受阻（{exc}），改用接管方式继续…")
            sess.attach()
    log("考试已开始，按题库优先、模型兜底的顺序作答")

    library = Library()
    answered = 0
    already = 0
    index = 0
    # 跳过的主观题号：这些题程序没作答，交卷会直接记 0 分且不可改，所以要拦住交卷
    skipped_subjective: list[int] = []
    # 主观题的 AI 草稿，结束时写成文件给使用者抄
    subjective_answers: list[tuple] = []

    while index < max_questions:
        try:
            question = sess.fetch(index)
        except ExamCompleted:
            log("平台提示考试已提交，停止")
            break
        except ExamError as exc:
            # 题目拉不到通常意味着已经到卷尾
            log(f"第 {index + 1} 题未取到，视为卷尾（{exc}）")
            break

        if question.saved_answer:
            already += 1
            log(f"第 {index + 1} 题已有保存的答案，跳过")
            index += 1
            continue

        if question.qtype in SUBJECTIVE_TYPES:
            skipped_subjective.append(index + 1)
            label = question.type_name or "主观题"
            log(f"第 {index + 1} 题是{label}，程序不提交这类题，先把参考答案算给你")
            draft = ask_ai(question)
            if draft:
                subjective_answers.append((index + 1, question.title, draft))
                log(f"　参考答案：{draft[:80]}")
            else:
                log("　没能生成参考答案（检查模型配置），这题需要你自己作答")
            index += 1
            continue

        answer, source = resolve_answer(question, library)
        if not answer:
            log(f"第 {index + 1} 题没有取到答案，跳过")
            index += 1
            continue

        try:
            # 试卷页上的 paperId、openc 等字段必须原样回传，否则平台回「无效操作」
            sess.submit(question, answer, page_fields=form_payload(sess.last_html))
            answered += 1
            log(f"第 {index + 1} 题已提交（来源：{source}）→ {answer[:40]}")
            if on_progress:
                on_progress(answered, index + 1)
        except ExamError as exc:
            log(f"第 {index + 1} 题提交失败：{exc}")

        index += 1
        if index < max_questions:
            time.sleep(max(1, delay))

    if skipped_subjective:
        shown = "、".join(str(i) for i in skipped_subjective[:8])
        more = f" 等 {len(skipped_subjective)} 题" if len(skipped_subjective) > 8 else ""
        log(f"第 {shown}{more} 未作答（主观题），已自动作答 {answered} 题。")
        if subjective_answers:
            saved = _save_subjective(subjective_answers)
            if saved:
                log(f"这 {len(subjective_answers)} 道主观题的参考答案已存到：{saved}")
        log("为避免这些题交白卷，本次不自动交卷。请把主观题填完后再自行提交。")
        if on_finish:
            on_finish(False)
        return answered

    if answered + already == 0:
        log("本次一道题都没答上（请检查题库和模型配置），为避免交白卷，不自动交卷")
        if on_finish:
            on_finish(False)
        return 0

    try:
        sess.final_submit()
        log(f"已交卷，共提交 {answered} 道题")
        if on_finish:
            on_finish(True)
    except ExamError as exc:
        log(f"交卷失败：{exc}")
        if on_finish:
            on_finish(False)

    return answered
