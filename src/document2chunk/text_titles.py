"""text_titles —— .md/.txt 缺失标题复原（行级启发式，纯函数）。

背景（serve 的 .md/.txt 早退路径此前直通/仅转码，无结构复原）：网页转存 md 与
OCR txt 普遍没有 ``#`` 标题，标题层级以粗体 ``**``、中文编号（一、/（一）/第X章/
节/条）表达，甚至与正文粘连在同一段。本模块把这些样式复原为 ``#``~``####``。

领域知识来源：WebCrawler/chunker ``structure.py`` 的样式层级表
（cn_major=章级、cn_minor=节级、article=条级，数字枚举不入层级）；正则与
``postprocess.style_of`` 同源。与 chunker 的主要差异：

- chunker 只看块首行（md 段落形态）；本模块逐行扫描（OCR txt 的标题在块中间）。
- chunker 的 ``RE_CN_MAJOR`` 带 ``$`` 锚点导致粘连拆分是死代码；本模块以
  「样式前缀 + 句号切分 + 标题短语限长」真正实现拆分。
- 目录区识别（连续短样式行）为本模块新增——OCR txt 的目录行常无页码后缀。

无损不变量：复原只加 ``#`` 前缀、删标题上的 ``**``、增删空白——不删其他任何
字符（标题保留句尾 ``。``）。因此 ``restore_titles`` 幂等（已复原/已有 ``#`` 的
输入原样返回），``_strip_markup`` 归一化后输入输出相等可作测试断言。

标题层级映射（对齐 chunker ``_STYLE_LEVEL``）::

    文档大标题            → #
    第X章 / 一、          → ##
    第X节 / （一）        → ###
    第X条                 → ####
    数字枚举 1. / （1）   → 不提升（列表项，chunker 同）
"""

from __future__ import annotations

import re

_NUM = r"[一二三四五六七八九十百千零〇两]+"

RE_MD_HEADING = re.compile(r"^#{1,6}\s")
RE_CHAPTER = re.compile(rf"^(第{_NUM}章)")
RE_SECTION = re.compile(rf"^(第{_NUM}节)")
RE_ARTICLE = re.compile(rf"^(第{_NUM}条)")
RE_CN_MAJOR = re.compile(rf"({_NUM})、")
RE_CN_MINOR = re.compile(rf"^[（(]({_NUM})[）)]")

_STYLE_REGEX = {
    "chapter": RE_CHAPTER,
    "section": RE_SECTION,
    "article": RE_ARTICLE,
    "cn_major": RE_CN_MAJOR,
    "cn_minor": RE_CN_MINOR,
}
_STYLE_LEVEL = {
    "chapter": 2,
    "cn_major": 2,
    "section": 3,
    "cn_minor": 3,
    "article": 4,
}

# 整行样式标题：整行长度上限（超过视为正文/列表项）
_WHOLE_LINE_MAX = 50
# 粘连拆分：样式编号后到首个「。」的标题短语长度上限（超过视为列表项/正文）
_SPLIT_HEAD_MAX = 30
# 粘连拆分：剩余正文最短长度（对齐 chunker _HEADING_BODY_THRESHOLD；更短则整行作标题）
_SPLIT_BODY_MIN = 15
# 目录区：连续 ≥N 个「短样式行」（中间仅隔空行）→ 全部视为目录行
_TOC_RUN = 3
# 文档大标题扫描窗口（前 N 个非空行）
_TITLE_WINDOW = 8
_DOC_TITLE_MAX = 60
# 整行粗体大标题限长（粗体本身是强信号，宽于纯文本候选——六部门联合发文标题可达 61 字）
_DOC_TITLE_BOLD_MAX = 80
_TITLE_KEYWORDS = (
    "关于", "《", "的通知", "的意见", "办法", "规划", "纲要", "方案",
    "条例", "规定", "指引", "解读", "工作要点", "白皮书",
)
# 行尾为这些标点 → 不是标题（列表项常以 ；/， 收尾）；句号「。」不算（无损保留）
_NOT_TITLE_END = "；;，,、：:！？"
# 文档大标题候选行的开头守卫（表格/图片/代码/分割线/引用/列表/斜体星号）
_TITLE_GUARD_START = "<{|#[`!->=—*_+"
# 目录行特征：点引导符（含 ASCII 句点串 …….22 / ····|22）或 |页码 后缀
_RE_TOC_DOT = re.compile(r"[.·•‧∙⋅。]{3,}|…+|\|\s*\d+\s*$")
# 行内出现 ≥2 个数字枚举标记（1.xxx 2.xxx）→ 疑似内联列表，不作整行标题
_RE_INLINE_ENUM = re.compile(r"\d+[.、]")
# 机构名结尾（大标题两行拼接：机构行 + 关于行）
_ORG_TAIL = re.compile(r"(国务院|部|委|局|院|厅|办公室|集团|银行|署|委员会|总公司)$")


# ==================== 归一化与样式识别 ====================


def _strip_markup(text: str) -> str:
    """归一化：去掉 # 前缀、``**``、全部空白（含 NBSP/全角）——用于「正文无损」断言。"""
    joined = "".join(re.sub(r"^#{1,6}\s*", "", ln) for ln in text.splitlines())
    return re.sub(r"\s+", "", joined.replace("**", ""))


def _norm(line: str) -> str:
    """行归一化（识别用）：剥 ``**``、去首尾空白（含全角空格）。"""
    return line.replace("**", "").strip().strip("　").strip()


def _style_kind(text: str) -> str | None:
    """文本的样式类型（chapter/section/article/cn_major/cn_minor），未命中返回 None。"""
    for kind, regex in _STYLE_REGEX.items():
        if regex.match(text):
            return kind
    return None


def _is_toc_line(norm: str) -> bool:
    """目录行特征：点引导符（····）或 |页码 后缀。"""
    return bool(_RE_TOC_DOT.search(norm))


def _clean_heading(text: str) -> str:
    """标题文本清洗：剥 ``**``、合并空白、去行尾列表标点（保留句号——无损不变量）。"""
    text = text.replace("**", "")
    text = re.sub(r"\s+", " ", text.strip().strip("　")).strip()
    return text.rstrip(_NOT_TITLE_END)


# ==================== 保护区标记 ====================


def _mark_protected(lines: list[str]) -> list[bool]:
    """标记不参与复原的行：YAML frontmatter、```/~~~ 代码围栏内部。"""
    protected = [False] * len(lines)

    # frontmatter：首行 ---，且 50 行内找到闭合 --- 且中间有 key: 行
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 50)):
            if lines[j].strip() == "---":
                if any(":" in ln for ln in lines[1:j]):
                    for k in range(j + 1):
                        protected[k] = True
                break

    # 代码围栏（围栏行本身与内部都保护）
    fence = None
    for i, ln in enumerate(lines):
        st = ln.lstrip()
        if fence is None:
            if st.startswith("```") or st.startswith("~~~"):
                fence = st[:3]
                protected[i] = True
        else:
            protected[i] = True
            if st.startswith(fence):
                fence = None
    return protected


def _mark_toc_runs(lines: list[str], protected: list[bool]) -> list[bool]:
    """标记目录区：连续 ≥_TOC_RUN 个「短样式行」（中间仅隔空行）。

    只统计整行长度 ≤_WHOLE_LINE_MAX 的样式行——章条文档的条文
    （第X条+长正文粘连）不会把章/条串成「目录」。
    """
    toc = [False] * len(lines)
    run_idx: list[int] = []
    for i, ln in enumerate(lines):
        if protected[i]:
            continue  # 围栏/frontmatter 不打断也不计入
        norm = _norm(ln)
        if norm and _style_kind(norm) is not None and len(norm) <= _WHOLE_LINE_MAX:
            run_idx.append(i)
        elif not ln.strip():
            continue  # 空行不打断串
        else:
            if len(run_idx) >= _TOC_RUN:
                for k in run_idx:
                    toc[k] = True
            run_idx = []
    if len(run_idx) >= _TOC_RUN:
        for k in run_idx:
            toc[k] = True
    return toc


# ==================== 文档大标题 ====================


def _has_md_heading(lines: list[str]) -> bool:
    return any(RE_MD_HEADING.match(ln) for ln in lines)


def _title_candidate(line: str) -> str | None:
    """文档大标题候选：返回清洗后标题文本，非候选返回 None。

    条件：含标题关键词（关于/《/的通知/办法/规划…）、长度 ≤_DOC_TITLE_MAX、
    不以句号等结尾、不以守卫字符开头（表格/图片/代码/分割线）。
    """
    st = _norm(line)
    if not st or len(st) > _DOC_TITLE_MAX:
        return None
    if st[0] in _TITLE_GUARD_START:
        return None
    if st[-1] in "。！？；，、:：)）":
        return None
    if not any(kw in st for kw in _TITLE_KEYWORDS):
        return None
    return st


def _next_content_line(lines: list[str], i: int, protected: list[bool]) -> int | None:
    """i 之后 5 行内最近的非空非保护行下标。"""
    for j in range(i + 1, min(len(lines), i + 5)):
        if not protected[j] and lines[j].strip():
            return j
    return None


def _find_doc_title(
    lines: list[str], protected: list[bool], toc: list[bool]
) -> tuple[int, int, str, str | None] | None:
    """扫描前 _TITLE_WINDOW 个非空行找文档大标题。

    返回 (起始行, 结束行含, 标题, 尾随文本或 None)——尾随文本是标题行上
    粗体闭合后的残余（`**标题**国科发资〔2020〕132号` 的文号），单独成行保留。
    覆盖形态：单行完整标题；粗体+文号尾随；机构行+关于行两行拼接
    （`中共中央 国务院` / `关于…的意见`）；OCR 断行（`…关于印发` / `《…》的通知`）。
    """
    seen = 0
    for i, ln in enumerate(lines):
        if seen >= _TITLE_WINDOW:
            break
        if protected[i] or toc[i] or not ln.strip():
            continue
        seen += 1
        if RE_MD_HEADING.match(ln):
            break  # 已有 # 标题 → 不补大标题
        norm = _norm(ln)
        if _style_kind(norm) is not None:
            break  # 正文样式标题起头（如直接以「一、」开头的文件）→ 无大标题
        st = ln.strip()
        # 粗体开头：整行粗体，或粗体+尾随（文号等）
        if st.startswith("**") and len(st) > 4:
            close = st.find("**", 2)
            if close >= 4:
                inner = _clean_heading(st[2:close])
                tail = st[close + 2:].strip()
                if inner and len(inner) <= _DOC_TITLE_BOLD_MAX:
                    return i, i, inner, tail or None
                break
        cand = _title_candidate(ln)
        if cand is not None:
            # OCR 断行：候选以 关于/关于印发 收尾 → 拼下一行
            if cand.endswith("关于") or cand.endswith("关于印发"):
                nxt = _next_content_line(lines, i, protected)
                if nxt is not None:
                    joined = cand + _norm(lines[nxt])
                    if len(joined) <= 80 and _title_candidate(joined):
                        return i, nxt, _clean_heading(joined), None
            return i, i, cand, None
        # 机构行 + 关于行拼接（`中共中央 国务院` + `关于…的意见`）
        nxt = _next_content_line(lines, i, protected)
        if nxt is not None:
            joined = norm + _norm(lines[nxt])
            if (
                len(norm) <= 20
                and (_ORG_TAIL.search(norm) or norm.endswith("关于印发"))
                and _title_candidate(joined)
            ):
                return i, nxt, _clean_heading(joined), None
    return None


# ==================== 样式行 → 标题 ====================


def _split_style_heading(norm: str, kind: str) -> tuple[str, str | None] | None:
    """样式行 → (标题文本, 正文或 None)；不构成标题返回 None。

    优先粘连拆分（句号切分），否则整行作标题：

    - 粘连拆分：样式后到首个「。」的短语 ≤_SPLIT_HEAD_MAX 且剩余正文
      ≥_SPLIT_BODY_MIN → 拆（`（一）指导思想。以习…` → 标题+正文）
    - 整行标题：整行 ≤_WHOLE_LINE_MAX 且不以列表标点收尾
      （`一、总体要求`；正文过短的粘连也归此路，对齐 chunker）
    - 第X条特例：规章条文编号后直接接正文（`第一条为完善…`），
      标题取「第X条」、其余全部为正文（句号切分不适用）
    """
    regex = _STYLE_REGEX[kind]
    m = regex.match(norm)
    if m is None:
        return None
    prefix = m.group(0)
    rest = norm[m.end():].strip()

    if kind == "article":
        if not rest:
            return _clean_heading(prefix), None  # 第X条（整行仅编号）
        sep = norm[m.end():m.end() + 1]  # 编号后紧跟的字符
        if sep in (" ", "　"):
            # 「第X条 标题词」短词 → 整行作标题；「第X条 完整句子。」→ 编号+正文
            if len(rest) <= 20 and "。" not in rest and rest[-1] not in _NOT_TITLE_END:
                return _clean_heading(norm), None
            if len(norm) <= _WHOLE_LINE_MAX + 20 or "。" in rest:
                return _clean_heading(prefix), rest
            return None
        # 「第X条正文」直连（规章惯例）→ 标题取编号、正文拆出（不受
        # _SPLIT_BODY_MIN 限制——`第十条下列行为属于…：`这类短引语须独立成段）
        if len(rest) >= 5:
            return _clean_heading(prefix), rest
        return _clean_heading(norm), None

    if not rest:
        return _clean_heading(prefix), None
    dot = rest.find("。")
    if 0 <= dot <= _SPLIT_HEAD_MAX:
        head = prefix + rest[:dot]
        body = rest[dot + 1:].strip()
        if len(body) >= _SPLIT_BODY_MIN:
            return _clean_heading(head), body
        return _clean_heading(norm), None  # 正文过短 → 整行作标题
    if (
        len(norm) <= _WHOLE_LINE_MAX
        and norm[-1] not in _NOT_TITLE_END
        and len(_RE_INLINE_ENUM.findall(norm)) < 2  # 内联数字列表 → 非标题
    ):
        return _clean_heading(norm), None
    return None


def _handle_bold_line(st: str) -> tuple[int, str, str | None] | None:
    """粗体行 → (层级, 标题, 正文或 None)；非标题粗体返回 None。

    - ``**一、xxx。**`` 整行粗体：粗体内容按样式规则提升
    - ``**一、xxx。**正文…`` 粗体开头+尾随正文：粗体内句号切标题，
      正文 = 粗体内剩余 + 尾随正文（原顺序直连，无损）
    - 拆不出足量正文（尾随 <_SPLIT_BODY_MIN 且粗体内无句号）→ None 原样保留
    """
    close = st.find("**", 2)
    if close < 4:
        return None
    inner = st[2:close]
    after = st[close + 2:].strip()
    norm_inner = _norm(inner)
    kind = _style_kind(norm_inner)
    if kind is None:
        return None
    level = _STYLE_LEVEL[kind]

    if not after:
        split = _split_style_heading(norm_inner, kind)
        if split is None:
            return None
        head, body = split
        return level, head, body

    # 粗体 + 尾随正文
    m = _STYLE_REGEX[kind].match(norm_inner)
    prefix, rest = m.group(0), norm_inner[m.end():].strip()
    dot = rest.find("。")
    if 0 <= dot <= _SPLIT_HEAD_MAX:
        head = _clean_heading(prefix + rest[:dot])
        inner_body = rest[dot + 1:].strip()
    else:
        head = _clean_heading(norm_inner)
        inner_body = ""
    body = inner_body + after
    if len(body) >= _SPLIT_BODY_MIN:
        return level, head, body
    return None


# ==================== 主入口 ====================


def _emit_body(body: str) -> list[str]:
    """正文段输出：正文自身以样式开头时（`**二、目标**　（一）xxx。正文` 拆出的
    正文里嵌着下一级标题）递归再拆，保证单遍即收敛（幂等）。"""
    pieces: list[str] = []
    cur = body
    for _ in range(5):  # 递归深度保险
        got = _try_transform(cur.strip())
        if got is None:
            pieces.append(cur)
            return pieces
        pieces.extend(got)
        if len(got) < 3:  # [标题] —— 正文被整体吸收
            return pieces
        cur = got[-1]
        # got = [标题, "", 余下正文] → pieces 已含 [标题, ""], cur 继续处理
        # 上一轮 extend 会把 cur 也放进去，这里回退
        pieces = pieces[:-1]
    pieces.append(cur)
    return pieces


def _try_transform(st: str) -> list[str] | None:
    """单行（strip 后）→ 输出行列表（[标题] 或 [标题, "", 正文…]）；None = 原样。

    注意：非粗体行不做 ``**`` 剥离——中段粗体（`加强**一、**类项目`）剥后
    可能伪造样式前缀；粗体形态统一走 _handle_bold_line。
    """
    if st.startswith("**"):
        got = _handle_bold_line(st)
        if got is None:
            return None
        level, head, body = got
        hashes = "#" * level
        if body:
            return [f"{hashes} {head}", "", *_emit_body(body)]
        return [f"{hashes} {head}"]

    kind = _style_kind(st)
    if kind is None or _is_toc_line(st):
        return None
    split = _split_style_heading(st, kind)
    if split is None:
        return None
    head, body = split
    hashes = "#" * _STYLE_LEVEL[kind]
    if body:
        return [f"{hashes} {head}", "", *_emit_body(body)]
    return [f"{hashes} {head}"]


def restore_titles(text: str) -> str:
    """把缺失 ``#`` 标题的 md/txt 全文复原标题层级（纯函数、幂等、无损）。

    只改标题：加 ``#`` 前缀、删标题上的 ``**``、粘连处插空行；正文、表格、
    图片标记、OCR 噪声一律原样。调用方（serve）负责解码与异常兜底。
    """
    if not text.strip():
        return text
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    trailing = lines and lines[-1] == ""  # 末尾换行保留
    if trailing:
        lines = lines[:-1]

    protected = _mark_protected(lines)
    toc = _mark_toc_runs(lines, protected)

    title_span: tuple[int, int] | None = None
    title_text = ""
    title_tail: str | None = None
    if not _has_md_heading(lines):
        found = _find_doc_title(lines, protected, toc)
        if found is not None:
            title_span = (found[0], found[1])
            title_text = found[2]
            title_tail = found[3]

    out: list[str] = []
    n = len(lines)
    for i, ln in enumerate(lines):
        st = ln.strip()

        # 保护区/目录区/空行/已有 # 标题 → 原样
        if protected[i] or toc[i] or not st or RE_MD_HEADING.match(ln):
            out.append(ln)
            continue

        # 大标题行（含两行拼接的第二行：跳过，已被首行吸收）
        if title_span is not None and title_span[0] <= i <= title_span[1]:
            if i == title_span[0]:
                if out and out[-1].strip():
                    out.append("")
                out.append(f"# {title_text}")
                if title_tail:
                    out.append("")
                    out.append(title_tail)
                if title_span[1] > i and i + 1 < n:
                    out.append("")
            continue

        got = _try_transform(st)
        if got is not None:
            if out and out[-1].strip():
                out.append("")
            out.extend(got)
            if i + 1 < n and lines[i + 1].strip():
                out.append("")
            continue

        out.append(ln)

    result = eol.join(out)
    if trailing:
        result += eol
    return result
