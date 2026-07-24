"""mineru2doc —— MinerU PDF 分析结果 → 多级标题 Markdown 文档（简化版）。

独立原型，不复用 src/document2chunk 架构。数据流：
loader → title_judge(正则补救) → normalize(栈式定级) → render(markdown)。
"""

from __future__ import annotations

from .model import Block, MinerUDoc

__version__ = "0.1.0"

__all__ = ["Block", "MinerUDoc", "convert", "convert_to_dir", "__version__"]


def convert(
    source,
    *,
    base_url: str | None = None,
    demote: bool = False,
    output: str | None = None,
    image_dir: str | None = None,
) -> str:
    """端到端：MinerU 结果 → 多级标题 Markdown 字符串。

    Args:
        source: 输入。目录/``.json``/``.md`` → FileLoader；``.pdf`` + base_url → HttpLoader。
        base_url: MinerU 服务地址（如 ``http://128.23.67.112:9030``）。给 .pdf 时必填。
        demote: 开启"降误检"（MinerU 误判的长句标题 → 正文），默认关。
        output: 写入文件路径；``-`` = stdout（返回值仍是字符串）；None = 只返回字符串。
        image_dir: 图片落盘目录（保留 md 相对引用 ``images/<hash>.jpg``）。None 时：若
            ``output`` 是文件则取其所在目录，否则不落盘。
    """
    import os

    from .loader import load
    from .render import to_markdown
    from .title_judge import RegexJudge
    from .normalize import normalize_levels

    image_out_dir = image_dir
    if image_out_dir is None and output and output != "-":
        image_out_dir = os.path.dirname(os.path.abspath(output)) or "."

    doc = load(source, base_url=base_url, image_out_dir=image_out_dir)
    blocks = RegexJudge(demote=demote).remediate(doc.blocks)
    blocks = normalize_levels(blocks)
    md = to_markdown(blocks)

    if output:
        if output == "-":
            import sys
            sys.stdout.write(md)
        else:
            with open(output, "w", encoding="utf-8") as f:
                f.write(md)
    return md


def convert_to_dir(
    input_file,
    output_dir,
    image_dir: str | None = None,
    *,
    base_url: str | None = None,
    demote: bool = False,
) -> str:
    """读 ``input_file`` → 写 ``{output_dir}/result.md`` + 图片到 ``image_dir``。

    图片默认落 ``output_dir/images``；``result.md`` 用相对路径 ``images/<name>`` 引用，
    故 ``image_dir`` 应为 ``output_dir/images``（契约默认）。HTTP 路径模式 / CLI 模式共用。
    返回 ``result.md`` 的绝对路径。
    """
    import os
    import shutil
    import tempfile

    os.makedirs(output_dir, exist_ok=True)
    if not image_dir:
        image_dir = os.path.join(output_dir, "images")

    with tempfile.TemporaryDirectory() as tmp:
        md = convert(input_file, base_url=base_url, demote=demote, image_dir=tmp)
        result_path = os.path.join(output_dir, "result.md")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(md)
        src_imgs = os.path.join(tmp, "images")
        if os.path.isdir(src_imgs):
            os.makedirs(image_dir, exist_ok=True)
            for fn in os.listdir(src_imgs):
                shutil.copy2(os.path.join(src_imgs, fn), os.path.join(image_dir, fn))
    return os.path.join(output_dir, "result.md")
