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
        rels = self.read_xml("word/_rels/document.xml.rels")
        if rels is None:
            return None
        # Relationship 节点在 relationships 命名空间，属性无前缀
        for rel in rels:
            if rel.get("Id") == rel_id:
                target = rel.get("Target") or ""
                # Target 形如 "media/image1.png"（相对 word/）
                data = self.read_bytes("word/" + target)
                ext = target.rsplit(".", 1)[-1].lower() if "." in target else ""
                if data is not None:
                    return data, ext
                return None
        return None
