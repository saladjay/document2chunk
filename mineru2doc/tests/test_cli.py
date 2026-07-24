import io
import zipfile

import pytest

from mineru2doc.cli import _safe_extract


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in entries.items():
            z.writestr(name, body)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_safe_extract_normal(tmp_path):
    z = _zip({"result.md": "# 标题", "images/a.jpg": "img"})
    saved = _safe_extract(z, str(tmp_path))
    assert "result.md" in saved
    assert (tmp_path / "result.md").read_text(encoding="utf-8") == "# 标题"
    assert (tmp_path / "images" / "a.jpg").read_text() == "img"


def test_safe_extract_rejects_zip_slip(tmp_path):
    z = _zip({"../evil.txt": "x"})
    with pytest.raises(ValueError):
        _safe_extract(z, str(tmp_path))
    assert not (tmp_path.parent / "evil.txt").exists()


def test_safe_extract_rejects_absolute(tmp_path):
    z = _zip({"/etc/evil.txt": "x"})
    with pytest.raises(ValueError):
        _safe_extract(z, str(tmp_path))
