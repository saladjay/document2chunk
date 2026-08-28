"""text_titles（.md/.txt 缺失标题复原）测试。

两层覆盖：
- 单元：合成 snippet 逐规则断言（粘连拆分/列表项拒绝/目录区/frontmatter/围栏/
  两行拼接大标题/幂等/无损不变量/CRLF）。
- 集成：tests/fixtures/textmd 下的真实样例（来源
  download_chunk_from_coreagent/data_files，8 件代表 8 类形态），断言
  关键标题、无损、幂等。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from document2chunk.text_titles import _strip_markup, restore_titles

FIXTURES = Path(__file__).parent / "fixtures" / "textmd"

# 无损断言：容许删除＝拆分点句号 + 标题行尾列表标点（；，、：等）
_DROP_PUNCT = str.maketrans("", "", "；;，,、：:")


def _lossless(src: str, res: str) -> bool:
    a = _strip_markup(src)
    b = _strip_markup(res)
    return a == b or (
        a.replace("。", "").translate(_DROP_PUNCT) == b.replace("。", "").translate(_DROP_PUNCT)
    )


def _headings(text: str, level: int) -> list[str]:
    pat = re.compile(rf"^{'#' * level}\s+(.+)$", re.MULTILINE)
    return pat.findall(text)


# ==================== 单元：逐规则 ====================


def test_bold_merged_split():
    """粗体标题+正文粘连：`**一、xxx。**正文…` → ## + 正文段。"""
    src = "**一、充分认识意义。**高校毕业生是生力军，科研助理是重要组成。\n"
    res = restore_titles(src)
    assert "## 一、充分认识意义" in res
    assert "高校毕业生是生力军" in res
    assert _lossless(src, res)
    assert restore_titles(res) == res


def test_bold_inner_body_mixed():
    """粗体内含标题句+正文句（`**三、标题。粗体内正文。**尾随正文`）全部保住。"""
    src = "**三、主动作为开发岗位。科研助理是指从事辅助工作的人员。**项目承担单位应创新机制，采取多种方式选聘。\n"
    res = restore_titles(src)
    assert "## 三、主动作为开发岗位" in res
    assert "科研助理是指从事辅助工作的人员。项目承担单位应创新机制" in res
    assert _lossless(src, res)


def test_plain_minor_split():
    """无粗体粘连：`（一）指导思想。正文…` → ### + 正文。"""
    src = "（一）指导思想。以习近平新时代中国特色社会主义思想为指导，全面贯彻党的十九大精神，坚持新发展理念。\n"
    res = restore_titles(src)
    assert "### （一）指导思想" in res
    assert "以习近平新时代中国特色社会主义思想为指导" in res


def test_enumeration_item_not_heading():
    """（一）开头但无句号的列表项（第X条下的枚举）不得提升。"""
    src = "　　（一）采取造假、串通、重复申报等不正当手段获得科研活动承担、管理、咨询、服务等资格以及技术检测、验收结题等认证的；\n"
    res = restore_titles(src)
    assert "###" not in res
    assert _lossless(src, res)


def test_standalone_short_line_promoted():
    """OCR txt 块中间的独立短样式行（非块首）也要提升。"""
    src = "交通运输领域深入贯彻落实重要指示批示精神，不断完善\n一、发展现状与形势\n“十三五”以来，取得了一批国际领先的成果。\n"
    res = restore_titles(src)
    assert "## 一、发展现状与形势" in res


def test_article_runon_split():
    """第X条直连正文（规章惯例）：标题取编号、正文拆出，短引语也拆。"""
    src = "　　第一条为完善科技创新治理体系，加强科研诚信建设，营造良好生态，根据有关规定制定本办法。\n\n　　第十条下列行为属于科研失信行为：\n"
    res = restore_titles(src)
    assert "#### 第一条" in res
    assert "为完善科技创新治理体系" in res
    assert "#### 第十条" in res
    assert "下列行为属于科研失信行为：" in res
    assert _lossless(src, res)


def test_article_space_short_title():
    """`第三十条 附则`（空格+短词）整行作标题。"""
    src = "**第三十条 附则**\n本办法自发布之日起施行。\n"
    res = restore_titles(src)
    assert "## 第三十条 附则" in res


def test_article_space_full_sentence_split():
    """`第四十四条 本办法自…施行。`（空格+完整句）→ 编号+正文。"""
    src = "第四十四条 本办法自2017年5月1日起施行，有效期5年，由交通运输部科技主管部门负责解释。\n"
    res = restore_titles(src)
    assert "#### 第四十四条" in res
    assert "本办法自2017年5月1日起施行" in res


def test_chapter_bold():
    src = "**第一章 总则**\n"
    res = restore_titles(src)
    assert "## 第一章 总则" in res


def test_doc_title_bold():
    src = "**科技部 教育部关于做好某项工作的通知**\n\n各省、自治区、直辖市：\n"
    res = restore_titles(src)
    assert "# 科技部 教育部关于做好某项工作的通知" in res


def test_doc_title_bold_with_tail():
    """`**标题**文号`：标题提升 #，文号尾随单独成行。"""
    src = "**国务院办公厅关于印发促进科技成果转移转化行动方案的通知**国办发〔2016〕28号\n\n各省、自治区、直辖市人民政府：\n"
    res = restore_titles(src)
    assert "# 国务院办公厅关于印发促进科技成果转移转化行动方案的通知" in res
    assert "国办发〔2016〕28号" in res
    assert "**" not in res


def test_doc_title_two_line_merge():
    """机构行 + 关于行两行拼接成一个大标题。"""
    src = "中共中央 国务院  \n关于构建更加完善的要素市场化配置体制机制的意见  \n（2020年3月30日）\n"
    res = restore_titles(src)
    assert "# 中共中央 国务院关于构建更加完善的要素市场化配置体制机制的意见" in res
    assert _lossless(src, res)


def test_doc_title_ocr_wrapped_merge():
    """OCR 断行：`…关于印发` + `《…》的通知` 拼接。"""
    src = "<图片内容|start>\n\n<img src=\"https://example.com/a.jpeg\"/>\n交通运输部科学技术部关于印发\n《“十四五”交通领域科技创新规划》的通知\n各省、自治区、直辖市交通运输厅：\n"
    res = restore_titles(src)
    assert "# 交通运输部科学技术部关于印发《“十四五”交通领域科技创新规划》的通知" in res


def test_toc_run_not_promoted():
    """连续 ≥3 个短样式行 = 目录区，全部不提升（含无页码后缀的）。"""
    src = "誉目\n一、发展现状与形势\n二、发展思路与目标|6\n(一)指导思想|6\n(二)基本原则\n（三）发展目标\n三、重点研发任务|9\n"
    res = restore_titles(src)
    assert "##" not in res
    assert "###" not in res


def test_toc_dot_leader_ascii():
    """ASCII 句点引导符（….22）的目录行不提升。"""
    src = "第四节 建设一流科研机构和研究型高校............................ 22\n"
    res = restore_titles(src)
    assert "###" not in res


def test_inline_enum_not_whole_heading():
    """含 ≥2 个内联数字枚举的行不作整行标题。"""
    src = "（五）加强科技创新能力建设 1.加强科技人才队伍建设 2.优化科研平台布局 3.提升科技服务能力\n"
    res = restore_titles(src)
    assert "### （五）" not in res


def test_existing_heading_untouched():
    """已有 # 标题（含内部粗体）原样保留、不补大标题；下方样式行仍正常提升。"""
    src = "# **既有标题**\n\n正文段落。\n\n一、样式行\n"
    res = restore_titles(src)
    assert res.startswith("# **既有标题**")  # 原行不动（含粗体）
    assert _headings(res, 1) == ["**既有标题**"]  # 无重复大标题
    assert "## 一、样式行" in res  # 混排文件下方样式仍提升
    src2 = "# 既有标题\n\n正文段落。\n"
    assert restore_titles(src2) == src2


def test_frontmatter_and_fence_protected():
    """YAML frontmatter 与代码围栏内部不做复原。"""
    src = (
        "---\n"
        'title: "测试文档"\n'
        "reference_no: 1\n"
        "---\n"
        "# 已有标题\n\n"
        "```markdown\n"
        "一、围栏内的伪标题\n"
        "```\n"
    )
    res = restore_titles(src)
    assert res == src


def test_no_style_passthrough_identical():
    plain = "关于印发集团2026年度人工智能应用与低空经济指导意见已经印发，请遵照执行。\n\n各部门要抓好落实。\n"
    assert restore_titles(plain) == plain


def test_json_first_line_not_title():
    src = '{"发文年份":"2015年","级别":"国家","政策名称":"某实施方案"}\n\n{"发文年份":"2016年"}\n'
    assert restore_titles(src) == src


def test_idempotent_and_crlf():
    src = "**一、总体要求**\r\n\r\n（一）指导思想。以习近平新时代中国特色社会主义思想为指导，坚持稳中求进工作总基调。\r\n"
    res = restore_titles(src)
    assert "\r\n" in res
    assert "## 一、总体要求" in res
    assert "### （一）指导思想" in res
    assert restore_titles(res) == res


def test_nested_heading_line_single_pass():
    """一行内嵌两级标题（`**二、目标**　（一）xxx。正文`）单遍拆完（幂等）。"""
    src = "**二、指导思想和总体目标**　　（一）指导思想。深入贯彻党的十八大精神，发挥市场在资源配置中的决定性作用。\n"
    res = restore_titles(src)
    assert "## 二、指导思想和总体目标" in res
    assert "### （一）指导思想" in res
    assert restore_titles(res) == res


def test_empty_and_blank():
    assert restore_titles("") == ""
    assert restore_titles("\n\n") == "\n\n"


# ==================== 集成：真实样例（8 类形态） ====================

_CASES = {
    # (文件名, H1 断言, 关键标题断言列表)
    "科技部等六部门发布鼓励科研项目开发科研助理岗位吸纳高校毕业生就业的通知（国科发资〔2020〕132号）.md": (
        "科技部 教育部 人力资源社会保障部 财政部 中科院 自然科学基金委关于鼓励科研项目开发科研助理岗位吸纳高校毕业生就业的通知",
        ["## 一、充分认识开发科研助理岗位吸纳高校毕业生就业的重要意义",
         "## 二、依托各类国家科技计划（专项、基金等）项目拓宽大学生就业渠道",
         "## 三、主动作为积极开发科研助理岗位",
         "## 七、做好开发科研助理岗位吸纳高校毕业生就业的组织、协调和推动工作"],
    ),
    "中共中央、国务院印发《交通强国建设纲要》.md": (
        "中共中央 国务院印发《交通强国建设纲要》",
        ["## 一、总体要求", "### （一）指导思想", "### （二）发展目标", "## 二、基础设施布局完善、立体互联"],
    ),
    "三部委关于印发“十四五”原材料工业发展规划的通知.md": (
        "三部委关于印发“十四五”原材料工业发展规划的通知",
        [],  # 短通知无层级标题
    ),
    "中共中央 国务院关于构建更加完善的要素市场化配置体制机制的意见.md": (
        "中共中央 国务院关于构建更加完善的要素市场化配置体制机制的意见",
        ["## 一、总体要求", "### （一）指导思想", "### （二）基本原则", "## 二、推进土地要素市场化配置",
         "### （三）建立健全城乡统一的建设用地市场"],
    ),
    "《广东省科研诚信管理办法》（试行）.md": (
        "广东省科研诚信管理办法（试行）",
        ["## 第一章 总则", "#### 第一条", "## 第二章 管理职责", "## 第三章 信用评价",
         "#### 第十条"],
    ),
    "2022-07-16_国务院办公厅关于完善科技成果评价机制的指导意见_1569908688519565312.md": (
        None,  # 已有 # 标题：不补大标题
        ["# 国务院办公厅关于完善科技成果评价机制的指导意见"],
    ),
    "“十四五”交通领域科技创新规划.txt": (
        "交通运输部科学技术部关于印发《“十四五”交通领域科技创新规划》的通知",
        ["## 一、发展现状与形势", "## 二、发展思路与目标", "### （一）指导思想。", "## 三、重点研发任务"],
    ),
    "1.科研管理制度_index.txt": (
        None,  # 完全已有 # 的 index：应原样
        ["# 科研管理制度", "## 简介", "#### 📄 Word 文档 (.docx)"],
    ),
}


@pytest.mark.parametrize("name,h1,heads", [
    (k, v[0], v[1]) for k, v in _CASES.items()
])
def test_fixture(name: str, h1: str | None, heads: list[str]):
    src = (FIXTURES / name).read_text(encoding="utf-8")
    res = restore_titles(src)
    h1_list = _headings(res, 1)
    if h1 is None:
        pass  # 不强断言 H1
    else:
        assert h1 in h1_list, f"H1 缺失：{h1_list}"
    for h in heads:
        assert h in res.splitlines() or h in _headings(res, h.count("#") if h.startswith("#") else 2), f"缺标题：{h}"
    assert _lossless(src, res), "正文无损被破坏"
    assert restore_titles(res) == res, "不幂等"


def test_fixture_index_unchanged():
    """完全已有 # 的 index 文件应逐字节原样（无损保守性）。"""
    name = "1.科研管理制度_index.txt"
    src = (FIXTURES / name).read_text(encoding="utf-8")
    assert restore_titles(src) == src
