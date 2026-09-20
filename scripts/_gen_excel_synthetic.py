"""生成 Excel 轨A 的 synthetic 测试样本（机器生成，与人工真实语料分离）。

用法：uv run python scripts/_gen_excel_synthetic.py <输出目录>
产出清单（全部为格式陷阱类，规整数据表已由人工语料覆盖）：
  multi_header.xlsx     三级表头 + 跨行合并（难点1/3）
  totals.xlsx           表尾关键词合计行 + 校验和型合计（难点13）
  percent_currency.xlsx 百分比/货币/日期显示值规范化（Q6/Q14）
  hidden_sheet.xlsx     含隐藏 sheet + 隐藏列（难点6）
  empty.xlsx            单空 sheet（N1）
  broken.xlsx           PK 头 + 垃圾字节（毒输入兜底）
  ~$a.xlsx              Excel 锁文件形状（N2）
  csv_utf8.csv / csv_gbk_semicolon.csv / csv_empty.csv / csv_no_header.csv（N4）
生成日期与机器信息写入本文件所在提交，样本不唯一、可随时重新生成。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font


def _wb_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 三级表头 + 合并
    wb = Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(["项目基础信息", None, None, "经费", None])
    ws.append(["基本信息", None, None, "预算（万元）", None])
    ws.append(["序号", "项目名称", "负责人", "总额", "已拨"])
    ws.append([1, "示范项目A", "张三", 100.5, 50])
    ws.append([2, "示范项目B", "李四", 200, 120])
    ws.merge_cells("A1:C1")
    ws.merge_cells("A2:C2")
    ws.merge_cells("D1:E1")
    for c in "ABCDE":
        ws[f"{c}3"].font = Font(bold=True)
    Path(out / "multi_header.xlsx").write_bytes(_wb_bytes(wb))

    # 2. 合计行（关键词 + 校验和）
    wb = Workbook()
    ws = wb.active
    ws.title = "经费"
    ws.append(["科目", "金额（元）"])
    ws.append(["人员费", 1000])
    ws.append(["设备费", 2500])
    ws.append(["材料费", 400.25])
    ws.append([None, 3900.25])          # 校验和型（无标签）
    ws.append(["合计", 3900.25])        # 关键词型
    Path(out / "totals.xlsx").write_bytes(_wb_bytes(wb))

    # 3. 百分比 / 货币 / 日期
    wb = Workbook()
    ws = wb.active
    ws.title = "口径"
    ws.append(["部门", "增长率", "预算", "统计日"])
    ws.append(["华东", 0.126, 1024.5, "2024-03-01"])
    ws["B2"].number_format = "0.00%"
    ws["C2"].number_format = '"¥"#,##0.00'
    ws["D2"].number_format = "yyyy-mm-dd"
    import datetime as _dt
    ws["D2"] = _dt.datetime(2024, 3, 1)
    Path(out / "percent_currency.xlsx").write_bytes(_wb_bytes(wb))

    # 4. 隐藏 sheet + 隐藏列
    wb = Workbook()
    ws = wb.active
    ws.title = "可见"
    ws.append(["名称", "内部标记", "数量"])
    ws.append(["甲", "X1", 1])
    ws.column_dimensions["B"].hidden = True
    ws2 = wb.create_sheet("暗账")
    ws2.append(["秘密", 42])
    ws2.sheet_state = "hidden"
    Path(out / "hidden_sheet.xlsx").write_bytes(_wb_bytes(wb))

    # 5. 空 sheet
    wb = Workbook()
    wb.active.title = "空表"
    Path(out / "empty.xlsx").write_bytes(_wb_bytes(wb))

    # 6/7. 毒输入与锁文件
    Path(out / "broken.xlsx").write_bytes(b"PK\x03\x04 this is not a zip")
    Path(out / "~$a.xlsx").write_bytes(b"\x20\x00Excel lock placeholder ~ 165 bytes total size")

    # 8. csv 家族
    Path(out / "csv_utf8.csv").write_bytes("名称,数量\n甲,1\n乙,2\n".encode("utf-8"))
    Path(out / "csv_gbk_semicolon.csv").write_bytes("名称;数量\n甲;3\n".encode("gbk"))
    Path(out / "csv_empty.csv").write_bytes(b"")
    Path(out / "csv_no_header.csv").write_bytes("甲,1\n乙,2\n".encode("utf-8"))

    print(f"synthetic samples -> {out.resolve()}")
    for p in sorted(out.iterdir()):
        print(f"  {p.name}  {p.stat().st_size}B")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_synthetic_excel")
