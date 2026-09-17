"""PackageReader —— .docx (ZIP) 读取。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from lxml import etree

from document2chunk.extractors.docx._ooxml import CP, DC, DCTERMS, w


class PackageReader:
    """.docx 包读取器（lxml recover=True 处理畸形 XML）。"""

    def __init__(self, source) -> None:
        if isinstance(source, (bytes, bytearray)):
            self._zip = zipfile.ZipFile(io.BytesIO(bytes(source)))
        else:
            self._zip = zipfile.ZipFile(str(source))
        self._rels_elem: Optional[etree._Element] = None  # rels 只解析一次
        self._rel_cache: dict = {}  # rel_id -> Optional[(target, name, ext)]

    def read_bytes(self, name: str) -> Optional[bytes]:
        try:
            return self._zip.read(name)
        except KeyError:
            return None

    def read_xml(self, name: str) -> Optional[etree._Element]:
        data = self.read_bytes(name)
        if data is None:
            return None
        parser = etree.XMLParser(recover=True)
        return etree.fromstring(data, parser=parser)

    # ---- 核心部件 ----

    def document_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/document.xml")

    def styles_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/styles.xml")

    def numbering_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/numbering.xml")

    def endnotes_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/endnotes.xml")

    def footnotes_element(self) -> Optional[etree._Element]:
        return self.read_xml("word/footnotes.xml")

    def header_elements(self) -> list:
        """word/header*.xml 列表（页眉文本进 metadata，不进正文）。"""
        return list(self.iter_header_elements())

    def iter_header_elements(self):
        """惰性逐个产出 word/header* 解析树（namelist 顺序，可提前停止）。"""
        for name in self._zip.namelist():
            if name.startswith("word/header"):
                el = self.read_xml(name)
                if el is not None:
                    yield el

    def core_properties(self) -> dict:
        root = self.read_xml("docProps/core.xml")
        props = {}
        if root is None:
            return props

        def txt(ns: str, tag: str) -> Optional[str]:
            el = root.find(f"{{{ns}}}{tag}")
            return (el.text or "").strip() if el is not None and el.text else None

        props["title"] = txt(DC, "title")
        props["author"] = txt(DC, "creator")
        props["language"] = txt(DC, "language")
        props["created"] = txt(DCTERMS, "created")
        props["modified"] = txt(DCTERMS, "modified")
        props["company"] = txt(CP, "company")
        return props

    def media_for_rel(self, rel_id: str) -> Optional[Tuple[bytes, str]]:
        """r:embed → (image_bytes, ext)。"""
        info = self.media_info_for_rel(rel_id)
        if info is None:
            return None
        return info[1], info[2]

    def _rel_lookup(self, rel_id: str) -> Optional[Tuple[str, str, str]]:
        """r:id/r:embed → (target, name, ext)，带缓存。rels 树只解析一次，
        每个 rel_id 只线性扫一次（避免 N 张图 × M 条 rels 的 O(N·M)）。"""
        if rel_id in self._rel_cache:
            return self._rel_cache[rel_id]
        if self._rels_elem is None:
            self._rels_elem = self.read_xml("word/_rels/document.xml.rels")
        rels = self._rels_elem
        result: Optional[Tuple[str, str, str]] = None
        if rels is not None:
            # Relationship 节点在 relationships 命名空间，属性无前缀
            for rel in rels:
                if rel.get("Id") == rel_id:
                    target = rel.get("Target") or ""
                    name = target.rsplit("/", 1)[-1]
                    ext = target.rsplit(".", 1)[-1].lower() if "." in target else ""
                    result = (target, name, ext)
                    break
        self._rel_cache[rel_id] = result
        return result

    def rel_target(self, rel_id: str) -> Optional[Tuple[str, str]]:
        """r:id/r:embed → (媒体 zip 内原名, ext)。只查 rels，绝不解压媒体字节。"""
        info = self._rel_lookup(rel_id)
        if info is None:
            return None
        return info[1], info[2]

    def media_info_for_rel(self, rel_id: str) -> Optional[Tuple[str, bytes, str]]:
        """r:id/r:embed → (媒体 zip 内原名, bytes, ext)。"""
        info = self._rel_lookup(rel_id)
        if info is None:
            return None
        target, name, ext = info
        # Target 形如 "media/image1.png"（相对 word/）
        data = self.read_bytes("word/" + target)
        if data is None:
            return None
        return name, data, ext
