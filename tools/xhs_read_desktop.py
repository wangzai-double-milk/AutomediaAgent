"""Visible desktop feeds, note detail and profiles with real link provenance."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode

from tools import xhs_desktop as d


def stable_id(url, kind):
    pattern = (r"/(?:(?:explore|search_result)/|user/profile/[a-f0-9]{24}/)([a-f0-9]{24})"
               if kind == "note" else r"/user/profile/([a-f0-9]{24})")
    match = re.fullmatch(pattern, urlsplit(url or "").path)
    return match[1] if match else None


def query(url):
    parts = parse_qs(urlsplit(url or "").query)
    return {key: values[0] for key, values in parts.items() if len(values) == 1}


FILTERS = {"sort_by": ("排序依据", ["综合", "最新", "最多点赞", "最多评论", "最多收藏"]),
           "note_type": ("笔记类型", ["不限", "视频", "图文"]),
           "publish_time": ("发布时间", ["不限", "一天内", "一周内", "半年内"]),
           "search_scope": ("搜索范围", ["不限", "已看过", "未看过", "已关注"]),
           "location": ("位置距离", ["不限", "同城", "附近"])}


def filter_control(current, key, value):
    heading, values = FILTERS[key]
    header = d.choose(current, lambda n: n["role"] == "AXStaticText" and d.text(n) == heading)
    panel = d.descendants(current, header["parent"])
    headings = {label for label, _ in FILTERS.values()}
    start = next(i for i, n in enumerate(panel) if n["ref"] == header["ref"]) + 1
    end = next((i for i in range(start, len(panel)) if d.text(panel[i]) in headings), len(panel))
    scoped = {**current, "web": panel[start:end]}
    return d.choose(scoped, lambda n: n["role"] == "AXStaticText" and d.text(n) == value)


def apply_filters(filters):
    if not isinstance(filters, dict) or any(key not in FILTERS or value not in FILTERS[key][1]
                                           for key, value in filters.items()):
        raise ValueError("search_filter_invalid")
    from tools import task
    from tools.xhs_publish import atomic_json
    from PIL import Image
    directory = Path(task.ensure_task(hint="desktop_search_filters")).resolve()
    evidence = {}
    for key, value in filters.items():
        current = d.snapshot()
        if not any(d.text(n) == "排序依据" and d.visible(n) for n in current["web"]):
            d.act("hover", current, d.choose(current, lambda n: d.text(n) == "筛选"))
        current = d.wait(lambda s: any(d.text(n) == "排序依据" and d.visible(n) for n in s["web"]), 4)
        node = filter_control(current, key, value)
        d.act("press", current, node)
        # Selected options are red; no AX checked state is exposed by this site.
        current = d.snapshot()
        if not any(d.text(n) == "排序依据" and d.visible(n) for n in current["web"]):
            d.act("hover", current, d.choose(current, lambda n: d.text(n) == "筛选"))
            current = d.wait(lambda s: any(d.text(n) == "排序依据" for n in s["web"]), 4)
        node = filter_control(current, key, value)
        path = directory / ("filter_" + key + ".png")
        d.act("capture", current, node, path=str(path))
        with Image.open(path) as image:
            pixels = list(image.convert("RGB").getdata())
        red = sum(r > 165 and r - g > 55 and r - b > 65 for r, g, b in pixels) / len(pixels)
        if red < .04:
            raise RuntimeError("desktop_search_filter_not_selected: " + key)
        evidence[key] = {"value": value, "selected_red_fraction": red, "evidence_path": str(path)}
    current = d.snapshot()
    if any(d.text(n) == "收起" and d.visible(n) for n in current["web"]):
        d.press_label(["收起"], current)
    atomic_json(directory / "search_filters.json", evidence)
    return [(key, value) for key, value in filters.items()]


def feeds(current):
    records = {}
    for link in current["web"]:
        if link["role"] != "AXLink" or not d.visible(link):
            continue
        note_id = stable_id(link.get("url"), "note")
        if not note_id:
            continue
        children = d.descendants(current, link.get("parent"))
        authors = [n for n in children if n["role"] == "AXLink" and d.text(n)
                   and stable_id(n.get("url"), "user")]
        titles = [n for n in children if n["role"] == "AXLink" and d.text(n) != "仅自己可见" and d.text(n)
                  and stable_id(n.get("url"), "note") == note_id]
        author = authors[0] if len(authors) == 1 else None
        values = query(link.get("url"))
        author_texts = [d.text(n) for n in d.descendants(current, author["ref"])
                        if n["role"] == "AXStaticText" and d.text(n)] if author else []
        record = {"id": note_id, "title": d.text(titles[0]) if len(titles) == 1 else "",
                  "url": link["url"], "xsec_token": values.get("xsec_token", ""),
                  "xsec_source": values.get("xsec_source"),
                  "author": author_texts[0] if author_texts else d.text(author) if author else "",
                  "author_id": stable_id(author["url"], "user") if author else None,
                  "author_url": author.get("url") if author else None,
                  "like_count": None, "type": None, "cover_url": None,
                  "transport": "macOS_accessibility"}
        if note_id not in records or record["title"]:
            records[note_id] = record
    return list(records.values())


def note_scope(current):
    """Smallest real ancestor containing the title and comment composer."""
    parents = {n["ref"]: n for n in current["web"]}
    headings = [n for n in current["web"] if n["role"] == "AXHeading" and d.text(n)]
    if len(headings) != 1:
        raise RuntimeError("desktop_note_detail_scope_missing")
    ancestor = headings[0].get("parent")
    while ancestor in parents:
        children = d.descendants(current, ancestor)
        areas = [n for n in children if n["role"] == "AXTextArea"]
        if len(areas) == 1:
            if parents[ancestor]["role"] == "AXWebArea":
                break
            return {**current, "web": children}, headings[0], areas[0]
        ancestor = parents[ancestor].get("parent")
    raise RuntimeError("desktop_note_overlay_not_identified")


def comments(current, include_refs=False):
    """Group each loaded comment through its own author ancestor, never flatten."""
    authors = [n for n in current["web"] if n["role"] == "AXLink" and d.text(n)
               and stable_id(n.get("url"), "user")]
    records = []
    for author in authors:
        nodes = {n["ref"]: n for n in current["web"]}
        ancestor = author.get("parent")
        while ancestor in nodes:
            children = d.descendants(current, ancestor)
            other = [n for n in children if n in authors]
            has_reply = any(d.text(n) == "回复" or re.fullmatch(r"展开 \d+ 条回复", d.text(n))
                            for n in children)
            # Numeric reply counts are exposed in the same paired icon rows.
            if len(other) == 1 and (has_reply or len([n for n in children if n["role"] == "AXImage"]) >= 3):
                text_nodes = [n for n in children if n["role"] == "AXStaticText" and d.text(n).strip()
                              and n.get("parent") != author["ref"]]
                body = [n for n in text_nodes if not re.fullmatch(
                    r"[\d.]+|作者|置顶|刚刚|赞|回复|展开 \d+ 条回复|(?:\d{4}-)?\d\d?-\d\d?|.*(?:天前|分钟前|小时前)|昨天.*|今天.*",
                    d.text(n))]
                # The body is the first content group after the author, before date/location.
                if body:
                    group = body[0]["parent"]
                    content = "".join(d.text(n) for n in text_nodes if n.get("parent") == group)
                    record = {"id": None, "author_id": stable_id(author["url"], "user"),
                              "author": d.text(author), "content": content, "replies": None,
                              "author_url": author["url"]}
                    if include_refs:
                        record.update(_ref=ancestor, _thread_ref=nodes[ancestor].get("parent"))
                    records.append(record)
                break
            if len(other) > 1:
                break
            ancestor = nodes[ancestor].get("parent")
    return records


def read_detail(current, feed_id):
    if stable_id(current.get("page_url"), "note") != feed_id:
        raise RuntimeError("desktop_detail_note_id_mismatch")
    scoped, heading, composer = note_scope(current)
    nodes = {n["ref"]: n for n in scoped["web"]}
    section = d.descendants(scoped, heading["parent"])
    siblings = [n for n in section if n.get("parent") == heading["parent"]]
    following = siblings[siblings.index(heading) + 1:]
    # The website's body is the direct content group after its heading.
    # A no-topic note must not fall back to the whole detail panel.
    body_group = next((n for n in following if n["role"] == "AXGroup"), None)
    body_nodes = d.descendants(scoped, body_group["ref"]) if body_group else []
    body = "".join(d.text(n) for n in body_nodes if n["role"] == "AXStaticText")
    authors = [n for n in scoped["web"] if n["role"] == "AXLink" and d.text(n)
               and stable_id(n.get("url"), "user") and d.visible(n)
               and n["bounds"]["y"] < heading["bounds"]["y"]]
    if len(authors) != 1:
        raise RuntimeError("desktop_note_author_ambiguous")
    author = authors[0]
    rows = d.descendants(scoped, nodes[composer["parent"]]["parent"])
    counts = [d.text(n) for n in rows if n["role"] == "AXStaticText" and re.fullmatch(r"[\d.]+万?", d.text(n))]
    loaded = comments({**scoped, "web": [n for n in scoped["web"]
                                        if n.get("bounds") and n["bounds"]["y"] > heading["bounds"]["y"]]})
    total = [re.fullmatch(r"共 (\d+) 条评论", d.text(n)) for n in scoped["web"]]
    total = [match[1] for match in total if match]
    follow = [d.text(n) for n in scoped["web"] if n["role"] == "AXButton"
              and d.text(n) in {"关注", "已关注", "互相关注", "取消关注"}]
    return {"ok": True, "feed_id": feed_id, "note": {"id": feed_id, "title": d.text(heading), "desc": body,
            "author": d.text(author), "author_id": stable_id(author["url"], "user"),
            "author_url": author["url"], "type": None, "images": [], "video_url": None,
            "tags": [d.text(n)[1:] for n in body_nodes if n["role"] == "AXLink" and d.text(n).startswith("#")],
            "interact_info": {"liked": None, "collected": None,
                              "like_count": counts[0] if len(counts) == 3 else None,
                              "collected_count": counts[1] if len(counts) == 3 else None,
                              "comment_count": counts[2] if len(counts) == 3 else None},
            "following": follow[0] in {"已关注", "互相关注", "取消关注"} if len(follow) == 1 else None},
            "comments": loaded, "comment_count": int(total[0]) if len(total) == 1 else None,
            "loaded_comment_count": len(loaded), "all_comments_loaded": False,
            "evidence_url": current["page_url"], "structured": True,
            "media_extracted": False, "transport": "macOS_accessibility"}


def open_detail(feed_id, token, source=None):
    if not re.fullmatch(r"[a-f0-9]{24}", feed_id):
        raise ValueError("feed_id must be an observed 24-character note ID")
    d.select_site()
    current = d.snapshot()
    if stable_id(current.get("page_url"), "note") == feed_id:
        note_scope(current)
        return current
    links = [n for n in current["web"] if n["role"] == "AXLink" and d.visible(n)
             and stable_id(n.get("url"), "note") == feed_id
             and query(n.get("url")).get("xsec_token") == token]
    if not links:
        raise RuntimeError("desktop_note_link_not_observed; reopen_origin_feed_or_search")
    links.sort(key=lambda n: bool(d.text(n)), reverse=True)
    d.act("press", current, links[0])
    def ready(s):
        if stable_id(s.get("page_url"), "note") != feed_id:
            return False
        try:
            note_scope(s)
            return True
        except RuntimeError:
            return False
    return d.wait(ready, 12)


class Actions:
    """Existing action signatures; bounded desktop evidence, no CDP discovery."""

    def __init__(self, args=None):
        self.args = args or {}

    def check_login(self):
        with d._lock:
            d.select_site()
            current = d.snapshot()
            links = [n for n in current["web"] if n["role"] == "AXLink" and d.text(n) == "我"
                     and stable_id(n.get("url"), "user") and d.visible(n)]
            if len(links) == 1:
                return {"ok": True, "logged_in": True, "user_id": stable_id(links[0]["url"], "user"),
                        "nickname": None, "evidence_url": current["page_url"],
                        "transport": "macOS_accessibility"}
            login = any(re.search(r"扫码登录|手机号登录|登录后查看", d.text(n)) for n in current["web"])
            return {"ok": login, "logged_in": False if login else None, "reason": "login_required" if login
                    else "desktop_login_state_not_observed"}

    def list_feeds(self):
        return self.search_feeds(None)

    def search_feeds(self, keyword, filters=None):
        with d._lock:
            d.select_site()
            current = d.snapshot()
            areas = [n for n in current["web"] if n["role"] == "AXTextArea" and n.get("value")]
            if areas:
                raise RuntimeError("desktop_unsent_composer_text_present")
            url = "https://www.xiaohongshu.com/explore" if keyword is None else (
                "https://www.xiaohongshu.com/search_result?" + urlencode(
                    {"keyword": keyword, "source": "web_explore_feed"}))
            d.navigate(url)
            d.wait(lambda s: bool(feeds(s)) or any(re.search(r"暂无相关|没有找到", d.text(n))
                                                 for n in s["web"]), 15)
            applied = apply_filters(filters) if filters else []
            current = d.wait(lambda s: bool(feeds(s)) or any(re.search(r"暂无相关|没有找到", d.text(n))
                                                           for n in s["web"]), 15)
            records = feeds(current)
            return {"ok": True, "keyword": keyword, "count": len(records), "feeds": records,
                    "filters_applied": applied, "visible_page_only": True,
                    "evidence_url": current["page_url"], "transport": "macOS_accessibility"}

    def get_feed_detail(self, feed_id, xsec_token, load_comments=True, xsec_source=None):
        with d._lock:
            current = open_detail(feed_id, xsec_token, xsec_source)
            result = read_detail(current, feed_id)
            result["requested_load_comments"] = load_comments
            # These are already loaded AX comments, not a claim to have exhausted pagination.
            return result

    def user_profile(self, user_id, xsec_token="", xsec_source=None):
        with d._lock:
            d.select_site()
            if not re.fullmatch(r"[a-f0-9]{24}", user_id):
                raise ValueError("user_id must be observed")
            current = d.snapshot()
            if stable_id(current.get("page_url"), "user") != user_id:
                links = [n for n in current["web"] if n["role"] == "AXLink" and d.text(n)
                         and stable_id(n.get("url"), "user") == user_id and d.visible(n)]
                if not links:
                    raise RuntimeError("desktop_profile_link_not_observed")
                # Navigating the exact visible href avoids the site's avatar new-tab behavior.
                d.navigate(links[0]["url"])
            current = d.wait(lambda s: stable_id(s.get("page_url"), "user") == user_id
                             and any(re.search(r"小红书号", d.text(n)) for n in s["web"]), 12)
            page = d.page_summary(current)
            nodes = {n["ref"]: n for n in current["web"]}
            labels = [n for n in current["web"] if n["role"] == "AXStaticText"
                      and d.text(n).startswith("小红书号：")]
            basic = {"nickname": None, "following": None, "followers": None, "likes_and_collections": None}
            if len(labels) == 1:
                group = d.descendants(current, labels[0]["parent"])
                # Nickname is the adjacent preceding heading group in the profile header.
                header = d.descendants(current, nodes[labels[0]["parent"]]["parent"])
                names = [d.text(n) for n in header if n["role"] == "AXStaticText" and d.visible(n)
                         and n["bounds"]["y"] < labels[0]["bounds"]["y"] and d.text(n)]
                basic["nickname"] = names[0] if len(names) == 1 else None
                basic["red_id"] = d.text(labels[0]).split("：", 1)[1]
            for label, key in (("关注", "following"), ("粉丝", "followers"), ("获赞与收藏", "likes_and_collections")):
                values = []
                for node in current["web"]:
                    if node["role"] == "AXStaticText" and d.text(node) == label:
                        group = d.descendants(current, node["parent"])
                        numbers = [d.text(n) for n in group if n["role"] == "AXStaticText"
                                   and re.fullmatch(r"[\d.]+万?", d.text(n))]
                        if len(numbers) == 1:
                            values.append(numbers[0])
                basic[key] = values[0] if len(values) == 1 else None
            access = current.get("account_access", {})
            restricted = access.get("writes_blocked") and access.get("user_id") == user_id
            return {"ok": not restricted, "status": "restricted" if restricted else "ok",
                    "account_access": access, "user_id": user_id, "basic_info": basic,
                    "notes": feeds(current), "notes_count": None, "page": page,
                    "visible_page_only": True, "evidence_url": current["page_url"]}
