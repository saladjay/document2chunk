# DOCX OOXML 覆盖面补齐 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 DOCX 解析器对 OOXML 的覆盖（AlternateContent 去重/文本框/OMML 公式/OLE/尾注脚注/sdt/媒体落盘/页眉元数据），全部进统一 IR。

**Architecture:** 分层扩展——`embedded.py`（去重遍历 + 嵌入物解析）、`notes.py`（尾注脚注）、parser 只加分派点、`package_reader` 扩 part 读取、extractor 加 `image_dir`/`metadata.custom`/postprocess 后尾注追加。IR models.py 与 postprocess.py 零改动。已并入阶段 A（heading_source 契约、postprocess 接入）。

**Tech Stack:** Python ≥3.10 + lxml + pydantic v2（禁止 python-docx）。

**设计文档:** `docs/superpowers/specs/2026-08-17-docx-ooxml-coverage-design.md`（频率表与全部决策依据）

## Global Constraints

- 分支 `worktree-docx-ooxml-coverage`（worktree `.claude/worktrees/docx-ooxml-coverage` 已就绪，勿新建）；已合并阶段 A（main@2739868，merge d1a1847）
- `src/document2chunk/ir/models.py` 零改动；`postprocess.py` 零改动（三路共享）；PDF/OCR 代码零改动；`tests/test_docx.py` 与 `tests/test_postprocess.py` 保持不动（回归锁）
- **阶段 A 接口契约**：parser 主循环的 heading 产出带 `heading_source`/`centered` metadata 与伪标题预扫描（`_is_pseudo_heading`）——新增的 heading 产出路径（文本框展开）必须同样携带；extractor 调 `postprocess()` 不得删除
- **postprocess 交互**（设计 §7）：filter_noise/merge_cross_page 对 DOCX 全 no-op；calibrate_levels 只动 HeadingNode；尾注内容必须在 postprocess **之后**追加（否则 split_attachments 会把尾注划进末尾附件段）
- 新测试一律进 `tests/test_docx_ooxml.py`；命令一律 `uv run pytest ...`
- 错误处理风格：WARN + 跳过，不中断（spec §3.8）；日志 `logging.getLogger(__name__)`
- 提交信息中文 conventional commits，结尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`
- 基线：`uv run pytest -q` = **180 passed**（含阶段 A 的 15 个；环境已补 numpy，非 pyproject 声明）

---

### Task 1: `embedded.py` 基础——AlternateContent 去重遍历

**Files:**
- Modify: `src/document2chunk/extractors/docx/_ooxml.py`
- Create: `src/document2chunk/extractors/docx/embedded.py`
- Create: `tests/test_docx_ooxml.py`（含共享 fixture 辅助，后续任务都往这里加测试）

**Interfaces:**
- Consumes: 无（首个任务）
- Produces:
  - `_ooxml.py`: 常量 `M`/`MC`/`WPS`（math/markup-compat/wordprocessingShape 命名空间）
  - `embedded.content_children(el) -> Iterator[etree._Element]`：直接子元素遍历，`mc:AlternateContent` 替换为其 Choice（无 Choice 走 Fallback）的子元素
  - `embedded.iter_content(el) -> Iterator[etree._Element]`：深度遍历（同上去重，不 yield AlternateContent/Fallback 自身）
  - `embedded.inside_textbox(el) -> bool`：祖先链含 `txbxContent`
  - 测试辅助 `tests/test_docx_ooxml.py::make_docx(document_xml, *, styles_xml=, endnotes_xml=, footnotes_xml=, header_parts=, media=, rels_xml=) -> bytes` 与命名空间常量 `DOC_NS`

- [ ] **Step 1: 写失败测试（新建 `tests/test_docx_ooxml.py`）**

```python
"""OOXML 覆盖面测试（阶段 B）：文本框/公式/OLE/尾注脚注/sdt/页眉/媒体。

fixture 全部手搓仿 WPS 写法（真实公文样本不入库，见设计文档 §6.1）。
"""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from document2chunk.extractors.docx import DocxExtractor

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

DOC_NS = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:mc="{MC}" xmlns:v="{V}" xmlns:o="{O}" xmlns:m="{M}" xmlns:wps="{WPS}"'


def make_docx(
    document_xml,
    *,
    styles_xml=None,
    endnotes_xml=None,
    footnotes_xml=None,
    header_parts=None,
    media=None,
    rels_xml=None,
) -> bytes:
    """手搓 docx。header_parts/media: {part名: 内容}；rels_xml 为 document rels。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr("word/document.xml", f"{XMLDECL}\n{document_xml}")
        if styles_xml:
            z.writestr("word/styles.xml", f"{XMLDECL}\n{styles_xml}")
        if endnotes_xml:
            z.writestr("word/endnotes.xml", f"{XMLDECL}\n{endnotes_xml}")
        if footnotes_xml:
            z.writestr("word/footnotes.xml", f"{XMLDECL}\n{footnotes_xml}")
        if header_parts:
            for name, data in header_parts.items():
                z.writestr(name, f"{XMLDECL}\n{data}")
        if media:
            for name, data in media.items():
                z.writestr(name, data)
        if rels_xml:
            z.writestr("word/_rels/document.xml.rels", f"{XMLDECL}\n{rels_xml}")
    return buf.getvalue()


# ---------- Task 1: AlternateContent 去重 ----------


def _texts(el):
    """收集元素子树内所有 w:t 文本（顺序）。"""
    return [e.text or "" for e in el.iter() if etree.QName(e).localname == "t"]


def test_content_children_choice_wins():
    from document2chunk.extractors.docx.embedded import content_children

    xml = f"""<w:p {DOC_NS}>
      <w:r><w:t>前</w:t></w:r>
      <mc:AlternateContent>
        <mc:Choice Requires="wps"><w:r><w:t>CHOICE</w:t></w:r></mc:Choice>
        <mc:Fallback><w:r><w:t>FALLBACK</w:t></w:r></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    kids = [etree.QName(c).localname for c in content_children(p)]
    assert kids == ["r", "r"]  # AlternateContent 替换为 Choice 内的 r
    assert _texts(p)[0] == "前"


def test_iter_content_skips_fallback():
    from document2chunk.extractors.docx.embedded import iter_content

    xml = f"""<w:p {DOC_NS}>
      <mc:AlternateContent>
        <mc:Choice Requires="wps"><w:r><w:t>CHOICE</w:t></w:r></mc:Choice>
        <mc:Fallback><w:r><w:t>FB1</w:t></w:r><w:r><w:t>FB2</w:t></w:r></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    texts = [e.text for e in iter_content(p) if etree.QName(e).localname == "t"]
    assert texts == ["CHOICE"]


def test_iter_content_fallback_when_no_choice():
    from document2chunk.extractors.docx.embedded import iter_content

    xml = f"""<w:p {DOC_NS}>
      <mc:AlternateContent>
        <mc:Fallback><w:pict><w:r><w:t>ONLYFB</w:t></w:r></w:pict></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    texts = [e.text for e in iter_content(p) if etree.QName(e).localname == "t"]
    assert texts == ["ONLYFB"]


def test_inside_textbox():
    from document2chunk.extractors.docx.embedded import inside_textbox

    xml = f"""<w:p {DOC_NS}><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
      <w:p><w:r><w:t>x</w:t></w:r></w:p>
    </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>"""
    p = etree.fromstring(xml.encode())
    inner_p = p.find(f".//{{{W}}}p")
    assert inner_p is not None and p is not inner_p
    assert inside_textbox(inner_p) is True
    assert inside_textbox(p) is False
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v`
Expected: 4 FAILED（`ModuleNotFoundError: No module named 'document2chunk.extractors.docx.embedded'`）

- [ ] **Step 3: 实现 `_ooxml.py` 常量与 `embedded.py`**

`src/document2chunk/extractors/docx/_ooxml.py` 末尾追加：

```python
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
```

新建 `src/document2chunk/extractors/docx/embedded.py`：

```python
"""OOXML 嵌入物解析（阶段 B）：去重遍历 / 文本框 / OMML 公式 / OLE。

设计依据：docs/superpowers/specs/2026-08-17-docx-ooxml-coverage-design.md §3/§4。
"""

from __future__ import annotations

from typing import Iterator

from lxml import etree

_ALT_DEPTH_LIMIT = 10


def _choice_or_fallback(alt_el):
    """mc:AlternateContent → 首个 mc:Choice；无 Choice 走 mc:Fallback。"""
    target = None
    for sub in alt_el:
        if etree.QName(sub).localname == "Choice":
            target = sub
            break
    if target is None:
        for sub in alt_el:
            if etree.QName(sub).localname == "Fallback":
                target = sub
                break
    return target


def content_children(el, depth: int = 0) -> Iterator[etree._Element]:
    """直接子元素遍历；AlternateContent 替换为其 Choice/Fallback 的子元素。

    WPS 双写（12% 样本）：Choice(wps) 与 Fallback(VML) 各含一份内容，
    只走 Choice 防文字/图片双计。
    """
    if depth > _ALT_DEPTH_LIMIT:
        return
    for child in el:
        if etree.QName(child).localname == "AlternateContent":
            target = _choice_or_fallback(child)
            if target is not None:
                yield from content_children(target, depth + 1)
            continue
        yield child


def iter_content(el) -> Iterator[etree._Element]:
    """深度遍历子树（去重规则同 content_children）。"""
    for child in content_children(el):
        yield child
        yield from iter_content(child)


def inside_textbox(el) -> bool:
    """祖先链是否含 txbxContent（文本框内外判定）。"""
    a = el.getparent()
    while a is not None:
        if etree.QName(a).localname == "txbxContent":
            return True
        a = a.getparent()
    return False
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v`
Expected: 4 PASSED

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -q`
Expected: 184 passed（180 + 4）

- [ ] **Step 6: Commit**

```bash
git add src/document2chunk/extractors/docx/_ooxml.py src/document2chunk/extractors/docx/embedded.py tests/test_docx_ooxml.py
git commit -m "feat(docx): AlternateContent 去重遍历基座（embedded.py）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `package_reader` 扩展——notes/header part 与媒体信息

**Files:**
- Modify: `src/document2chunk/extractors/docx/package_reader.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `PackageReader.endnotes_element() -> Optional[etree._Element]`
  - `PackageReader.footnotes_element() -> Optional[etree._Element]`
  - `PackageReader.header_elements() -> List[etree._Element]`
  - `PackageReader.media_info_for_rel(rel_id) -> Optional[Tuple[str, bytes, str]]`（媒体 zip 内原名、bytes、ext）；既有 `media_for_rel` 改为其薄封装（返回值不变）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_docx_ooxml.py`）**

```python
# ---------- Task 2: package_reader 扩展 ----------

_RELS_PNG = f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7" Type="{R}/image" Target="media/image1.png"/>
</Relationships>"""


def _reader(data: bytes):
    from document2chunk.extractors.docx.package_reader import PackageReader

    return PackageReader(data)


def test_reader_notes_and_header_parts():
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>'
    data = make_docx(
        doc,
        endnotes_xml=f'<w:endnotes {DOC_NS}><w:endnote w:id="1"><w:p><w:r><w:t>e1</w:t></w:r></w:p></w:endnote></w:endnotes>',
        header_parts={"word/header1.xml": f'<w:hdr {DOC_NS}><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>'},
    )
    r = _reader(data)
    assert r.endnotes_element() is not None
    assert r.footnotes_element() is None
    hs = r.header_elements()
    assert len(hs) == 1


def test_reader_media_info_for_rel():
    doc = f'<w:document {DOC_NS}><w:body><w:p/></w:body></w:document>'
    data = make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG)
    r = _reader(data)
    assert r.media_info_for_rel("rId7") == ("image1.png", b"PNGDATA", "png")
    assert r.media_info_for_rel("rId999") is None
    # 既有接口行为不变
    assert r.media_for_rel("rId7") == (b"PNGDATA", "png")
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k reader`
Expected: 2 FAILED（`AttributeError: ... has no attribute 'endnotes_element'`）

- [ ] **Step 3: 实现（`package_reader.py`）**

在 `numbering_element()` 方法后追加：

```python
    def endnotes_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/endnotes.xml")

    def footnotes_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/footnotes.xml")

    def header_elements(self) -> list:
        """word/header*.xml 列表（页眉文本进 metadata，不进正文）。"""
        out = []
        for name in self._zip.namelist():
            if name.startswith("word/header"):
                el = self.read_xml(name)
                if el is not None:
                    out.append(el)
        return out
```

把 `media_for_rel` 整体替换为：

```python
    def media_for_rel(self, rel_id: str) -> Optional[Tuple[bytes, str]]:
        """r:embed → (image_bytes, ext)。"""
        info = self.media_info_for_rel(rel_id)
        if info is None:
            return None
        return info[1], info[2]

    def media_info_for_rel(self, rel_id: str) -> Optional[Tuple[str, bytes, str]]:
        """r:id/r:embed → (媒体 zip 内原名, bytes, ext)。"""
        rels = self.read_xml("word/_rels/document.xml.rels")
        if rels is None:
            return None
        # Relationship 节点在 relationships 命名空间，属性无前缀
        for rel in rels:
            if rel.get("Id") == rel_id:
                target = rel.get("Target") or ""
                # Target 形如 "media/image1.png"（相对 word/）
                data = self.read_bytes("word/" + target)
                if data is None:
                    return None
                name = target.rsplit("/", 1)[-1]
                ext = target.rsplit(".", 1)[-1].lower() if "." in target else ""
                return name, data, ext
        return None
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k reader`
Expected: 2 PASSED

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 187 passed

```bash
git add src/document2chunk/extractors/docx/package_reader.py tests/test_docx_ooxml.py
git commit -m "feat(docx): PackageReader 读 endnotes/footnotes/header part 与媒体信息

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: OMML 公式——极简映射 + 纯文本兜底

**Files:**
- Modify: `src/document2chunk/extractors/docx/embedded.py`
- Modify: `src/document2chunk/extractors/docx/parser.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 1 的 `content_children`
- Produces:
  - `embedded.omml_to_latex(el) -> str`（m:f→`\frac{}{}`、m:sSup/m:sSub/m:sSubSup、m:rad→`\sqrt`、m:d→`(…)`、m:t 文本、其余递归拼接）
  - `embedded.omml_text(el) -> str`（纯文本兜底：所有 m:t 拼接）
  - parser：`_classify` 新 kind `"formula"`（段落主体为 oMathPara 且无 run/hyperlink → 返回 `("formula", None, [], latex, None, [])`）；`_parse_runs` 处理 p 直接子级 `oMath`/`oMathPara` → `InlineFormulaNode`；`_parse_run` 处理 r 内 `oMath`（latex 进 run 文本）；`parse()`/`_parse_cell_blocks` 加 `kind == "formula"` 分支产出块级 `FormulaNode`

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 3: OMML 公式 ----------


def test_omml_inline_formula_in_paragraph():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>比值为</w:t></w:r><m:oMath>
        <m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>
      </m:oMath></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    from document2chunk.ir import InlineFormulaNode

    para = result.content[0]
    assert isinstance(para, ParagraphNode)
    formulas = [r for r in para.runs if isinstance(r, InlineFormulaNode)]
    assert len(formulas) == 1 and formulas[0].latex == "\\frac{a}{b}"
    assert "\\frac{a}{b}" in para.text  # latex 进段落 text（markdown 走 text）


def test_omml_inline_formula_inside_run():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>x</w:t><m:oMath>
        <m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>
      </m:oMath></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    from document2chunk.ir import InlineFormulaNode

    para = result.content[0]
    assert "x^{2}" in para.text
    assert any(isinstance(r, InlineFormulaNode) and r.latex == "x^{2}" for r in para.runs)


def test_omml_block_formula():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><m:oMathPara><m:oMath>
        <m:rad><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>
      </m:oMath></m:oMathPara></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    f = result.content[0]
    assert isinstance(f, FormulaNode) and f.latex == "\\sqrt{x}"
```

测试文件顶部 import 区把 `from document2chunk.extractors.docx import DocxExtractor` 下面的 IR import 补上（后续任务共用）：

```python
from document2chunk.ir import (
    FormulaNode,
    HeadingNode,
    ImageNode,
    InlineFormulaNode,
    ListNode,
    ParagraphNode,
    TableNode,
)
```

（`test_omml_*` 函数内的局部 `from document2chunk.ir import ...` 可省略，直接用顶部 import。）

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k omml`
Expected: 3 FAILED（无 InlineFormulaNode 产出/FormulaNode 未生成——`isinstance` 断言失败或 content[0] 是 ParagraphNode）

- [ ] **Step 3: 实现 `embedded.py` 公式转换（追加）**

```python
# ============ OMML 公式（极简映射，未知结构降级文本拼接） ============


def _find_child(el, name: str):
    """按局部名取首个子元素（OMML 子元素固定顺序，局部名足够）。"""
    for child in el:
        if etree.QName(child).localname == name:
            return child
    return None


def omml_to_latex(el) -> str:
    """OMML → LaTeX 极简映射；未识别结构递归拼接子元素。"""
    if el is None:
        return ""
    ln = etree.QName(el).localname
    if ln == "t":
        return el.text or ""
    if ln == "f":
        return "\\frac{%s}{%s}" % (
            omml_to_latex(_find_child(el, "num")),
            omml_to_latex(_find_child(el, "den")),
        )
    if ln == "sSup":
        return "%s^{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sup")),
        )
    if ln == "sSub":
        return "%s_{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sub")),
        )
    if ln == "sSubSup":
        return "%s_{%s}^{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sub")),
            omml_to_latex(_find_child(el, "sup")),
        )
    if ln == "rad":
        deg = omml_to_latex(_find_child(el, "deg"))
        body = omml_to_latex(_find_child(el, "e"))
        return ("\\sqrt[%s]{%s}" % (deg, body)) if deg.strip() else ("\\sqrt{%s}" % body)
    if ln == "d":
        inner = "".join(
            omml_to_latex(c) for c in el if etree.QName(c).localname == "e"
        )
        return "(%s)" % inner
    return "".join(omml_to_latex(c) for c in el)


def omml_text(el) -> str:
    """纯文本兜底：子树内所有 m:t 拼接。"""
    if el is None:
        return ""
    return "".join(e.text or "" for e in el.iter() if etree.QName(e).localname == "t")
```

- [ ] **Step 4: 实现 parser 分派（`parser.py`）**

4a. 顶部 import 区**增量修改**（不动既有 imports，含阶段 A 的 `from document2chunk.postprocess import style_of`）：

新增两行（放 lxml import 之后即可）：

```python
from document2chunk.extractors.docx import embedded, notes
from document2chunk.extractors.docx.embedded import omml_to_latex
```

（`notes` 为 Task 6 引入，此处一并写入避免二次改动；`embedded` 供 Task 4/5 的 `iter_content`/`content_children`/`inside_textbox` 用。）

`from document2chunk.ir import (...)` 元组内按字母序插入两项：`FormulaNode,`（`BlockNode` 之后）、`InlineFormulaNode,`（`ImageNode` 之后）。

4b. `_classify` 方法开头插入公式段判定：

```python
    def _classify(self, p):
        # OMML 公式段：段落主体是 oMathPara 且无普通 run → 块级公式（阶段B §4.3）
        formula = self._block_formula(p)
        if formula is not None:
            return "formula", None, [], formula, None, []
        ppr = p.find(w("pPr"))
        ...  # 其余不动
```

同类内新增方法：

```python
    def _block_formula(self, p) -> Optional[str]:
        """段落级 oMathPara → latex；混合段落（含 run/hyperlink）返回 None 走行内。"""
        omp = None
        has_text = False
        for child in embedded.content_children(p):
            ln = etree.QName(child).localname
            if ln == "oMathPara":
                omp = child
            elif ln in ("r", "hyperlink"):
                has_text = True
        if omp is None or has_text:
            return None
        latex = " ".join(
            x
            for x in (
                omml_to_latex(c)
                for c in embedded.content_children(omp)
                if etree.QName(c).localname == "oMath"
            )
            if x.strip()
        )
        if not latex.strip():
            latex = embedded.omml_text(omp)  # 纯文本兜底（设计 §4.3）
        return latex or None
```

4c. `_parse_runs`：遍历改 `content_children`，加 oMath/oMathPara 分支、run 分支后补行内公式：

```python
    def _parse_runs(self, p, pstyle_id) -> Tuple[List[InlineNode], str]:
        inlines: List[InlineNode] = []
        text_parts: List[str] = []
        base = self._styles.merged_rpr(pstyle_id)

        for child in embedded.content_children(p):
            tag = etree.QName(child).localname
            if tag == "r":
                t, run = self._parse_run(child, base)
                if run is not None:
                    inlines.append(run)
                    text_parts.append(t)
                inlines.extend(self._inline_math(child))
            elif tag == "hyperlink":
                hl_runs: List[RunNode] = []
                hl_text: List[str] = []
                for r in child.findall(w("r")):
                    t, run = self._parse_run(r, base)
                    if run is not None:
                        hl_runs.append(run)
                        hl_text.append(t)
                target = ra(child, "id") or child.get(w("anchor")) or ""
                inlines.append(
                    HyperlinkNode(id=self._rid(), target=target, runs=hl_runs)
                )
                text_parts.append("".join(hl_text))
            elif tag == "oMath":
                latex = omml_to_latex(child)
                if latex.strip():
                    inlines.append(InlineFormulaNode(id=self._rid(), latex=latex))
                    text_parts.append(latex)
            elif tag == "oMathPara":
                # 混合段落里的公式段：内部各 oMath 作行内处理
                for om in embedded.content_children(child):
                    if etree.QName(om).localname == "oMath":
                        latex = omml_to_latex(om)
                        if latex.strip():
                            inlines.append(InlineFormulaNode(id=self._rid(), latex=latex))
                            text_parts.append(latex)
        return inlines, "".join(text_parts)

    def _inline_math(self, r) -> List[InlineFormulaNode]:
        """run 内 m:oMath → 行内公式节点（latex 同时经 _parse_run 进文本）。"""
        out: List[InlineFormulaNode] = []
        for sub in embedded.content_children(r):
            if etree.QName(sub).localname == "oMath":
                latex = omml_to_latex(sub)
                if latex.strip():
                    out.append(InlineFormulaNode(id=self._rid(), latex=latex))
        return out
```

`omml_to_latex` 在 parser 内以裸名调用（上面 4b/4c 代码已按裸名写），import 见 4a。

4d. `_parse_run`：遍历改 `content_children`，加 oMath 文本分支：

```python
    def _parse_run(self, r, base) -> Tuple[str, Optional[RunNode]]:
        parts: List[str] = []
        for sub in embedded.content_children(r):
            tag = etree.QName(sub).localname
            if tag == "t":
                parts.append(sub.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag == "br":
                parts.append("\n")
            elif tag == "oMath":
                parts.append(omml_to_latex(sub))
        text = "".join(parts)
        ...  # 其余不动
```

4e. `parse()` 主循环 kind 分派，在 `if kind == "heading":` 之前插入：

```python
            if kind == "formula":
                flush_list()
                blocks.append(FormulaNode(id=self._bid(), latex=text or None))
            elif kind == "heading":
                ...  # 原有
```

（即把原 `if kind == "heading":` 改成 `elif` 挂在后面。）

`_parse_cell_blocks` 同样在 heading 分支前插入：

```python
                if kind == "formula":
                    blocks.append(FormulaNode(id=self._bid(), latex=text or None))
                elif kind == "heading":
                    ...  # 原有
```

- [ ] **Step 5: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k omml`
Expected: 3 PASSED

- [ ] **Step 6: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 190 passed

```bash
git add src/document2chunk/extractors/docx/embedded.py src/document2chunk/extractors/docx/parser.py tests/test_docx_ooxml.py
git commit -m "feat(docx): OMML 公式解析——行内/块级 FormulaNode（极简 LaTeX 映射）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: OLE 嵌入占位 ImageNode

**Files:**
- Modify: `src/document2chunk/extractors/docx/embedded.py`
- Modify: `src/document2chunk/extractors/docx/parser.py`（`_extract_images`）
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 1 `iter_content`；Task 2 `media_info_for_rel`
- Produces:
  - `embedded.parse_ole_object(obj_el, reader, id_factory) -> ImageNode`（`alt="OLE 对象 (ProgID)"`、`image_id`=v:imagedata 的 r:id、format 取 rel 扩展名、尺寸取 v:shape style 的 pt→EMU）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 4: OLE 占位 ----------

_RELS_WMF = f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId9" Type="{R}/image" Target="media/olePreview1.wmf"/>
</Relationships>"""


def test_ole_placeholder_image():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>预算明细见下表：</w:t></w:r></w:p>
      <w:p><w:r><w:object>
        <v:shape style="width:100pt;height:60pt"><v:imagedata r:id="rId9"/></v:shape>
        <o:OLEObject ProgID="Excel.Sheet.8"/>
      </w:object></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/olePreview1.wmf": b"WMFDATA"}, rels_xml=_RELS_WMF)
    )
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    assert len(imgs) == 1
    img = imgs[0]
    assert img.alt == "OLE 对象 (Excel.Sheet.8)"
    assert img.image_id == "rId9"
    assert img.format == "wmf"
    assert img.width_emu == 1270000  # 100pt × 12700
    assert img.height_emu == 762000  # 60pt × 12700


def test_ole_without_preview_rel_still_placeholder():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:object><o:OLEObject ProgID="Equation.3"/></w:object></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    assert len(imgs) == 1
    assert imgs[0].alt == "OLE 对象 (Equation.3)"
    assert imgs[0].format is None
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k ole`
Expected: 2 FAILED（0 个 ImageNode）

- [ ] **Step 3: 实现 `embedded.py`（追加）**

文件顶部 import 区改为：

```python
import re
from typing import Iterator, Optional

from lxml import etree

from document2chunk.extractors.docx._ooxml import ra
from document2chunk.ir import ImageNode
```

追加：

```python
# ============ OLE 嵌入（占位 ImageNode，阶段B §4.4） ============

_SHAPE_WH = re.compile(r"width:([\d.]+)pt;height:([\d.]+)pt", re.IGNORECASE)


def parse_ole_object(obj_el, reader, id_factory) -> ImageNode:
    """w:object → 占位 ImageNode（预览图 rel + ProgID alt，pt→EMU）。"""
    prog = None
    embed = None
    shape = None
    for sub in obj_el.iter():
        ln = etree.QName(sub).localname
        if prog is None and ln == "OLEObject":
            prog = sub.get("ProgID")
        if embed is None and ln == "imagedata":
            embed = ra(sub, "id")
        if shape is None and ln == "shape":
            shape = sub
    fmt = None
    if embed and reader is not None:
        info = reader.media_info_for_rel(embed)
        if info:
            fmt = info[2] or None
    w = h = None
    if shape is not None:
        mt = _SHAPE_WH.search((shape.get("style") or "").replace(" ", ""))
        if mt:
            w, h = int(float(mt.group(1)) * 12700), int(float(mt.group(2)) * 12700)
    alt = f"OLE 对象 ({prog})" if prog else "OLE 对象"
    return ImageNode(
        id=id_factory(),
        image_id=embed or "",
        format=fmt,
        width_emu=w,
        height_emu=h,
        alt=alt,
    )
```

- [ ] **Step 4: 实现 parser `_extract_images` 重写（object 分支 + iter_content 去重）**

parser 顶部先加日志（`import logging` 放 stdlib import 区，`_logger` 放 import 区之后）：

```python
import logging

_logger = logging.getLogger(__name__)
```

整体替换 `_extract_images`：

```python
    def _extract_images(self, p) -> List[ImageNode]:
        out: List[ImageNode] = []
        for el in embedded.iter_content(p):
            ln = etree.QName(el).localname
            if ln == "blip":
                embed = ra(el, "embed")
                if not embed:
                    continue
                drawing = el.getparent()
                while drawing is not None and etree.QName(drawing).localname != "drawing":
                    drawing = drawing.getparent()
                cx = cy = alt = fmt = None
                if drawing is not None:
                    ext = drawing.find(f".//{{{WP}}}extent")
                    if ext is not None:
                        cx, cy = ext.get("cx"), ext.get("cy")
                    docpr = drawing.find(f".//{{{WP}}}docPr")
                    if docpr is not None:
                        alt = docpr.get("descr") or docpr.get("name")
                if self._reader is not None:
                    media = self._reader.media_for_rel(embed)
                    if media is not None:
                        _, fmt = media
                out.append(
                    ImageNode(
                        id=self._bid(),
                        image_id=embed,
                        format=fmt,
                        width_emu=int(cx) if cx and cx.isdigit() else None,
                        height_emu=int(cy) if cy and cy.isdigit() else None,
                        alt=alt,
                    )
                )
            elif ln == "object":
                try:
                    out.append(embedded.parse_ole_object(el, self._reader, self._bid))
                except Exception as e:  # 单嵌入物失败不拖垮段落（设计 §5 / spec §3.8）
                    _logger.warning("OLE 解析失败，跳过: %s", e)
        return out
```

- [ ] **Step 5: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k ole`
Expected: 2 PASSED

- [ ] **Step 6: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 192 passed

```bash
git add src/document2chunk/extractors/docx/embedded.py src/document2chunk/extractors/docx/parser.py tests/test_docx_ooxml.py
git commit -m "feat(docx): OLE 嵌入占位 ImageNode（ProgID alt + 预览图 rel）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 文本框内联展开 + 图片泄漏修复

**Files:**
- Modify: `src/document2chunk/extractors/docx/parser.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 1 `iter_content`/`content_children`/`inside_textbox`；Task 3 formula kind
- Produces:
  - `DocumentParser._textbox_blocks(p) -> List[BlockNode]`（锚点段内 txbxContent 段落/表格 → 块，`metadata={"textbox": True}`）
  - `_extract_images` 增加文本框内外判定（外层跳过框内 blip/object；`_textbox_blocks` 内层解析时框内 p 自身在框内 → 收集）
  - `parse()` 主循环与 `_parse_cell_blocks` 在段落后调用展开

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 5: 文本框 ----------


def test_textbox_wps_choice_inline_expansion():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><mc:AlternateContent>
        <mc:Choice Requires="wps">
          <w:drawing><wp:anchor><wp:extent cx="5000000" cy="1000000"/><wp:docPr descr="红头"/>
            <a:graphic><wps:txbx><w:txbxContent>
              <w:p><w:r><w:t>广东某高速公路有限公司</w:t></w:r></w:p>
              <w:p><w:r><w:t>关于XX项目的批复</w:t></w:r></w:p>
            </w:txbxContent></wps:txbx></a:graphic></wp:anchor></w:drawing>
        </mc:Choice>
        <mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>FB红头</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>
      </mc:AlternateContent></w:r></w:p>
      <w:p><w:r><w:t>正文第一段</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts[:3] == ["广东某高速公路有限公司", "关于XX项目的批复", "正文第一段"]
    assert "FB红头" not in "".join(texts)  # Fallback 不双计
    assert result.content[0].metadata.get("textbox") is True


def test_textbox_vml_form():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:r><w:t>VML标题</w:t></w:r></w:p>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    assert result.content[0].text == "VML标题"
    assert result.content[0].metadata.get("textbox") is True
    assert result.content[1].text == "正文"


def test_image_inside_textbox_no_leak():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:r><w:t>框内文字</w:t></w:r></w:p>
        <w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/>
          <a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/image1.png": b"PNG"}, rels_xml=_RELS_PNG)
    )
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    # 不泄出为顶层重复块：恰 1 个，且位于文本框展开区内（第 2 块）
    assert len(imgs) == 1
    assert result.content[1] is imgs[0]
    assert result.content[0].text == "框内文字"
    assert result.content[2].text == "正文"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k textbox`
Expected: 3 FAILED（文本框内容丢失 / 图片泄漏双计）

- [ ] **Step 3: 实现 parser**

3a. `_extract_images` 开头加框内跳过（循环体首行）：

```python
    def _extract_images(self, p) -> List[ImageNode]:
        out: List[ImageNode] = []
        host_in_textbox = embedded.inside_textbox(p)
        for el in embedded.iter_content(p):
            if not host_in_textbox and embedded.inside_textbox(el):
                continue  # 文本框内图片随展开逻辑处理，不泄出顶层
            ln = etree.QName(el).localname
            ...  # 其余不动
```

3b. 新增 `_textbox_blocks`（放在 `_extract_images` 后）：

```python
    def _textbox_blocks(self, p) -> List[BlockNode]:
        """锚点段内文本框内容 → 块列表（内联展开，紧随锚点段落，阶段B §4.2）。"""
        blocks: List[BlockNode] = []
        for el in embedded.iter_content(p):
            if etree.QName(el).localname != "txbxContent":
                continue
            try:
                for child in embedded.content_children(el):
                    tag = etree.QName(child).localname
                    if tag == "p":
                        kind, level, runs, text, list_info, images = self._classify(child)
                        if images:
                            blocks.extend(images)
                        md = {"textbox": True}
                        if self._is_centered(child):
                            md["centered"] = True
                        if kind == "formula":
                            blocks.append(
                                FormulaNode(id=self._bid(), latex=text or None, metadata=md)
                            )
                        elif kind == "heading" and text:
                            _, hsrc = self._heading_level_source(
                                child, self._paragraph_style(child), text
                            )
                            md["heading_source"] = hsrc or "heuristic"  # 阶段A 契约（设计 §7）
                            blocks.append(
                                HeadingNode(
                                    id=self._bid(), level=level, text=text, runs=runs,
                                    metadata=md,
                                )
                            )
                        elif text or runs:
                            blocks.append(
                                ParagraphNode(id=self._bid(), runs=runs, text=text, metadata=md)
                            )
                    elif tag == "tbl":
                        blocks.append(self._parse_table(child))
            except Exception as e:  # 单文本框失败不拖垮段落（设计 §5）
                _logger.warning("文本框解析失败，跳过: %s", e)
        return blocks
```

3c. `parse()` 主循环：在段落 kind 分派链（`if kind == "formula": ... elif kind == "heading": ... elif kind == "list": ... else: ...`）**之后**、循环体末尾（TOC `continue` 分支之后的位置）插入：

```python
            # 文本框内容内联展开（紧随锚点段落）
            tb = self._textbox_blocks(child)
            if tb:
                flush_list()
                blocks.extend(tb)
```

注意：`continue` 的分支（tbl/TOC）不会走到这里，属预期——body 顶层锚点必为段落。

3d. `_parse_cell_blocks` 的 `if tag == "p":` 分支末尾追加：

```python
                tb = self._textbox_blocks(child)
                if tb:
                    blocks.extend(tb)
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k textbox`
Expected: 3 PASSED

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 195 passed

```bash
git add src/document2chunk/extractors/docx/parser.py tests/test_docx_ooxml.py
git commit -m "feat(docx): 文本框内容内联展开（wps/VML 双形态）+ 修图片递归泄漏

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 尾注/脚注——引用标记 + 文末集中

**Files:**
- Create: `src/document2chunk/extractors/docx/notes.py`
- Modify: `src/document2chunk/extractors/docx/parser.py`（仅 marker 分支）
- Modify: `src/document2chunk/extractors/docx/extractor.py`（postprocess 后追加内容块）
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 2 `PackageReader.read_xml`；Task 3 `content_children`（marker 分支加在 `_parse_run` 循环）
- Produces:
  - `notes.parse_notes(reader, kind: str) -> List[ParagraphNode]`（kind ∈ {"endnote","footnote"}；跳过 separator 条目；按 id 数值序；块 id=`note_{kind}_{id}`、`metadata={"note":{"type":kind,"id":id}}`、text=`"[尾注N] {内容}"`）
  - `_parse_run`：`footnoteReference`/`endnoteReference` → run 文本追加 `[尾注N]`/`[脚注N]`
  - `extractor.extract()`：postprocess 返回 main_content 之后追加 endnote+footnote 块（**必须后置**：split_attachments 会把 postprocess 前追加的块划进末尾附件段）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 6: 尾注/脚注 ----------

ENDNOTES = f"""<w:endnotes {DOC_NS}>
  <w:endnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>
  <w:endnote w:type="continuationSeparator"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>
  <w:endnote w:id="2"><w:p><w:r><w:t>Keoleian G A, et al. Life Cycle Assessment.</w:t></w:r></w:p></w:endnote>
  <w:endnote w:id="1"><w:p><w:r><w:t>王某某. 沥青路面研究[J]. 公路, 2023.</w:t></w:r></w:p></w:endnote>
</w:endnotes>"""


def test_endnote_marker_and_tail_blocks():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>引用一处</w:t></w:r><w:r><w:endnoteReference w:id="2"/></w:r></w:p>
      <w:p><w:r><w:t>引用二处</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc, endnotes_xml=ENDNOTES))
    assert result.content[0].text == "引用一处[尾注2]"
    assert result.content[1].text == "引用二处[尾注1]"
    tail = result.content[2:]
    assert len(tail) == 2  # separator 条目不计
    assert tail[0].text == "[尾注1] 王某某. 沥青路面研究[J]. 公路, 2023."  # id 数值序
    assert tail[1].text == "[尾注2] Keoleian G A, et al. Life Cycle Assessment."
    assert tail[0].metadata["note"] == {"type": "endnote", "id": "1"}


def test_footnote_same_mechanism():
    footnotes = f"""<w:footnotes {DOC_NS}>
      <w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
      <w:footnote w:id="1"><w:p><w:r><w:t>见附录A。</w:t></w:r></w:p></w:footnote>
    </w:footnotes>"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>说明</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc, footnotes_xml=footnotes))
    assert result.content[0].text == "说明[脚注1]"
    assert result.content[1].text == "[脚注1] 见附录A。"
    assert result.content[1].metadata["note"] == {"type": "footnote", "id": "1"}


def test_empty_notes_part_no_blocks():
    """WPS 空壳 footnotes.xml（40% 样本）不产出任何块。"""
    shell = f"""<w:footnotes {DOC_NS}>
      <w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
      <w:footnote w:type="continuationSeparator"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
    </w:footnotes>"""
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>'
    result = DocxExtractor().extract(make_docx(doc, footnotes_xml=shell))
    assert len(result.content) == 1 and result.content[0].text == "正文"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k "note"`
Expected: 3 FAILED（无标记无尾块）

- [ ] **Step 3: 实现 `notes.py`（新建）**

```python
"""尾注/脚注 part → 内容块（阶段 B 设计 §4.5）。

真实使用：尾注为参考文献（单文件最多 61 处）；脚注在 799 样本中零使用，
机制相同顺带实现。内容文末集中、按 id 数值序（还原引用顺序）。
"""

from __future__ import annotations

from typing import List

from lxml import etree

from document2chunk.extractors.docx._ooxml import wa
from document2chunk.ir import ParagraphNode


def parse_notes(reader, kind: str) -> List[ParagraphNode]:
    """kind ∈ {"endnote", "footnote"} → 内容块列表（可能为空）。"""
    part = "word/endnotes.xml" if kind == "endnote" else "word/footnotes.xml"
    root = reader.read_xml(part)
    if root is None:
        return []
    tag = "endnote" if kind == "endnote" else "footnote"
    label = "尾注" if kind == "endnote" else "脚注"
    entries = []
    for note in root:
        if etree.QName(note).localname != tag:
            continue
        if wa(note, "type"):  # separator / continuationSeparator 跳过
            continue
        nid = wa(note, "id") or ""
        text = " ".join("".join(note.itertext()).split())
        if not text:
            continue
        entries.append((nid, text))

    def _num(nid: str) -> int:
        try:
            return int(nid)
        except ValueError:
            return 0

    entries.sort(key=lambda e: _num(e[0]))
    return [
        ParagraphNode(
            id=f"note_{kind}_{nid}",
            text=f"[{label}{nid}] {text}",
            metadata={"note": {"type": kind, "id": nid}},
        )
        for nid, text in entries
    ]
```

- [ ] **Step 4: 实现 parser**

4a. parser 顶部已有 `from document2chunk.extractors.docx import embedded, notes`（Task 3 4a 预置），无需再加。

4b. `_parse_run` 循环加标记分支（在 `elif tag == "oMath":` 之后）：

```python
            elif tag in ("footnoteReference", "endnoteReference"):
                nid = wa(sub, "id") or ""
                label = "尾注" if tag == "endnoteReference" else "脚注"
                parts.append(f"[{label}{nid}]")
```

4c. `extractor.py`：在 postprocess 调用之后（`main_content, attach_segments = postprocess(...)` 之后、构建 `result` 之前）插入：

```python
        # 尾注/脚注内容：正文末尾集中。必须在 postprocess 之后——
        # 若在之前追加，split_attachments 会把尾注划进文档末尾的附件段（设计 §4.5）
        main_content = (
            main_content
            + notes.parse_notes(reader, "endnote")
            + notes.parse_notes(reader, "footnote")
        )
```

import 区追加：`from document2chunk.extractors.docx import notes`（放在 parser import 之后）。

- [ ] **Step 5: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k "note"`
Expected: 3 PASSED

- [ ] **Step 6: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 198 passed

```bash
git add src/document2chunk/extractors/docx/notes.py src/document2chunk/extractors/docx/parser.py src/document2chunk/extractors/docx/extractor.py tests/test_docx_ooxml.py
git commit -m "feat(docx): 尾注/脚注引用标记 + 内容文末集中（notes.py）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: sdt 内容控件透明展开

**Files:**
- Modify: `src/document2chunk/extractors/docx/parser.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces: `DocumentParser._iter_body_parts(body, depth=0)` 生成器（`w:sdt` 递归展开 `sdtContent`，深度上限 10）；`parse()` 主循环改用它遍历

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 7: sdt 展开 ----------


def test_sdt_wrapped_toc_consumed():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj></w:sdtPr>
        <w:sdtContent>
          <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> TOC \\o "1-3" </w:instrText></w:r></w:p>
          <w:p><w:hyperlink><w:r><w:t>第一章 背景</w:t></w:r></w:hyperlink></w:p>
          <w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
        </w:sdtContent>
      </w:sdt>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts == ["正文"]  # TOC 条目不进 content（既有语义）
    assert result.toc_entries and result.toc_entries[0].text == "第一章 背景"


def test_sdt_wrapped_body_not_lost():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt><w:sdtContent>
        <w:p><w:r><w:t>sdt内段落一</w:t></w:r></w:p>
        <w:p><w:r><w:t>sdt内段落二</w:t></w:r></w:p>
      </w:sdtContent></w:sdt>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts == ["sdt内段落一", "sdt内段落二"]
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k sdt`
Expected: 2 FAILED（sdt 整体被跳过：内容为空 / toc_entries 为 None）

- [ ] **Step 3: 实现 parser**

3a. 新增方法（放在 `parse()` 之后）：

```python
    def _iter_body_parts(self, body, depth: int = 0):
        """body 子元素遍历；w:sdt 透明展开 sdtContent（上限 10 层，阶段B §4.6）。"""
        if depth > 10:
            return
        for child in body:
            if etree.QName(child).localname == "sdt":
                content = child.find(w("sdtContent"))
                if content is not None:
                    yield from self._iter_body_parts(content, depth + 1)
                continue
            yield child
```

3b. `parse()` 主循环首行 `for child in body:` 改为：

```python
        for child in self._iter_body_parts(body):
```

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k sdt`
Expected: 2 PASSED

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 200 passed

```bash
git add src/document2chunk/extractors/docx/parser.py tests/test_docx_ooxml.py
git commit -m "feat(docx): sdt 内容控件透明展开（TOC 容器走既有消费路径）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: extractor——image_dir 落盘 + 页眉元数据 + 页眉页脚锁

**Files:**
- Modify: `src/document2chunk/extractors/docx/extractor.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 2 `header_elements`/`media_info_for_rel`
- Produces:
  - `DocxExtractor.extract(source, *, options=None, heuristic_headings=False, image_dir=None)`（新 kwarg）
  - `_export_media(reader, blocks, image_dir)` 模块级函数：仅落盘被引用媒体、zip 内原名、失败 WARN
  - `metadata.custom["docx"]["header_text"]`（页眉非空文本拼接，截断 200 字）
  - 页眉/页脚内容不进 content（锁测试）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- Task 8: image_dir / 页眉 ----------


def test_image_dir_exports_media(tmp_path):
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/><wp:docPr descr="logo"/>
        <a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG),
        image_dir=tmp_path,
    )
    assert (tmp_path / "image1.png").read_bytes() == b"PNGDATA"
    # IR 不引磁盘路径
    assert str(tmp_path) not in str(result.model_dump())


def test_image_dir_none_no_export(tmp_path):
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>'
    DocxExtractor().extract(make_docx(doc), image_dir=None)
    assert not (tmp_path / "image1.png").exists()


def test_header_text_metadata_and_content_lock():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    hdr = f'<w:hdr {DOC_NS}><w:p><w:r><w:t>粤高速集团文件</w:t></w:r></w:p></w:hdr>'
    result = DocxExtractor().extract(
        make_docx(doc, header_parts={"word/header1.xml": hdr})
    )
    texts = "".join(getattr(b, "text", "") for b in result.content)
    assert "粤高速集团文件" not in texts  # 页眉不进正文（锁现状）
    assert result.metadata.custom["docx"]["header_text"] == "粤高速集团文件"
```

- [ ] **Step 2: 运行验证失败**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k "image_dir or header_text"`
Expected: 3 FAILED（`TypeError: extract() got an unexpected keyword argument 'image_dir'` 等）

- [ ] **Step 3: 实现（`extractor.py` 增量修改，保留阶段 A 的 postprocess 接入）**

3a. import 区增量修改：

```python
import logging  # 顶部 stdlib 区
from typing import Iterator, List, Optional  # List/Optional 已有，追加 Iterator
```

```python
from document2chunk.ir import (   # 既有元组内追加 ImageNode、TableNode 两项（字母序）
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    ImageNode,
    ListNode,
    ParagraphNode,
    SourceType,
    TableNode,
)
_logger = logging.getLogger(__name__)  # import 区之后
```

3b. 模块级追加（`_body_font_size` 之后）：

```python
def _iter_images(blocks) -> Iterator[ImageNode]:
    """递归收集块序列中的 ImageNode（含表格/列表嵌套）。"""
    for b in blocks:
        if isinstance(b, ImageNode):
            yield b
        elif isinstance(b, TableNode):
            for row in b.rows:
                for cell in row.cells:
                    yield from _iter_images(cell.blocks)
        elif isinstance(b, ListNode):
            for item in b.items:
                yield from _iter_images(item.blocks)


def _export_media(reader: PackageReader, blocks, image_dir) -> None:
    """仅落盘被引用媒体，zip 内原名；失败 WARN 跳过（对齐 PDF 路，阶段B §4.7）。"""
    out = Path(image_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _logger.warning("docx 媒体目录创建失败 %s: %s", out, e)
        return
    for img in _iter_images(blocks):
        if not img.image_id:
            continue
        info = reader.media_info_for_rel(img.image_id)
        if info is None:
            continue
        name, data, _ext = info
        try:
            (out / name).write_bytes(data)
        except OSError as e:
            _logger.warning("docx 媒体落盘失败 %s: %s", name, e)
```

3c. `extract` 签名追加 kwarg：

```python
    def extract(
        self,
        source,
        *,
        options=None,
        heuristic_headings: bool = False,
        image_dir=None,
    ) -> ExtractionResult:
```

3d. `blocks, toc_entries = parser.parse(doc_elem)` 之后、`core = reader.core_properties()` 之前插入（用 parser 的 blocks：postprocess 前的全集）：

```python
        # 媒体落盘（仅被引用媒体；IR 不引磁盘路径）
        if image_dir is not None:
            _export_media(reader, blocks, image_dir)
```

3e. `metadata = _meta()` 之后插入：

```python
        # 页眉文本 → metadata.custom（不进正文，阶段B §4.8）
        header_lines = [
            " ".join("".join(h.itertext()).split()) for h in reader.header_elements()
        ]
        header_text = " / ".join(x for x in header_lines if x)[:200]
        if header_text:
            metadata.custom["docx"] = {"header_text": header_text}
```

注意：Task 6 已在本文件加过 notes 追加（postprocess 之后）与 `import notes`，本任务不触碰那段。

- [ ] **Step 4: 运行验证通过**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k "image_dir or header_text"`
Expected: 3 PASSED

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest -q` → Expected: 203 passed

```bash
git add src/document2chunk/extractors/docx/extractor.py tests/test_docx_ooxml.py
git commit -m "feat(docx): image_dir 媒体落盘 + 页眉文本进 metadata.custom

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: 集成测试 + spec 同步 + 真实样本抽查

**Files:**
- Modify: `openspec/specs/docx-extractor/spec.md`
- Create: `scripts/spotcheck_docx_ooxml.py`
- Test: `tests/test_docx_ooxml.py`

**Interfaces:**
- Consumes: Task 1-8 全部产出
- Produces: 红头公文组合集成测试；spec 能力同步；真实样本人工抽查脚本（验收标准 2 的抽查环节）

- [ ] **Step 1: 写集成测试（追加）**

```python
# ---------- Task 9: 红头公文组合集成 ----------


def test_gongwen_integration():
    """红头公文组合：文本框红头(双写) + 标题 + OLE + 尾注 → 阅读顺序正确。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><mc:AlternateContent>
        <mc:Choice Requires="wps"><w:drawing><wp:anchor>
          <a:graphic><wps:txbx><w:txbxContent>
            <w:p><w:r><w:t>广东某高速公路有限公司</w:t></w:r></w:p>
          </w:txbxContent></wps:txbx></a:graphic></wp:anchor></w:drawing>
        </mc:Choice>
        <mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>FB</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>
      </mc:AlternateContent></w:r></w:p>
      <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第一章 总体要求</w:t></w:r></w:p>
      <w:p><w:r><w:t>预算明细见</w:t></w:r><w:r><w:object>
        <v:shape style="width:90pt;height:50pt"><v:imagedata r:id="rId9"/></v:shape>
        <o:OLEObject ProgID="Excel.Sheet.8"/>
      </w:object></w:r></w:p>
      <w:p><w:r><w:t>参考文献</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(
            doc,
            endnotes_xml=ENDNOTES,
            media={"word/media/olePreview1.wmf": b"WMF"},
            rels_xml=_RELS_WMF,
        )
    )
    logical = assemble(result)
    md = to_markdown(logical)
    # 阅读顺序：红头 → 标题 → 正文(OLE alt) → 尾注
    assert md.index("广东某高速公路有限公司") < md.index("# 第一章 总体要求")
    assert md.index("# 第一章 总体要求") < md.index("OLE 对象 (Excel.Sheet.8)")
    assert md.index("OLE 对象") < md.index("[尾注1] 王某某")
    assert "FB" not in md  # Fallback 文本（本 fixture 中仅为 "FB"）不出现
```

测试文件顶部 import 补：

```python
from document2chunk.export import to_markdown
from document2chunk.structure import assemble
```

- [ ] **Step 2: 运行（Task 1-8 已实现，应直接通过；若失败说明前序有缺）**

Run: `uv run pytest tests/test_docx_ooxml.py -v -k integration`
Expected: 1 PASSED

- [ ] **Step 3: spec.md 同步（`openspec/specs/docx-extractor/spec.md`）**

3a. §2 处理流程图中间补两行（在 `→ TOC 域识别` 之前）：

```
         · mc:AlternateContent 只走 mc:Choice（WPS 双写去重）
         · 文本框 txbxContent → 锚点段后内联展开（metadata["textbox"]）
         · m:oMath/oMathPara → InlineFormulaNode/FormulaNode
         · w:object(OLE) → 占位 ImageNode（ProgID alt）
         · 尾注/脚注 → 引用标记 + 文末内容块（metadata["note"]）
         · w:sdt → 透明展开 sdtContent
```

3b. §3.4 结构元素末尾追加：

```
- **必须**：`mc:AlternateContent` 双写去重（只走 `mc:Choice`，无 Choice 走 `mc:Fallback`），文字/图片不双计。
- **必须**：文本框 `w:txbxContent`（wps/VML 两形态）→ 锚点段落之后内联展开，块带 `metadata["textbox"]=true`；框内图片随展开，不泄出顶层。
- **必须**：OMML `m:oMath`→`InlineFormulaNode`、`m:oMathPara`→`FormulaNode`（极简 LaTeX 映射：frac/上下标/sqrt/括号，纯文本兜底）。
- **必须**：OLE `w:object` → 占位 `ImageNode`（`alt="OLE 对象 (ProgID)"`、预览图 `r:id`、v:style pt→EMU）。
- **必须**：尾注/脚注引用 → run 文本追加 `[尾注N]`/`[脚注N]`；内容 part（跳过 separator）→ 文末集中块，`metadata["note"]={"type","id"}`，按 id 数值序。
- **必须**：`w:sdt` → 透明展开 `sdtContent`（TOC 容器走既有 TOC 消费），嵌套上限 10 层。
- **必须**：`image_dir` 提供时仅落盘被引用媒体（zip 内原名）；页眉非空文本 → `metadata.custom["docx"]["header_text"]`（截断 200 字）；页眉页脚内容不进 `content`（§3.5 禁止项指不进正文/不模拟版面，与 metadata 记录不冲突）。
```

3c. §3.7 改为：

```
### 3.7 高级特性

- 批注（`comments.xml`）、修订（`<w:ins>`/`<w:del>`）：**默认不实现**（799 样本各约 1%，见阶段 B 扫描）；内容控件（`<w:sdt>`）已实现透明展开（§3.4）。
```

3d. §4 场景追加：

```
- **当** 段落含 WPS 双写 AlternateContent（Choice/Fallback 各一份文本）**那么** 文本只出一份（取 Choice）。
- **当** 红头文本框锚定首段 **那么** 其内段落紧随首段展开且 `metadata["textbox"]=true`。
- **当** run 含 `<w:endnoteReference w:id="3"/>` 且 endnotes.xml 有 id=3 条目 **那么** 正文含 `[尾注3]`，文末出现 `[尾注3] {内容}` 块。
- **当** run 含 `<w:object>` + ProgID=Excel.Sheet.8 **那么** 产出 `ImageNode(alt="OLE 对象 (Excel.Sheet.8)")`。
```

- [ ] **Step 4: 真实样本抽查脚本（新建 `scripts/spotcheck_docx_ooxml.py`）**

```python
# -*- coding: utf-8 -*-
"""真实公文样本人工抽查（阶段 B 验收：每特性挑 1 个样本导出 markdown 头部）。

用法: uv run python scripts/spotcheck_docx_ooxml.py D:/document2chunk-test/docx
人工核对：红头在文档头部 / OLE 占位 / 尾注在末尾 / 公式 latex / 无 Fallback 双份文字。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from lxml import etree

from document2chunk.export import to_markdown
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.structure import assemble

WANT = {
    "txbxContent": None,
    "oMath": None,
    "object": None,
    "endnoteReference": None,
    "AlternateContent": None,
}


def main() -> None:
    parser = etree.XMLParser(recover=True)
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for p in sorted(root.rglob("*.docx")):
        try:
            doc = etree.fromstring(zipfile.ZipFile(p).read("word/document.xml"), parser=parser)
        except Exception:  # noqa: BLE001
            continue
        lns = {etree.QName(e).localname for e in doc.iter()}
        for feat in list(WANT):
            if WANT[feat] is None and feat in lns:
                WANT[feat] = p
        if all(WANT.values()):
            break
    for feat, p in WANT.items():
        if p is None:
            print(f"\n########## {feat}: 无样本")
            continue
        print(f"\n\n########## {feat} → {p.name}")
        result = DocxExtractor().extract(str(p))
        md = to_markdown(assemble(result))
        print(md[:2000])


if __name__ == "__main__":
    main()
```

Run: `PYTHONUTF8=1 uv run python scripts/spotcheck_docx_ooxml.py "D:/document2chunk-test/docx"`
Expected: 5 个特性各输出一段 markdown。**人工核对**（执行者自查后向用户报告）：
1. txbxContent 样本：红头/标题类文字出现在文档头部，无重复
2. AlternateContent 样本：无同一段文字出现两份
3. object 样本：`![OLE 对象 (…)](...)` 占位存在
4. endnoteReference 样本：文末有 `[尾注N]` 参考文献块
5. oMath 样本：`$...$` 公式段合理（极简映射，复杂公式允许文本化）

- [ ] **Step 5: 全量回归**

Run: `uv run pytest -q`
Expected: 204 passed（180 基线 + 24 新测试：23 计划 + 1 修复轮），无 FAILED

- [ ] **Step 6: 提交**

```bash
git add tests/test_docx_ooxml.py openspec/specs/docx-extractor/spec.md scripts/spotcheck_docx_ooxml.py
git commit -m "test(docx): 红头公文组合集成测试 + spec 能力同步 + 真实样本抽查脚本

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 完成定义（对照设计文档 §6.2 验收）

1. ✅ 频率表在设计文档 §2.1
2. ✅ 日出现特性全覆盖（Task 3-8）+ 真实样本人工抽查（Task 9 Step 4）
3. ✅ 零频率记录（设计文档 §1.3：脚注专属增强/修订/批注/复杂表截图/EMF转PNG 不做）
4. ✅ pytest 全绿、PDF/OCR 零改动
5. ✅ spec.md 同步（Task 9 Step 3）
