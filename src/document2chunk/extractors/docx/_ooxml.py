"""OOXML 命名空间与 clark 记法辅助。"""

from __future__ import annotations

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"


def w(tag: str) -> str:
    """w:tag → clark 记法。"""
    return f"{{{W}}}{tag}"


def wa(elem, name: str):
    """读 w 命名空间属性（w:name）。"""
    return elem.get(f"{{{W}}}{name}")


def ra(elem, name: str):
    """读 r 命名空间属性（r:name，如 r:embed）。"""
    return elem.get(f"{{{R}}}{name}")
