"""bellm 真连 e2e：需 MINERU_BELLM_AUTH 且网关可达，否则 skip（CI/离线不挂）。

fixture 为彩色噪声插图（200x150 随机像素）：纯色小矩形会被 MinerU 版面检测当
背景丢弃（60x60 灰块实测 md 无任何图片引用），噪声图两轮实测均被检出。
"""
import os
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "bellm_e2e_fig.pdf"
AUTH = os.environ.get("MINERU_BELLM_AUTH", "")

pytestmark = pytest.mark.skipif(
    not (AUTH and FIXTURE.exists()), reason="需要 MINERU_BELLM_AUTH 与 fixture")


def test_bellm_figure_pdf_end_to_end(tmp_path):
    from mineru2doc import convert

    imgs = tmp_path / "imgs"
    md = convert(FIXTURE.read_bytes(), base_url=None, engine="bellm",
                 image_dir=str(imgs))
    assert md.strip()
    # 引用格式定案（2026-10-30 实测）：md 引用 images/<hash>.jpg；
    # 落盘按引用完整相对路径，result.md 的相对引用就地可解析
    import re
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md)
    saved = sorted(str(p.relative_to(imgs)).replace("\\", "/")
                   for p in imgs.rglob("*") if p.is_file()) if imgs.exists() else []
    print(f"[e2e] refs={refs} saved={saved}")  # 结论进任务报告
    assert refs, "fixture 应产生 md 图片引用（噪声图被版面检测丢弃时此断言失败）"
    assert all(r.startswith("images/") for r in refs), refs
    assert saved == sorted(refs)  # 每个引用在 image_dir 下可解析到落盘文件
