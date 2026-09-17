"""pdf_extract_worker —— PDF 提取子进程 worker(issues7 S-2 毒 PDF 完整防线)。

用法(父进程调用):
    python -m document2chunk.pipeline.pdf_extract_worker <pdf_path> <image_dir> <out_json> [skip_detect]

子进程跑 PdfExtractor.extract → ExtractionResult.model_dump_json 写 out_json。
父进程 model_validate_json 回载。楔死(如 killer.pdf 在 C 层死循环)发生在子进程,
父进程 subprocess 超时 SIGKILL 后照常报错——服务存活。

测试钩子:env DOCUMENT2CHUNK_EXTRACT_TEST_SLEEP 秒级延迟(模拟慢/挂死提取)。
"""
from __future__ import annotations


def main(argv) -> int:
    import json
    import os
    import sys
    import time
    from pathlib import Path

    if len(argv) not in (4, 5):
        print("usage: python -m document2chunk.pipeline.pdf_extract_worker "
              "<pdf_path> <image_dir> <out_json> [skip_detect]", file=sys.stderr)
        return 2

    pdf_path, image_dir, out_json = argv[1], argv[2], argv[3]
    skip_detect = len(argv) == 5 and argv[4] == "skip_detect"

    sleep_s = os.environ.get("DOCUMENT2CHUNK_EXTRACT_TEST_SLEEP", "")
    if sleep_s:
        time.sleep(float(sleep_s))  # 测试钩子

    try:
        from document2chunk.extractors.pdf import PdfExtractor

        Path(image_dir).mkdir(parents=True, exist_ok=True)
        result = PdfExtractor(
            image_dir=image_dir, skip_detect=skip_detect,
        ).extract(str(pdf_path))
        Path(out_json).write_text(result.model_dump_json(), encoding="utf-8")
        print(json.dumps({"ok": True}))
        return 0
    except Exception as e:  # noqa: BLE001 —— 子进程内任何失败走 stderr + rc=1
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(main(_sys.argv))


def extract_pdf_guarded(
    source, image_dir, timeout_s: float, *, skip_detect: bool = True
):
    """子进程执行 PDF 提取，超时终止并抛 InvalidSourceError(issues7 S-2)。

    Args:
        source: PDF 路径或 bytes（bytes 时写临时文件供子进程读取）。
        image_dir: 图片落盘目录（子进程直接写入，父进程消费）。
        timeout_s: 子进程上限；超时 kill。生产默认 600s（env DOCUMENT2CHUNK_EXTRACT_TIMEOUT，0=关）。
        skip_detect: 直传 worker（serve 路由层已用同一判定器确认过，提取内免二次检测）。

    与 detect 看门狗同骨架:楔死在子进程,主服务 subprocess 超时后照常报错。
    """
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from document2chunk.exceptions import InvalidSourceError
    from document2chunk.ir import ExtractionResult

    tmp_pdf = None
    tmp_json = None
    try:
        if isinstance(source, (bytes, bytearray)):
            tmp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp_pdf.write(bytes(source))
            tmp_pdf.close()
            pdf_path = tmp_pdf.name
        else:
            pdf_path = str(source)
        fd, out_json = tempfile.mkstemp(prefix="d2c_ext_", suffix=".json")
        os.close(fd)
        tmp_json = out_json
        cmd = [
            sys.executable, "-m", "document2chunk.pipeline.pdf_extract_worker",
            pdf_path, str(image_dir), out_json,
        ]
        if skip_detect:
            cmd.append("skip_detect")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            raise InvalidSourceError(
                f"PDF 提取超过 {timeout_s:g}s 未完成——疑似病态 PDF（子进程已终止）。"
                "请单独核查该文件；其余文件不受影响"
            ) from e
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip()[-300:]
            raise InvalidSourceError(f"PDF 提取失败（子进程 rc={proc.returncode}）：{tail}")
        return ExtractionResult.model_validate_json(Path(out_json).read_text(encoding="utf-8"))
    finally:
        for p in (tmp_pdf.name if tmp_pdf else None, tmp_json):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
