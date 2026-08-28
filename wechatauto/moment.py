"""朋友圈（Moments）相关接口实现。

本模块提供两类接口：

- :class:`Moment`（UIA 路线，老架构）：微信 4.x 自绘界面已不暴露
  UIA 节点，此路线在 4.x 上不可用，仅保留兼容；
- :class:`MomentDB`（数据库路线，4.x 推荐）：直接读取微信本地
  ``sns.db`` 的 ``SnsTimeLine`` 表，内容为 ``SnsDataItem`` XML，
  可稳定获取全部朋友圈（含正文、图片/视频 md5、点赞、评论、定位）。

注：**发朋友圈（PublishMoments）功能已舍弃**——4.x 的发表为自绘
界面操作，无法可靠自动化；本模块仅保留朋友圈读取/点赞/评论能力。
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from wechatauto import uia
from wechatauto.languages import MOMENTS, get_lang
from wechatauto.logger import wxlog
from wechatauto.param import WxParam, WxResponse
from wechatauto.ui.base import BaseUISubWnd
from wechatauto.utils.tools import find_all_windows_from_root
from wechatauto.utils.win32 import SetClipboardText


def _lang(table, key: str) -> str:
    """根据当前语言环境返回对应文案。"""
    return get_lang(table, key)


def _is_time_line(text: str) -> bool:
    """粗略判断一行文本是否为时间信息。"""

    if not text:
        return False
    patterns = [
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{2}-\d{2}",
        r"\d{1,2}:\d{2}",
        r"昨[天日]",
        r"星期[一二三四五六日天]",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _split_like_names(text: str) -> List[str]:
    """解析点赞字符串。"""

    if not text:
        return []

    like_prefix = _lang(MOMENTS, '赞')
    text = text.strip()
    if text.startswith(like_prefix):
        text = text[len(like_prefix):].lstrip('：: ')

    sep = _lang(MOMENTS, '分隔符_点赞')
    if sep:
        parts = [part.strip() for part in text.split(sep) if part.strip()]
    else:
        parts = [name.strip() for name in re.split(r'[,:，]', text) if name.strip()]
    return parts


@dataclass
class MomentComment:
    """朋友圈评论数据结构。"""

    author: str
    content: str
    reply_to: Optional[str] = None
    raw: str = ''

    @classmethod
    def from_text(cls, text: str) -> 'MomentComment':
        text = text.strip()
        reply_to = None
        author = ''
        content = text

        # 格式示例："张三 回复 李四：你好" 或 "张三: 哈喽"
        match = re.match(r'^(?P<author>[^：:]+?)\s*(?:回复\s*(?P<reply>[^：:]+?)\s*)?[：:](?P<content>.*)$', text)
        if match:
            author = match.group('author').strip()
            reply_to = match.group('reply')
            if reply_to:
                reply_to = reply_to.strip()
            content = match.group('content').strip()
        else:
            author = ''
            content = text.strip()

        return cls(author=author, content=content, reply_to=reply_to, raw=text)


class MomentItem(BaseUISubWnd):
    """朋友圈单条动态。"""

    def __init__(self, control: uia.Control, parent: 'MomentList'):
        self.control = control
        self.parent = parent
        self.root = parent.root
        self._parsed = False
        self.nickname: str = ''
        self.content: str = ''
        self.location: Optional[str] = None
        self.time: str = ''
        self.likes: List[str] = []
        self.comments: List[MomentComment] = []
        self.image_count: int = 0
        self.is_advertisement: bool = False
        self._comment_controls: Dict[str, uia.Control] = {}

    # ----------------------------------------------------------------------------------------------
    # 数据解析
    # ----------------------------------------------------------------------------------------------

    def _ensure_parsed(self) -> None:
        if self._parsed:
            return

        raw_text = self.control.Name or ''
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        if lines:
            self.nickname = lines[0]

        body_lines = lines[1:]
        content_lines: List[str] = []
        comment_lines: List[str] = []
        likes_line: Optional[str] = None

        for line in body_lines:
            if not line:
                continue

            if re.search(_lang(MOMENTS, 're_图片数'), line):
                count = re.findall(r'\d+', line)
                if count:
                    self.image_count = int(count[0])
                continue

            if line.startswith(_lang(MOMENTS, '赞')):
                likes_line = line
                continue

            if line == _lang(MOMENTS, '评论'):
                # 后续均为评论
                comment_lines.extend(body_lines[body_lines.index(line) + 1:])
                break

            if _lang(MOMENTS, '广告') in line:
                self.is_advertisement = True
                continue

            if not self.time and _is_time_line(line):
                self.time = line
                continue

            content_lines.append(line)

        # 若未在循环中捕获评论，则继续检查剩余行
        if not comment_lines:
            collecting = False
            for line in body_lines:
                if line == _lang(MOMENTS, '评论'):
                    collecting = True
                    continue
                if collecting:
                    comment_lines.append(line)

        if likes_line:
            self.likes = _split_like_names(likes_line)

        self.content = '\n'.join(content_lines).strip()
        self.comments = [MomentComment.from_text(line) for line in comment_lines if line.strip()]

        # 记录可用于回复的控件
        for child in self.control.GetChildren():
            if child.ControlTypeName == 'TextControl':
                text = (child.Name or '').strip()
                if text:
                    self._comment_controls.setdefault(text, child)

        self._parsed = True

    # ----------------------------------------------------------------------------------------------
    # 对外属性访问
    # ----------------------------------------------------------------------------------------------

    @property
    def publisher(self) -> str:
        self._ensure_parsed()
        return self.nickname

    @property
    def text(self) -> str:
        self._ensure_parsed()
        return self.content

    @property
    def timestamp(self) -> str:
        self._ensure_parsed()
        return self.time

    @property
    def like_users(self) -> List[str]:
        self._ensure_parsed()
        return list(self.likes)

    @property
    def comment_list(self) -> List[MomentComment]:
        self._ensure_parsed()
        return list(self.comments)

    # ----------------------------------------------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------------------------------------------

    def find_comment(self, author: str) -> Optional[MomentComment]:
        self._ensure_parsed()
        for comment in self.comments:
            if comment.author == author:
                return comment
        return None

    def get_comment_control(self, comment: MomentComment) -> Optional[uia.Control]:
        self._ensure_parsed()
        key_candidates = [comment.raw, f"{comment.author}: {comment.content}", f"{comment.author}：{comment.content}"]
        for key in key_candidates:
            if key and key in self._comment_controls:
                return self._comment_controls[key]
        # fallback: 遍历匹配
        for text, ctrl in self._comment_controls.items():
            if comment.author and text.startswith(comment.author):
                if comment.content in text:
                    return ctrl
        return None


class MomentList(BaseUISubWnd):
    """朋友圈时间线列表。"""

    def __init__(self, parent: 'Moment'):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate_list(parent)
        self._items: Optional[List[MomentItem]] = None

    def _locate_list(self, parent: 'Moment') -> Optional[uia.Control]:
        wxlog.debug('尝试定位朋友圈列表控件')
        # 首先尝试通过常用 className 定位
        candidates: Iterable[uia.Control] = []
        try:
            candidates = parent._api.control.GetChildren()
        except Exception:
            candidates = []

        queue = list(candidates)
        visited = set()

        while queue:
            ctrl = queue.pop(0)
            if ctrl in visited:
                continue
            visited.add(ctrl)

            class_name = getattr(ctrl, 'ClassName', '') or ''
            automation_id = getattr(ctrl, 'AutomationId', '') or ''
            if ctrl.ControlTypeName == 'ListControl' and ('Moment' in class_name or 'moment' in automation_id.lower()):
                wxlog.debug(f'找到疑似朋友圈列表控件：{class_name}')
                return ctrl

            # 朋友圈列表一般会包含"评论"按钮
            children = []
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []

            if ctrl.ControlTypeName == 'ListControl':
                for child in children:
                    try:
                        if getattr(child, 'Name', '') == _lang(MOMENTS, '评论'):
                            wxlog.debug('通过子元素匹配到朋友圈列表控件')
                            return ctrl
                    except Exception:
                        continue

            queue.extend(children)

        wxlog.debug('未能定位到朋友圈列表控件')
        return None

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def refresh(self) -> None:
        self._items = None

    def get_items(self, refresh: bool = False) -> List[MomentItem]:
        if refresh or self._items is None:
            self._items = []
            if not self.control:
                return self._items

            try:
                children = self.control.GetChildren()
            except Exception:
                children = []

            for child in children:
                try:
                    if child.ControlTypeName in {'ListItemControl', 'CustomControl'}:
                        text = getattr(child, 'Name', '') or ''
                        if text.strip():
                            self._items.append(MomentItem(child, self))
                except Exception:
                    continue
        return list(self._items)


class Moment:
    """朋友圈接口封装。"""

    def __init__(self, wx_obj):
        self._wx = wx_obj
        self._api = wx_obj._api
        self.root = wx_obj._api
        self._list: Optional[MomentList] = None

    # ------------------------------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------------------------------

    def _ensure_list(self) -> Optional[MomentList]:
        if self._list and self._list.exists(0):
            return self._list

        try:
            self._wx.SwitchToMoments()
            time.sleep(0.2)
        except Exception:
            wxlog.debug('切换到朋友圈页面失败')
            return None

        self._list = MomentList(self)
        if not self._list.control:
            return None
        return self._list

    # ------------------------------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------------------------------

    def GetMoments(self, refresh: bool = False) -> List[MomentItem]:
        """获取朋友圈动态列表。

        Args:
            refresh: 是否强制刷新控件缓存。

        Returns:
            List[MomentItem]: 朋友圈动态对象列表。
        """

        moment_list = self._ensure_list()
        if not moment_list:
            return []
        return moment_list.get_items(refresh)

    def FindMomentByPublisher(self, nickname: str, refresh: bool = False) -> Optional[MomentItem]:
        """根据发布者昵称查找朋友圈动态。"""

        nickname = nickname.strip()
        for item in self.GetMoments(refresh=refresh):
            if item.publisher == nickname:
                return item
        return None

    # ------------------------------------------------------------------------------------------
    # 点赞与评论（部分功能依赖 UI 结构，尽量保证稳健）
    # ------------------------------------------------------------------------------------------

    def _invoke_action_menu(self, item: MomentItem) -> Optional['MomentActionMenu']:
        action_button = None
        try:
            for child in item.control.GetChildren():
                if child.ControlTypeName == 'ButtonControl':
                    action_button = child
                    break
        except Exception:
            action_button = None

        if action_button:
            action_button.Click()
        else:
            try:
                item.control.RightClick()
            except Exception:
                return None

        menu = MomentActionMenu(item)
        if not menu.exists(0.5):
            return None
        return menu

    def Like(self, item: MomentItem, cancel: bool = False) -> WxResponse:
        menu = self._invoke_action_menu(item)
        if not menu:
            return WxResponse.failure('未能打开朋友圈操作菜单')
        try:
            return menu.like(cancel)
        finally:
            menu.close()

    def Comment(self, item: MomentItem, content: str, reply_to: Optional[str] = None) -> WxResponse:
        if reply_to:
            comment = item.find_comment(reply_to)
            if not comment:
                return WxResponse.failure('未找到需要回复的评论')
            ctrl = item.get_comment_control(comment)
            if not ctrl:
                return WxResponse.failure('未定位到评论控件')
            ctrl.Click()
        else:
            menu = self._invoke_action_menu(item)
            if not menu:
                return WxResponse.failure('未能打开朋友圈操作菜单')
            try:
                result = menu.comment()
            finally:
                menu.close()
            if not result:
                return result

        dialog = MomentCommentDialog(self)
        if not dialog.exists(0.5):
            return WxResponse.failure('未弹出评论窗口')
        return dialog.send(content)


class MomentActionMenu(BaseUISubWnd):
    """朋友圈点赞/评论菜单。"""

    _win_cls_name: str = 'Qt51514QWindowToolSaveBits'

    def __init__(self, parent: MomentItem, timeout: float = 1.0):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate(timeout)

    def _locate(self, timeout: float) -> Optional[uia.Control]:
        t0 = time.time()
        while time.time() - t0 <= timeout:
            wins = find_all_windows_from_root(classname=self._win_cls_name, pid=self.root.pid)
            for win in wins:
                try:
                    children = win.GetChildren()
                except Exception:
                    children = []
                for child in children:
                    name = getattr(child, 'Name', '')
                    if name in {_lang(MOMENTS, '赞'), _lang(MOMENTS, '取消'), _lang(MOMENTS, '评论')}:
                        return win
            time.sleep(0.05)
        return None

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def _find_button(self, names: Iterable[str]) -> Optional[uia.Control]:
        if not self.control:
            return None
        target_names = list(names)
        try:
            children = self.control.GetChildren()
        except Exception:
            children = []
        for child in children:
            if child.ControlTypeName != 'ButtonControl':
                continue
            name = getattr(child, 'Name', '')
            if name in target_names:
                return child
        return None

    def like(self, cancel: bool = False) -> WxResponse:
        target_names = [_lang(MOMENTS, '赞')]
        if cancel:
            target_names.insert(0, _lang(MOMENTS, '取消'))

        button = self._find_button(target_names)
        if not button:
            return WxResponse.failure('未找到点赞按钮')
        button.Click()
        return WxResponse.success('操作成功')

    def comment(self) -> WxResponse:
        button = self._find_button([_lang(MOMENTS, '评论')])
        if not button:
            return WxResponse.failure('未找到评论按钮')
        button.Click()
        return WxResponse.success('已触发评论')

    def close(self) -> None:
        if not self.control:
            return
        try:
            self.control.SendKeys('{Esc}')
        except Exception:
            pass


class MomentCommentDialog(BaseUISubWnd):
    """朋友圈评论输入窗口。"""

    _win_cls_name: str = 'Qt51514QWindowToolSaveBits'

    def __init__(self, parent: Moment):
        self.parent = parent
        self.root = parent.root
        self.control = self._locate()
        if self.control:
            self._init_controls()

    def _locate(self) -> Optional[uia.Control]:
        wins = find_all_windows_from_root(classname=self._win_cls_name, pid=self.root.pid)
        for win in wins:
            try:
                children = win.GetChildren()
            except Exception:
                children = []
            for child in children:
                if child.ControlTypeName == 'ButtonControl' and getattr(child, 'Name', '') == _lang(MOMENTS, '发送'):
                    return win
        return None

    def _init_controls(self) -> None:
        self.edit: Optional[uia.Control] = None
        self.send_button: Optional[uia.Control] = None
        try:
            children = self.control.GetChildren()
        except Exception:
            children = []
        for child in children:
            if child.ControlTypeName == 'EditControl' and self.edit is None:
                self.edit = child
            elif child.ControlTypeName == 'ButtonControl' and getattr(child, 'Name', '') == _lang(MOMENTS, '发送'):
                self.send_button = child

    def exists(self, wait: float = 0) -> bool:  # type: ignore[override]
        if not self.control:
            return False
        try:
            return self.control.Exists(wait)
        except Exception:
            return False

    def send(self, content: str) -> WxResponse:
        if not self.exists(0):
            return WxResponse.failure('评论窗口不存在')

        if not content:
            return WxResponse.failure('评论内容不能为空')

        if not self.edit or not self.edit.Exists(0):
            return WxResponse.failure('未找到评论输入框')

        try:
            self.edit.Click()
            self.edit.SendKeys('{Ctrl}a')
            SetClipboardText(content)
            self.edit.SendKeys('{Ctrl}v')

            if self.send_button and self.send_button.Exists(0):
                self.send_button.Click()
            else:
                self.edit.SendKeys('{Enter}')
        except Exception as exc:  # pragma: no cover - UI 交互异常仅记录日志
            wxlog.debug(f'发送朋友圈评论失败：{exc}')
            return WxResponse.failure('发送评论失败')

        return WxResponse.success('评论成功')


# ===========================================================================
# 数据库路线（微信 4.x 推荐）：读取 sns.db SnsTimeLine
# ===========================================================================

def _parse_user_comment(block: str) -> dict:
    """解析单个 ``<user_comment>...</user_comment>`` 块。"""
    def tag(name: str) -> Optional[str]:
        m = re.search(r"<%s>([^<]*)</%s>" % (name, name), block)
        return m.group(1).strip() if m else None

    return {
        "username": tag("username") or "",
        "nickname": tag("nickname") or "",
        "content": tag("content") or "",
        "create_time": int(tag("create_time") or 0),
        "type": int(tag("type") or 0),
        "comment_id": tag("comment_id") or "",
        "ref_comment_id": tag("ref_comment_id") or "",
        "b_deleted": int(tag("b_deleted") or 0),
    }


class MomentDB:
    """微信 4.x 朋友圈数据库读取器（无需 UI/OCR）。

    数据来源：``sns.db`` 的 ``SnsTimeLine`` 表，``content`` 为
    ``SnsDataItem`` XML。支持解析正文、图片/视频 md5 与本地缓存路径、
    点赞、评论、定位。

    用法::

        from wechatauto import WeChatDB, MomentDB
        md = MomentDB(WeChatDB())
        for feed in md.get_moments(limit=10):
            print(feed["nickname"], feed["text"])
    """

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    def _open_sns(self):
        for rel, path, _ in self.db._db_files:
            if os.path.basename(path) == "sns.db":
                return self.db._open(rel)
        raise RuntimeError("未找到 sns.db（朋友圈库）")

    def get_moments(self, limit: int = 20, offset: int = 0,
                    username: Optional[str] = None) -> List[dict]:
        """读取朋友圈时间线（按 tid 倒序）。username 可过滤指定发布者。"""
        conn = self._open_sns()
        try:
            if username:
                rows = conn.execute(
                    "SELECT tid, user_name, content FROM SnsTimeLine "
                    "WHERE user_name=? ORDER BY tid DESC LIMIT ? OFFSET ?",
                    (username, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tid, user_name, content FROM SnsTimeLine "
                    "ORDER BY tid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        finally:
            conn.close()
        return [self.parse_feed(r["content"], r["user_name"]) for r in rows]

    def get_moment(self, tid: int) -> Optional[dict]:
        conn = self._open_sns()
        try:
            r = conn.execute(
                "SELECT tid, user_name, content FROM SnsTimeLine WHERE tid=?",
                (tid,),
            ).fetchone()
        finally:
            conn.close()
        return self.parse_feed(r["content"], r["user_name"]) if r else None

    def get_my_moments(self, limit: int = 20) -> List[dict]:
        return self.get_moments(limit=limit, username=self.db.wxid)

    def count(self) -> int:
        conn = self._open_sns()
        try:
            return conn.execute("SELECT count(*) FROM SnsTimeLine").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # XML 解析
    # ------------------------------------------------------------------
    def parse_feed(self, xml: str, fallback_user: str = "") -> dict:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8", "replace")
        m = re.search(r"<id>(\d+)</id>", xml)
        feed_id = m.group(1) if m else ""
        m = re.search(r"<username>([^<]+)</username>", xml)
        author = m.group(1) if m else (fallback_user or "")
        m = re.search(r"<createTime>(\d+)</createTime>", xml)
        create_time = int(m.group(1)) if m else 0
        m = re.search(r"<contentDesc>([^<]*)</contentDesc>", xml)
        text = html.unescape(m.group(1)) if m else ""
        m = re.search(r'<location latitude="([^"]*)" longitude="([^"]*)"', xml)
        location = {"latitude": m.group(1), "longitude": m.group(2)} if m else None

        # 图片 / 视频（mediaList 中的 url 带 md5）
        images, videos = [], []
        for mm in re.finditer(
            r'<media>.*?<url[^>]*md5="([0-9a-fA-F]{32})"[^>]*>(.*?)</url>', xml, re.S
        ):
            md5 = mm.group(1).lower()
            url = mm.group(2).strip()
            if any(ext in url.lower() for ext in (".mp4", ".mov", ".avi")):
                videos.append({"md5": md5, "url": url})
            else:
                images.append({"md5": md5, "url": url})

        # 点赞 / 评论
        likes, comments = [], []
        lm = re.search(r"<like_user_list>(.*?)</like_user_list>", xml, re.S)
        if lm:
            for blk in re.findall(r"<user_comment>(.*?)</user_comment>", lm.group(1), re.S):
                c = _parse_user_comment(blk)
                if c["username"]:
                    likes.append(c)
        cm = re.search(r"<comment_user_list>(.*?)</comment_user_list>", xml, re.S)
        if cm:
            for blk in re.findall(r"<user_comment>(.*?)</user_comment>", cm.group(1), re.S):
                c = _parse_user_comment(blk)
                if c["username"] and not c["b_deleted"]:
                    comments.append(c)

        return {
            "id": feed_id,
            "tid": fallback_user,
            "username": author,
            "nickname": self._nick(author),
            "text": text,
            "location": location,
            "create_time": create_time,
            "images": images,
            "videos": videos,
            "likes": likes,
            "comments": comments,
        }

    def _nick(self, username: str) -> str:
        if not username:
            return ""
        try:
            return self.db.get_nickname(username)
        except Exception:
            return username

    # ------------------------------------------------------------------
    # 媒体落地
    # ------------------------------------------------------------------
    @staticmethod
    def _cache_root(db) -> str:
        return os.path.join(db.account_dir, "cache")

    def find_local_media(self, md5: str, kind: str = "image") -> Optional[str]:
        """在 cache/<月>/Sns/<Img|Video> 下按 md5 查找本地缓存文件。"""
        if len(md5) < 2:
            return None
        sub = "Img" if kind == "image" else "Video"
        for root, _, files in os.walk(self._cache_root(self.db)):
            if os.sep + "Sns" + os.sep + sub not in os.sep + root + os.sep:
                continue
            for f in files:
                if f.startswith(md5):
                    return os.path.join(root, f)
        return None

    def download_media(self, media: dict, save_dir: Optional[str] = None,
                       kind: str = "image") -> Optional[str]:
        """下载朋友圈图片/视频：优先本地缓存，否则从 URL 拉取。"""
        save_dir = save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "wechatauto_moments"
        )
        os.makedirs(save_dir, exist_ok=True)
        md5 = (media or {}).get("md5")
        local = self.find_local_media(md5, kind) if md5 else None
        ext = ".jpg"
        if local:
            src = local
            data = open(src, "rb").read()
        else:
            url = (media or {}).get("url")
            if not url:
                return None
            try:
                import urllib.request
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)"}
                )
                data = urllib.request.urlopen(req, timeout=20).read()
            except Exception as e:
                wxlog.debug(f'下载朋友圈媒体失败：{e}')
                return None
            if url.lower().endswith((".mp4", ".mov", ".avi")):
                ext = ".mp4"
        out = os.path.join(save_dir, "%s%s" % (md5 or os.path.basename(url or "media"), ext))
        with open(out, "wb") as f:
            f.write(data)
        return out
