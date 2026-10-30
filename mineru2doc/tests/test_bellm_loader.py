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


def test_bytes_filename_threads_multipart_name_and_content_type():
    """zip 模式 bytes 输入透传真实文件名：multipart 文件名 + 按后缀 content-type。

    网关疑似按上传文件名后缀分发解析器——bytes 输入恒名 upload.pdf 会让对接文档
    承诺的 PNG/JPG 上传名不副实（JPEG 字节伪装成 .pdf）→ 502 风险。路径输入恒有
    真实名，不受影响；不给 filename 时回退 upload.pdf + application/pdf。
    """
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200, json={"md_content": "# T\n\n正文"})

    loader = BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                         transport=_transport(handler))
    loader.load(b"\x89PNG fake", filename="scan.png")
    loader.load(b"\xff\xd8 fake", filename="photo.jpeg")
    loader.load(b"%PDF-1.4")  # 缺省：无名 bytes 仍按 PDF 上传
    assert b'filename="scan.png"' in bodies[0]
    assert b'Content-Type: image/png' in bodies[0]
    assert b'filename="photo.jpeg"' in bodies[1]
    assert b'Content-Type: image/jpeg' in bodies[1]
    assert b'filename="upload.pdf"' in bodies[2]
    assert b'Content-Type: application/pdf' in bodies[2]


def test_images_saved_under_md_ref_relative_path(tmp_path):
    """定案（e2e 实测 2026-10-30）：md 引用 images/<hash>.jpg，键是裸 <hash>.jpg。
    落盘须按 md 引用的完整相对路径（image_out_dir/images/<hash>.jpg），使
    result.md 相对引用就地可解析——与 9030 HttpLoader 的 img_path 语义一致。"""
    def handler(request):
        return httpx.Response(200, json={
            "md_content": "# T\n\n![](images/ab12.jpg)\n\n正文",
            "images": {"ab12.jpg": f"data:image/jpeg;base64,{JPEG_B64}"},
        })

    out = tmp_path / "imgs"
    BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                transport=_transport(handler)).load(b"%PDF-1.4", image_out_dir=str(out))
    assert (out / "images" / "ab12.jpg").read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"
    assert not (out / "ab12.jpg").exists()  # 不再平铺在 image_out_dir 根


def test_return_images_flag_only_when_saving(tmp_path):
    """return_images=true 仅在要落盘图片时携带（网关缺省不回图片字节，省带宽）。"""
    bodies = []

    def handler(request):
        bodies.append(request.content.decode("latin-1"))
        return httpx.Response(200, json={"md_content": "# T\n\n正文"})

    loader = BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                         transport=_transport(handler))
    loader.load(b"%PDF-1.4")
    loader.load(b"%PDF-1.4", image_out_dir=str(tmp_path / "imgs"))
    assert 'name="return_images"' not in bodies[0]
    assert 'name="return_images"' in bodies[1]


def test_bare_base64_value_decoded(tmp_path):
    """data-url 前缀缺失（裸 base64，9030 形态）时按整值解码，不落空文件。"""
    def handler(request):
        return httpx.Response(200, json={
            "md_content": "![](images/x.jpg)",
            "images": {"x.jpg": JPEG_B64},
        })

    out = tmp_path / "imgs"
    BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                transport=_transport(handler)).load(b"%PDF-1.4", image_out_dir=str(out))
    assert (out / "images" / "x.jpg").read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"


def test_orphan_image_key_not_saved(tmp_path):
    """dict 有键但 md 无对应引用 → 不落盘（与 9030 _save_http_images 同语义）。"""
    def handler(request):
        return httpx.Response(200, json={
            "md_content": "# T\n\n正文",
            "images": {"orphan.jpg": f"data:image/jpeg;base64,{JPEG_B64}"},
        })

    out = tmp_path / "imgs"
    BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                transport=_transport(handler)).load(b"%PDF-1.4", image_out_dir=str(out))
    assert not out.exists() or not any(out.rglob("*"))


def test_traversal_ref_sanitized(tmp_path):
    """md 引用带 ../.. 时剥危险段后落 basename，不逃出 image_out_dir。"""
    def handler(request):
        return httpx.Response(200, json={
            "md_content": "![](../../evil.jpg)",
            "images": {"evil.jpg": f"data:image/jpeg;base64,{JPEG_B64}"},
        })

    out = tmp_path / "imgs"
    BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                transport=_transport(handler)).load(b"%PDF-1.4", image_out_dir=str(out))
    assert (out / "evil.jpg").read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"
    outside = tmp_path / "evil.jpg"
    assert not outside.exists()


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


def test_post_client_trust_env_false(monkeypatch):
    """真连防回归：BellmLoader 的 httpx.Client 必须显式 trust_env=False。

    Windows 本机系统代理（注册表 Clash 127.0.0.1:7890，不出现在 env）会把
    128.23.74.3 公网段内网地址劫进隧道 → ReadTimeout（MockTransport 单测测不出，
    曾真连翻车）。此处 spy 记录 loader 传给 httpx.Client 的 kwargs 钉死该约定。
    """
    real_client = httpx.Client
    recorded = {}

    def spy(*args, **kwargs):
        recorded.update(kwargs)
        kwargs.setdefault("trust_env", False)  # 桩恒安全：真 Client 不吃系统代理
        return real_client(*args, **kwargs)

    def handler(request):
        return httpx.Response(200, json={"md_content": "# 标题\n\n正文。"})

    monkeypatch.setattr(httpx, "Client", spy)
    doc = BellmLoader("http://gw:1/bellm/MinerU", auth="Basic a2V4",
                      transport=_transport(handler)).load(b"%PDF-1.4")
    assert doc.blocks
    assert recorded.get("trust_env") is False


def test_load_dispatch_bellm_by_env(monkeypatch):
    """engine 由 env 解析：MINERU_ENGINE=bellm 时走 BellmLoader（base_url/auth 取 env）。"""
    from mineru2doc import loader as loader_mod

    seen = {}

    class _SpyBellm:
        def __init__(self, base_url, *, auth, **kw):
            seen.update(base_url=base_url, auth=auth)

        def load(self, source, *, image_out_dir=None, filename=None):
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
