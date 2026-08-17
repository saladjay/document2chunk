# DOCX 接入统一后处理（阶段 A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 DOCX 变成第三路汇入统一 postprocess 的引擎路径——`DocxExtractor.extract()` 内调用 `postprocess()`，获得栈式标题定级、doc_title 检测、附件拆分、同表头表格合并。

**Architecture:** 镜像 PDF/OCR 的既有模式（两者都在 extractor 内完成后处理）。parser 层给 HeadingNode 打 `heading_source` 元数据标记、给段落打 `centered` 标记、做伪标题预扫描；postprocess 层为 DOCX 加字号比 doc_title 检测与样式层级权威分支；extractor 层计算正文基准字号并接线。

**Tech Stack:** Python ≥3.10，Pydantic v2（IR），lxml（docx 解析），pytest。无新依赖。

**Spec:** `docs/superpowers/specs/2026-08-17-docx-postprocess-design.md`（已批准）

## Global Constraints

- 分支 `feat/docx-postprocess`，在独立 worktree 开发（执行时用 superpowers:using-git-worktrees 建立）。
- IR 加性扩展：不改 `ir/models.py` 已有节点定义，新信息走 `_BlockBase.metadata` dict。
- `postprocess.py` 是 PDF/OCR/DOCX 三路共享代码：任何改动后跑**全量** pytest，PDF/OCR 测试不绿不提交。
- `HeadingNode.level` 必填 int（1–9）：伪标题占位用 2，禁止 None。
- 测试命令统一在 worktree 根目录：`uv run --all-extras pytest <path> -v`（worktree 首次执行前先 `uv sync --all-extras` 建 .venv；**禁止**复用主仓 .venv——editable install 指向主仓 src，会测错代码）。
- 提交信息用仓库惯例（`feat(postprocess): …` / `test(docx): …`），结尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 每个 Task 结束必须 commit。

---

### Task 1: 锁定无 provenance 路径的 None 安全（特征测试）

**Files:**
- Test: `tests/test_postprocess.py`（追加，不改既有内容）

**Interfaces:**
- Consumes: `filter_noise` / `merge_cross_page` / `split_attachments`（现有签名）
- Produces: 无（纯回归锁）。后续任务依赖「无 provenance 输入 → no-op」这一行为不被破坏。

**说明:** 这是 characterization test——预期**直接 PASS**（代码已具备该行为），目的是锁行为防回归，不遵循"先红后绿"。

- [ ] **Step 1: 追加测试**（放在文件末尾、`if __name__` 块之前；构造无 provenance 的块，直接构造 IR 节点）

```python
# ══════════════════════════════════════
#  无 provenance（DOCX 路）None 安全
# ══════════════════════════════════════

def _np():
    """无 provenance 的块构造（DOCX 形态）。"""
    from document2chunk.ir import RunNode, RunProperties
    r = RunNode(id="r1", text="x", style=RunProperties(font_size=16.0))
    return r


def test_filter_noise_no_provenance_noop():
    """DOCX 块（provenance=None）经 filter_noise 不删不改。"""
    blocks = [
        H("某标题", level=1),
        P("正文内容比较长的一段时间"),
        P("321"),
    ]
    for b in blocks:
        b.provenance = None
    out = filter_noise(blocks, layout_data=None, page_geometry=None)
    assert [b.id for b in out] == [b.id for b in blocks]


def test_merge_cross_page_no_provenance_noop():
    """无页码的块不做跨页续接（DOCX 天然单流），多行标题合并仍可工作。"""
    blocks = [
        H("某标题前半", level=1),
        H("某标题后半", level=1),
        P("正文内容比较长的一段时间"),
    ]
    for b in blocks:
        b.provenance = None
    out = merge_cross_page(blocks)
    # 段落未被拼接
    texts = [getattr(b, "text", "") for b in out]
    assert "正文内容比较长的一段时间" in texts
    # 无编号多行标题仍合并
    merged = [t for t in texts if t == "某标题前半某标题后半"]
    assert merged, texts


def test_split_attachments_no_geometry():
    """page_geometry=None 时按文本正则正常拆分。"""
    blocks = [
        H("主标题", level=1),
        P("正文段落内容"),
        H("附件1：某某表格", level=1),
        P("附件内容"),
    ]
    main, segs = split_attachments(blocks, page_geometry=None)
    assert [b.text for b in main] == ["主标题", "正文段落内容"]
    assert len(segs) == 1
    assert [b.text for b in segs[0]] == ["附件1：某某表格", "附件内容"]
```

注意：`H`/`P` 是本文件既有的构造辅助（带 OCR provenance），测试里显式置 `provenance = None` 模拟 DOCX。`_np()` 若未被用到就删掉，不要留死代码。

- [ ] **Step 2: 运行（预期直接 PASS）**

Run: `uv run --all-extras pytest tests/test_postprocess.py -v -k "no_provenance or no_geometry"`
Expected: 3 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_postprocess.py
git commit -m "test(postprocess): 锁定无 provenance 路径 None 安全（DOCX 前置）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: parser 打标记（heading_source / centered）+ 伪标题预扫描

**Files:**
- Modify: `src/document2chunk/extractors/docx/parser.py`
- Test: `tests/test_docx.py`（追加）

**Interfaces:**
- Consumes: `document2chunk.postprocess.style_of(text) -> Optional[str]`（编号样式名）
- Produces:
  - `HeadingNode.metadata["heading_source"] == "style" | "heuristic"`
  - `HeadingNode/ParagraphNode.metadata["centered"] == True`（仅当 `w:jc w:val="center"`）
  - 伪标题：无样式短编号段落 → `HeadingNode(level=2, metadata={"heading_source": "heuristic"})`
  - 新私有方法：`DocumentParser._heading_source(p, pstyle_id, text) -> Tuple[Optional[int], Optional[str]]`（返回 (level, source)）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_docx.py`；沿用文件头既有的 `make_docx`/`STYLES` 构造器）

```python
DOC_MARKED = f"""<w:document xmlns:w="{W}">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>总体要求</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、指导思想</w:t></w:r></w:p>
    <w:p><w:r><w:t>二、基本原则。这里是正文不是标题因为句号结尾。</w:t></w:r></w:p>
    <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>某某文件标题</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def test_heading_source_markers():
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    h = result.content[0]
    assert isinstance(h, HeadingNode)
    assert h.metadata.get("heading_source") == "style"


def test_pseudo_heading_promotion():
    """无样式短编号段落 → heading_source=heuristic 的 HeadingNode；
    句号结尾的长段不提升。"""
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    assert isinstance(result.content[1], HeadingNode)
    assert result.content[1].metadata.get("heading_source") == "heuristic"
    assert result.content[1].text == "一、指导思想"
    # 句号结尾 → 仍是段落
    assert isinstance(result.content[2], ParagraphNode)


def test_centered_marker():
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    p = result.content[3]
    assert isinstance(p, ParagraphNode)
    assert p.metadata.get("centered") is True
    # 大字号 run 保留（22pt，sz val=44）
    assert p.runs[0].style.font_size == 22.0
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --all-extras pytest tests/test_docx.py -v -k "source_markers or pseudo_heading or centered_marker"`
Expected: FAIL（`metadata.get("heading_source")` 返回 None / content[1] 是 ParagraphNode）

- [ ] **Step 3: 实现**

`src/document2chunk/extractors/docx/parser.py` 改四处：

3a. 文件头部（`_HEADING_HEURISTIC` 定义之后）加：

```python
from document2chunk.postprocess import style_of

# 伪标题：句尾出现这些字符 = 正文，不提升
_SENTENCE_END = "。！？!?；;：:"
# 伪标题文本长度上限（对齐 mineru2doc 降误检阈值）
_PSEUDO_HEADING_MAX_LEN = 40
```

（`from document2chunk.postprocess import style_of` 放到文件顶部既有 import 区，与其它 `from document2chunk.…` 并列。）

3b. 把 `_detect_heading` 拆成带来源版本 + 兼容壳（替换整个 `_detect_heading` 方法）：

```python
    def _detect_heading(self, p, pstyle_id, text) -> Optional[int]:
        level, _ = self._heading_level_source(p, pstyle_id, text)
        return level

    def _heading_level_source(self, p, pstyle_id, text) -> Tuple[Optional[int], Optional[str]]:
        """(level, source)：source = style（outlineLvl/pStyle）| heuristic（正则）| None。"""
        ppr = p.find(w("pPr"))
        if ppr is not None:
            ol = ppr.find(w("outlineLvl"))
            if ol is not None:
                val = wa(ol, "val")
                if val and val.isdigit():
                    n = int(val)
                    if 0 <= n <= 8:
                        return n + 1, "style"
            lvl = self._styles.heading_level(pstyle_id)
            if lvl:
                return lvl, "style"
        if self._heuristic and text:
            for rx, lvl in _HEADING_HEURISTIC:
                if rx.match(text.strip()):
                    return lvl, "heuristic"
        return None, None
```

3c. 新增两个私有方法（放在 `_list_info` 之后）：

```python
    @staticmethod
    def _is_centered(p) -> bool:
        ppr = p.find(w("pPr"))
        if ppr is None:
            return False
        jc = ppr.find(w("jc"))
        return jc is not None and wa(jc, "val") in ("center", "centre")

    def _is_pseudo_heading(self, text: str) -> bool:
        """无样式短编号段落 → 伪标题候选（交给 calibrate 栈定级）。"""
        t = (text or "").strip()
        if not t or len(t) > _PSEUDO_HEADING_MAX_LEN:
            return False
        if t[-1] in _SENTENCE_END:
            return False
        return style_of(t) is not None
```

3d. `parse()` 主循环的 else 分支（原 151-154 行）替换为：

```python
            else:
                flush_list()
                if text or runs:
                    md: dict = {"centered": True} if self._is_centered(child) else {}
                    if self._is_pseudo_heading(text):
                        blocks.append(HeadingNode(
                            id=self._bid(), level=2, text=text, runs=runs,
                            metadata={**md, "heading_source": "heuristic"},
                        ))
                    else:
                        blocks.append(ParagraphNode(
                            id=self._bid(), runs=runs, text=text, metadata=md,
                        ))
```

heading 分支（原 139-143 行）替换为（把 `level` 的取得换成带 source 版本——注意 `_classify` 已算过 `level`，这里只补 source。最小改法：在 `kind, level, runs, text, list_info, images = self._classify(child)` 之后加一行）：

```python
            _, hsrc = self._heading_level_source(child, None, text)
```

且 heading 分支改为：

```python
            if kind == "heading":
                flush_list()
                hmd = {"heading_source": hsrc or "heuristic"}
                if self._is_centered(child):
                    hmd["centered"] = True
                blocks.append(HeadingNode(
                    id=self._bid(), level=level, text=text, runs=runs, metadata=hmd,
                ))
```

（`hsrc` 传 `pstyle_id=None` 会漏 pStyle 判定——所以 `_classify` 返回的 `pstyle_id` 需要带出来。更正：把 `_classify` 的调用处改成同时取 `pstyle_id`：在 `_classify` 里把 `pstyle_id` 加进返回元组会改两处调用。**采用最小侵入方案**：`parse()` 里在 `_classify` 调用后自行重取：

```python
            kind, level, runs, text, list_info, images = self._classify(child)
            ppr0 = child.find(w("pPr"))
            pstyle0 = None
            if ppr0 is not None:
                ps0 = ppr0.find(w("pStyle"))
                if ps0 is not None:
                    pstyle0 = wa(ps0, "val")
            _, hsrc = self._heading_level_source(child, pstyle0, text)
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --all-extras pytest tests/test_docx.py -v`
Expected: 既有 7 个 + 新增 3 个全 PASS（既有测试断言 `len(result.content) == 5` 等——伪标题预扫描对 `_doc()` 样例无影响：`粗体14pt` 不匹配编号正则）

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/docx/parser.py tests/test_docx.py
git commit -m "feat(docx): heading_source/centered 标记 + 无样式短编号段落伪标题预扫描
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: postprocess 加 DOCX 分支（body_font_size + 样式层级权威 + 栈首见精化）

**Files:**
- Modify: `src/document2chunk/postprocess.py`
- Test: `tests/test_postprocess.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `metadata["heading_source"]` / `metadata["centered"]` 标记；`RunNode.style.font_size`（pt，float）
- Produces:
  - `postprocess(blocks, metadata, *, toc_entries=None, page_geometry=None, page_widths=None, layout_data=None, use_height_fallback=True, body_font_size=None, _log=None)`（新 kwarg `body_font_size: Optional[float]`）
  - `calibrate_levels(content, metadata, *, page_widths=None, toc_entries=None, use_height_fallback=True, body_font_size=None, _log=None)`（同上）
  - 模块级新函数 `_max_font_size(node) -> Optional[float]`、`_promote_doc_title_paragraphs_docx(content, body_font_size) -> List[BlockNode]`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_postprocess.py`）

```python
# ══════════════════════════════════════
#  DOCX 分支：字号比 doc_title + 样式层级权威 + 栈首见精化
# ══════════════════════════════════════

from document2chunk.ir import RunNode, RunProperties


def _drun(text, size):
    return RunNode(id=f"r{text[:3]}{size}", text=text,
                   style=RunProperties(font_size=size))


def _dp(text, size=16.0, centered=False, **kw):
    """DOCX 形态段落：无 provenance，带字号 runs。"""
    md = {"centered": True} if centered else {}
    md.update(kw)
    return ParagraphNode(id=f"dp{text[:4]}{size}", text=text,
                         runs=[_drun(text, size)], metadata=md)


def _dh(text, level, size=16.0, source=None, centered=False):
    """DOCX 形态标题：无 provenance，带字号 runs 与 heading_source。"""
    md = {}
    if source:
        md["heading_source"] = source
    if centered:
        md["centered"] = True
    return HeadingNode(id=f"dh{text[:4]}{level}", level=level, text=text,
                       runs=[_drun(text, size)], metadata=md)


def _mdocx():
    return DocumentMetadata(source_type=SourceType.DOCX)


def test_docx_doc_title_by_font_ratio():
    """首个居中大字号段落（22pt vs 正文 16pt）→ H1 + metadata.title。"""
    content = [
        _dp("某某关于改革完善占补平衡管理的通知", size=22.0, centered=True),
        _dp("正文第一段内容", size=16.0),
        _dp("正文第二段内容", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [(1, "某某关于改革完善占补平衡管理的通知")], heads
    assert md.title == "某某关于改革完善占补平衡管理的通知"


def test_docx_style_level_authoritative():
    """heading_source=style 的层级保留，不被编号栈改写。"""
    content = [
        _dh("二级样式标题", level=2, size=16.0, source="style"),
        _dh("一、无样式编号段", level=2, size=16.0, source="heuristic"),
        _dp("正文内容", size=16.0),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    # 样式 H2 保留；伪标题首见 cn_major 从 prev_level+1 起 → H3（issues5 场景）
    assert heads == [(2, "二级样式标题"), (3, "一、无样式编号段")], heads


def test_docx_stack_first_seen_from_prev():
    """首见编号样式的分配 = max(next_style_level, prev_level+1)。"""
    content = [
        _dh("无编号主标题若干字以上才像标题", level=1, size=16.0),  # 无 source → 走栈/回退路径
        _dh("一、第一部分", level=2, size=16.0, source="heuristic"),
        _dh("二、第二部分", level=2, size=16.0, source="heuristic"),
        _dh("（一）子项甲", level=2, size=16.0, source="heuristic"),
    ]
    md = _mdocx()
    out = calibrate_levels(content, md, use_height_fallback=False, body_font_size=16.0)
    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    assert heads == [
        (1, "无编号主标题若干字以上才像标题"),
        (2, "一、第一部分"),
        (2, "二、第二部分"),
        (3, "（一）子项甲"),
    ], heads


def test_postprocess_docx_entry():
    """postprocess 入口透传 body_font_size，全链路对 DOCX 形态输入不崩。"""
    content = [
        _dp("某某文件的通知标题很长超过八个字符", size=22.0, centered=True),
        _dh("一、总体要求", level=2, size=16.0, source="heuristic"),
        _dp("正文内容一段。", size=16.0),
        _dh("附件1：附表", level=2, size=16.0, source="heuristic"),
        _dp("附件里的正文。", size=16.0),
    ]
    md = _mdocx()
    main, segs = postprocess(content, md, use_height_fallback=False, body_font_size=16.0)
    assert md.title == "某某文件的通知标题很长超过八个字符"
    assert any(isinstance(b, HeadingNode) and b.level == 2 and b.text == "一、总体要求" for b in main)
    assert len(segs) == 1
    assert segs[0][0].text == "附件1：附表"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --all-extras pytest tests/test_postprocess.py -v -k "docx"`
Expected: FAIL — `calibrate_levels() got an unexpected keyword argument 'body_font_size'`（TypeError）

- [ ] **Step 3: 实现**

`src/document2chunk/postprocess.py` 改六处：

3a. import 行（第 27 行）加 `SourceType`：

```python
from document2chunk.ir import (
    BlockNode, DocumentMetadata, HeadingNode, ParagraphNode, SourceType, TableNode, TocEntry,
)
```

3b. `_prov_page` 之后加辅助函数：

```python
def _max_font_size(node) -> Optional[float]:
    """块内 runs 的最大字号（pt）。DOCX 路专用（runs 自带真实磅值）。"""
    sizes = [
        r.style.font_size
        for r in (getattr(node, "runs", None) or [])
        if getattr(getattr(r, "style", None), "font_size", None)
    ]
    return max(sizes) if sizes else None
```

3c. `_detect_doc_title_indices`（第 478 行）签名与检测条件加 DOCX 分支——签名追加 kwarg，循环内判定改为：

```python
def _detect_doc_title_indices(
    content: List[BlockNode],
    body_h: float,
    page_widths: Optional[Dict[int, float]],
    use_height_fallback: bool,
    *,
    body_font_size: Optional[float] = None,
    is_docx: bool = False,
) -> List[int]:
```

循环内（替换原 501-507 行的 h/ratio/centered/条件四行）：

```python
        if is_docx and body_font_size:
            fs = _max_font_size(b)
            ratio = (fs / body_font_size) if fs else 0.0
            centered = bool((b.metadata or {}).get("centered"))
            ok = (centered and ratio >= 1.0) or ratio >= DOC_TITLE_EDITED_RATIO
        else:
            h = _bbox_h(b)
            ratio = (h / body_h) if body_h else 0.0
            centered = _is_centered(b, page_widths)
            ok = (use_height_fallback and ratio >= DOC_TITLE_RATIO) or (
                not use_height_fallback and centered and ratio >= DOC_TITLE_EDITED_RATIO
            )
        if ok:
            indices.append(i)
```

（函数 docstring 的「高度检测」段补一句：DOCX 按字号比 + metadata["centered"]。）

3d. `_promote_doc_title_paragraphs` 之后加 DOCX 变体：

```python
def _promote_doc_title_paragraphs_docx(
    content: List[BlockNode],
    body_font_size: Optional[float],
) -> List[BlockNode]:
    """DOCX doc_title 段落提升：居中且字号≥基准，或字号≥基准×1.2。

    DOCX 无页几何，但 run 自带真实磅值；公文标题（二号≈22pt）vs 正文
    （三号≈16pt）比≈1.33。提升为 level=2 占位，主循环里胜者再定 H1。
    """
    if not body_font_size or body_font_size <= 0:
        return content
    out: List[BlockNode] = []
    for b in content:
        if isinstance(b, ParagraphNode):
            txt = (b.text or "").strip()
            fs = _max_font_size(b)
            if txt and fs and not style_of(txt) and not RE_APPENDIX.match(txt):
                ratio = fs / body_font_size
                centered = bool((b.metadata or {}).get("centered"))
                if (centered and ratio >= 1.0) or ratio >= DOC_TITLE_EDITED_RATIO:
                    out.append(HeadingNode(
                        id=b.id, level=2, text=txt, runs=b.runs,
                        provenance=b.provenance, metadata=dict(b.metadata or {}),
                    ))
                    continue
        out.append(b)
    return out
```

3e. `calibrate_levels`（第 594 行）——签名加 `body_font_size: Optional[float] = None`；0b/0c 段（第 621-643 行）改为：

```python
    # 0b. doc_title 检测（已有 HeadingNode）
    is_docx = metadata.source_type == SourceType.DOCX
    toc_map = _build_toc_mapping(toc_entries)
    doc_title_indices = _detect_doc_title_indices(
        content, body_h, page_widths, use_height_fallback,
        body_font_size=body_font_size, is_docx=is_docx,
    )
    for i in doc_title_indices:
        b = content[i]
        _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                 detected="doc_title", action="→候选", reason="heading 检测")

    # 0c. 段落提升兜底：OCR 按高度比（R2）；DOCX 按字号比。仅当无 heading 级
    # 候选时触发，避免竞争性 doc_title 误提升。
    if not doc_title_indices and use_height_fallback:
        content = _promote_doc_title_paragraphs(
            content, body_h, page_widths=page_widths, use_height_fallback=True
        )
        doc_title_indices = _detect_doc_title_indices(
            content, body_h, page_widths, use_height_fallback,
            body_font_size=body_font_size, is_docx=is_docx,
        )
        for i in doc_title_indices:
            b = content[i]
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected="doc_title(promoted)", action="→候选", reason="R2 段落提升")
    if not doc_title_indices and is_docx:
        content = _promote_doc_title_paragraphs_docx(content, body_font_size)
        doc_title_indices = _detect_doc_title_indices(
            content, body_h, page_widths, use_height_fallback,
            body_font_size=body_font_size, is_docx=True,
        )
        for i in doc_title_indices:
            b = content[i]
            _log_add(section="calibrate", block_id=b.id, text=(b.text or "")[:40],
                     detected="doc_title(promoted)", action="→候选", reason="DOCX 字号比提升")
```

3f. 主循环（第 705-717 行）——toc 覆盖之后插入样式权威分支，并把首见栈分配改为从 `prev_level+1` 起：

```python
        # toc 覆盖（精确/前缀，优先于栈序）
        toc_lvl = _match_toc_level(txt, toc_map)
        if toc_lvl is not None:
            lvl = toc_lvl
            _log_add(section="calibrate", block_id=b.id, text=txt[:40],
                     detected="toc", action=f"→H{lvl}", reason="toc 映射覆盖")
        elif (b.metadata or {}).get("heading_source") == "style":
            # DOCX 样式层级权威（outlineLvl/pStyle 是作者意图）：保留原级，
            # 仅随 doc_title 存在加 offset。优先于编号栈。
            lvl = (b.level + level_offset) if has_doc_title else b.level
            _log_add(section="calibrate", block_id=b.id, text=txt[:40],
                     detected="style", action=f"→H{lvl}", reason="样式层级权威")
        elif st:
            if st not in style_levels:
                # 首见样式从「栈顶 +1」起分配（DOCX 伪标题在样式 H2 之后
                # 应落 H3，issues5「一、应为三级」）；不回退低于已分配层
                style_levels[st] = max(next_style_level, prev_level + 1)
                next_style_level = style_levels[st] + 1
            lvl = style_levels[st]
            _log_add(section="calibrate", block_id=b.id, text=txt[:40],
                     detected=st, action=f"→H{lvl}", reason=f"栈序(offset={level_offset})")
        else:
            （else 分支原样不动）
```

3g. `postprocess`（第 794 行）——签名加 `body_font_size: Optional[float] = None`，`calibrate_levels` 调用处透传：

```python
    blocks = calibrate_levels(
        blocks, metadata,
        page_widths=page_widths, toc_entries=toc_entries,
        use_height_fallback=use_height_fallback, body_font_size=body_font_size,
        _log=_log,
    )
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run --all-extras pytest tests/test_postprocess.py -v`
Expected: 全 PASS（含既有测试——首见栈分配从 `prev_level+1` 起对 OCR/PDF 无影响：它们无 `heading_source` 标记，样式 heading 走 else 分支不更新 `prev_level` 之外的栈状态……**注意**：既有 OCR 测试中样式 heading（如无编号 markdown `#`）会推进 `prev_level`，首见编号样式原从 `next_style_level` 起分配；若既有测试断言变化，逐条核对语义：`max(next_style_level, prev_level + 1)` 仅在 `prev_level ≥ next_style_level` 时才抬高分配——这正是「新样式嵌套在当前上下文之内」的语义，属修正而非回归。）

- [ ] **Step 5: 跑全量回归（共享代码红线）**

Run: `uv run --all-extras pytest -q`
Expected: 全 PASS。若有 PDF/OCR 用例因首见分配变化而红，按上句语义核对；确属行为改善则更新该断言并在 commit message 里说明。

- [ ] **Step 6: Commit**

```bash
git add src/document2chunk/postprocess.py tests/test_postprocess.py
git commit -m "feat(postprocess): DOCX 分支——字号比 doc_title + 样式层级权威 + 栈首见从 prev+1
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: extractor 接线（postprocess 调用 + attachments 组装）

**Files:**
- Modify: `src/document2chunk/extractors/docx/extractor.py`
- Test: `tests/test_docx.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `postprocess(..., body_font_size=...)`；Task 2 的标记契约
- Produces: `DocxExtractor.extract()` 返回的 `ExtractionResult.attachments` 非空（拆分出的附件段，`custom={"is_attachment": True}`）；模块级 `_body_font_size(blocks) -> Optional[float]`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_docx.py`）

```python
DOC_FULL = f"""<w:document xmlns:w="{W}">
  <w:body>
    <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>关于加强耕地保护提升耕地质量的通知</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、总体要求</w:t></w:r></w:p>
    <w:p><w:r><w:t>坚持最严格的耕地保护制度。</w:t></w:r></w:p>
    <w:p><w:r><w:t>二、重点工作</w:t></w:r></w:p>
    <w:p><w:r><w:t>附件1：占补平衡指标表</w:t></w:r></w:p>
    <w:p><w:r><w:t>附件说明正文。</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def test_extractor_postprocess_wired():
    """extract() 走 postprocess：doc_title 提升 + 伪标题定级 + 附件拆分。"""
    result = DocxExtractor().extract(make_docx(DOC_FULL, STYLES))
    # doc_title：居中 22pt（正文 11pt，比≈2.0 ≥1.2）→ metadata.title
    assert result.metadata.title == "关于加强耕地保护提升耕地质量的通知"
    # 附件拆分
    assert len(result.attachments) == 1
    assert result.attachments[0].metadata.custom.get("is_attachment") is True
    # 主文标题层级：doc_title H1；一、/二、 伪标题 → H2
    levels = [(b.level, b.text) for b in result.content if isinstance(b, HeadingNode)]
    assert (2, "一、总体要求") in levels
    assert (2, "二、重点工作") in levels


def test_extractor_regression_basic():
    """既有 _doc() 样例接线后结构不变（5 块、层级不变）。"""
    result = DocxExtractor().extract(_doc())
    assert len(result.content) == 5
    assert result.content[0].level == 1
    assert result.content[2].level == 1
    assert result.attachments == []


def test_extractor_split_tables_merged():
    """连续同表头表格合并（DOCX 手工拆分表场景）。"""
    tbl = """<w:tbl><w:tr><w:tc><w:p><w:r><w:t>序号</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>名称</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""
    document = f'<w:document xmlns:w="{W}"><w:body>{tbl}{tbl}<w:p><w:r><w:t>正文。</w:t></w:r></w:p></w:body></w:document>'
    result = DocxExtractor().extract(make_docx(document, STYLES))
    tables = [b for b in result.content if isinstance(b, TableNode)]
    assert len(tables) == 1
    assert len(tables[0].rows) == 2  # 首表表头 + 1 数据行（第二表跳过表头追加）
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run --all-extras pytest tests/test_docx.py -v -k "postprocess_wired or regression_basic or split_tables_merged"`
Expected: `test_extractor_postprocess_wired` FAIL（title 为 None、attachments 为空）；`test_extractor_regression_basic` 可能已 PASS（回归锁）；`split_tables_merged` FAIL（两张表未合并）

- [ ] **Step 3: 实现**

`src/document2chunk/extractors/docx/extractor.py` 全量替换为：

```python
"""DocxExtractor —— .docx → ExtractionResult（lxml 直读 + 统一 postprocess）。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import List, Optional

from document2chunk.extractors.docx.package_reader import PackageReader
from document2chunk.extractors.docx.parser import DocumentParser
from document2chunk.extractors.docx.styles import StyleRegistry
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    ListNode,
    ParagraphNode,
    SourceType,
    TocEntry,
)


class InvalidDocxError(Exception):
    """无效的 .docx 文件。"""


def _body_font_size(blocks: List[BlockNode]) -> Optional[float]:
    """正文基准字号：全部段落 run 字号（pt）的众数（仿 BodyAnalysisStage）。

    遍历顶层段落 + 列表项内段落；表格内文字不参与（表内字号常异于正文）。
    """
    sizes: List[float] = []

    def walk(bs: List[BlockNode]) -> None:
        for b in bs:
            if isinstance(b, ParagraphNode):
                for r in b.runs or []:
                    fs = getattr(getattr(r, "style", None), "font_size", None)
                    if fs:
                        sizes.append(round(float(fs), 1))
            elif isinstance(b, ListNode):
                for item in b.items:
                    walk(item.blocks)

    walk(blocks)
    return Counter(sizes).most_common(1)[0][0] if sizes else None


class DocxExtractor:
    """可编辑 .docx 提取器。"""

    source_type: SourceType = SourceType.DOCX

    def extract(
        self,
        source,
        *,
        options=None,
        heuristic_headings: bool = False,
    ) -> ExtractionResult:
        reader = PackageReader(source)

        doc_elem = reader.document_element()
        if doc_elem is None:
            raise InvalidDocxError("缺少 word/document.xml，不是有效的 .docx")

        registry = StyleRegistry()
        registry.load(reader.styles_element())

        parser = DocumentParser(
            registry,
            numbering_elem=reader.numbering_element(),
            reader=reader,
            heuristic_headings=heuristic_headings,
        )
        blocks, toc_entries = parser.parse(doc_elem)

        core = reader.core_properties()
        source_file = Path(source).name if isinstance(source, (str, Path)) else None

        def _meta(custom: Optional[dict] = None) -> DocumentMetadata:
            return DocumentMetadata(
                source_type=SourceType.DOCX,
                source_file=source_file,
                title=core.get("title"),
                author=core.get("author"),
                created=core.get("created"),
                modified=core.get("modified"),
                custom=custom,
            )

        metadata = _meta()

        # 统一后处理（第三路汇合，designs/009）：DOCX 无页几何，页相关步骤天然 no-op；
        # 收益是 calibrate_levels（doc_title 字号比 + 栈式定级）与 split_attachments。
        from document2chunk.postprocess import postprocess
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=toc_entries if toc_entries else None,
            page_geometry=None,
            use_height_fallback=False,
            body_font_size=_body_font_size(blocks),
        )

        result = ExtractionResult(
            content=main_content,
            metadata=metadata,
            toc_entries=toc_entries if toc_entries else None,
        )
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=_meta(
                custom={"is_attachment": True})))
        return result
```

（注意：`DocumentMetadata` 是否有 `custom` 字段——检查 `ir/models.py`；calibrate_levels 已在使用 `metadata.custom["doc_titles"]`（postprocess.py:659），说明字段存在且默认 dict。）

- [ ] **Step 4: 运行验证通过**

Run: `uv run --all-extras pytest tests/test_docx.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/docx/extractor.py tests/test_docx.py
git commit -m "feat(docx): extract() 接入统一 postprocess + attachments 组装
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 全量回归 + 文档同步

**Files:**
- Modify: `openspec/specs/docx-extractor/spec.md`（能力变更同步）
- Modify: `README.md`（postprocess 节「PDF + OCR 共用」→「PDF / OCR / DOCX 三路共用」）

**Interfaces:**
- Consumes: 前四个任务的全部产出
- Produces: 无代码产出；文档与代码一致

- [ ] **Step 1: 全量测试**

Run: `uv run --all-extras pytest -q`
Expected: 全 PASS（0 failed）

- [ ] **Step 2: 同步 openspec 规格**

读 `openspec/specs/docx-extractor/spec.md`，在其能力清单中加入（措辞随文件既有风格调整，内容以下为准）：

```markdown
### 统一后处理（2026-08-17 阶段 A）

`DocxExtractor.extract()` 内部调用 `document2chunk.postprocess.postprocess()`：

- `filter_noise` / `merge_cross_page`：DOCX 无页概念，天然 no-op
- `calibrate_levels`：doc_title 按字号比（居中 + ≥基准，或 ≥基准×1.2）；
  样式标题（outlineLvl/pStyle）层级权威；无样式短编号段落由 parser 预扫为
  伪标题（`heading_source="heuristic"`），栈式定级（首见样式从栈顶+1 分配）
- `split_attachments`：附件/附录边界拆分为 `attachments`
- `merge_split_tables`：连续同表头表格合并
```

- [ ] **Step 3: README 同步**

`README.md` 的 `postprocess` 模块行与「文档级后处理」节开头，把「（PDF + OCR 共用）」改为「（PDF / OCR / DOCX 三路共用）」；在「文档级后处理」节末尾追加一段：

```markdown
DOCX 路同样汇入统一后处理：无页概念使 `filter_noise` / `merge_cross_page` 天然
no-op；`calibrate_levels` 对 DOCX 使用字号比 doc_title 检测（run 自带磅值），
样式标题层级权威、无样式编号段落经伪标题预扫描后由栈式定级；`split_attachments`
与 `merge_split_tables` 照常生效。
```

- [ ] **Step 4: Commit**

```bash
git add openspec/specs/docx-extractor/spec.md README.md
git commit -m "docs: docx 统一后处理接入同步 openspec 规格与 README
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 验收（计划外步骤，合并前）

真实公文 docx 样本目录由用户提供（路径届时给出）——逐份 `document2chunk.parse()` 对比标题树 + 附件数，人工抽查。样本接入方式（临时脚本，不进库）：

```bash
uv run --all-extras python -c "
import document2chunk, pathlib
for p in pathlib.Path(r'<样本目录>').glob('*.docx'):
    doc = document2chunk.parse(p)
    print(p.name, '|', doc.metadata.title, '| 附件', len(doc.attachments))
    for s in doc.iter_sections():
        print('  ' * s.level + s.title[:40])
"
```

## Self-Review 记录

- **Spec 覆盖**：§2 架构（Task 4）、§3 表格四行组件改动（Task 2/3/4）、§4 五项决策（样式权威=Task 3f、字号比=Task 3c/3d、use_height_fallback=False=Task 4、parser 预扫描=Task 2、None 安全=Task 1）、§6.1 七项测试（1=Task 1、2/3=Task 3、4=Task 3、5=Task 4、6=Task 4、7=Task 3 Step 5 + Task 5 Step 1）、§6.2 验收（上方节）、§7 handoff（已随 spec 提交）、§8 worktree（Global Constraints）。无缺口。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`heading_source`（str）、`centered`（bool）、`body_font_size`（Optional[float]）、`_heading_level_source -> Tuple[Optional[int], Optional[str]]`、`_max_font_size -> Optional[float]` 各任务间一致；`postprocess`/`calibrate_levels` 新 kwarg 在 Task 3 定义、Task 4 消费。
