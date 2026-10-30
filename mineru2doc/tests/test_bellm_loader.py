"""BellmLoader 单测：httpx.MockTransport 桩网关，覆盖 200/错误/data-url 剥前缀。"""
import base64

import httpx
import pytest

from mineru2doc.loader import BellmLoader, MinerULoaderError

JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fakejpeg").decode()


class _SpyLoader:
    def __init__(self, tag):
        self.tag = tag

    def load(self, source, *, image_out_dir=None):
        return self


def _transport(handler):
    # httpx.Client 不能充当 transport（无 handle_request），直接给 MockTransport。
    return httpx.MockTransport(handler)


def test_md_only(tmp_path):
    def handler(request):
        assert request.url.path == "/bellm/MinerU/file_parse"
        assert b'filename="upload.pdf"' in request.content
        assert request.headers["Authorization"] == "Basic a2V4"
        return httpx.Response(200, json={"md_content": "# 标题\n\n正文。"})

    doc = BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                      transport=_transport(handler)).load(b"%PDF-1.4 fake")
    assert doc.blocks and doc.blocks[0].text == "标题"


def test_images_data_url_stripped(tmp_path):
    def handler(request):
        return httpx.Response(200, json={
            "md_content": "# T\n\n正文",
            "images": {"ab12.jpg": f"data:image/jpeg;base64,{JPEG_B64}"},
        })

    out = tmp_path / "imgs"
    BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                transport=_transport(handler)).load(b"%PDF-1.4", image_out_dir=str(out))
    assert (out / "ab12.jpg").read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"


def test_bad_auth_502_error():
    def handler(request):
        return httpx.Response(401, json={"message": "Invalid authentication credentials"})

    with pytest.raises(MinerULoaderError, match="401"):
        BellmLoader("http://gw:1/bellm/MinerU", auth="Basic wrong",
                    transport=_transport(handler)).load(b"%PDF-1.4")


def test_no_md_content():
    def handler(request):
        return httpx.Response(200, json={"error": "boom"})

    with pytest.raises(MinerULoaderError, match="md_content"):
        BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                    transport=_transport(handler)).load(b"%PDF-1.4")


def test_empty_auth_rejected_at_init():
    with pytest.raises(MinerULoaderError, match="MINERU_BELLM_AUTH"):
        BellmLoader("http://gw:1/bellm/MinerU", auth="")


def test_load_dispatch_bellm_by_env(monkeypatch):
    """engine 由 env 解析：MINERU_ENGINE=bellm 时走 BellmLoader（base_url/auth 取 env）。"""
    from mineru2doc import loader as loader_mod

    seen = {}

    class _SpyBellm:
        def __init__(self, base_url, *, auth, **kw):
            seen.update(base_url=base_url, auth=auth)

        def load(self, source, *, image_out_dir=None):
            return _SpyLoader("BellmLoader")

    monkeypatch.setenv("MINERU_ENGINE", "bellm")
    monkeypatch.setenv("MINERU_BELLM_BASE_URL", "http://bellm-env:1/bellm/MinerU")
    monkeypatch.setenv("MINERU_BELLM_AUTH", "Basic envkey")
    monkeypatch.setattr(loader_mod, "BellmLoader", _SpyBellm)
    assert loader_mod.load(b"%PDF-1.4").tag == "BellmLoader"
    assert seen == {"base_url": "http://bellm-env:1/bellm/MinerU", "auth": "Basic envkey"}


def test_local_engine_uses_httploader(monkeypatch):
    """无 env 时默认 local：pdf+base_url 走 HttpLoader（9030 契约，桩断言不真连）。"""
    from mineru2doc import loader as loader_mod

    monkeypatch.delenv("MINERU_ENGINE", raising=False)
    monkeypatch.setattr(loader_mod, "HttpLoader",
                        lambda base_url, **kw: _SpyLoader("HttpLoader"))
    assert loader_mod.load(b"%PDF-1.4", base_url="http://x:1").tag == "HttpLoader"
