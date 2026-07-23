from mineru2doc.model import TEXT, Block
from mineru2doc.title_judge import RegexJudge


def _t(text, level=None):
    return Block(type=TEXT, text=text, level=level)


# ── spec §5：编号标题只标 number_depth（相对），不绝对覆盖层级 ──

def test_numbered_heading_marks_depth_keeps_mineru_level():
    # 「第三章 总则」MinerU level=2 → number_depth=1，level 保留（交 normalize）
    b = RegexJudge().remediate([_t("第三章 总则", level=2)])[0]
    assert b.number_depth == 1
    assert b.level == 2           # MinerU 层级保留，不被绝对覆盖
    assert b.is_heading

    # 「（二）适用范围」MinerU level=1 → number_depth=2
    b = RegexJudge().remediate([_t("（二）适用范围", level=1)])[0]
    assert b.number_depth == 2


def test_unnumbered_heading_unchanged():
    # 「总体要求」MinerU level=1，无编号 → 不动
    b = RegexJudge().remediate([_t("总体要求", level=1)])[0]
    assert b.level == 1
    assert b.number_depth is None


def test_promote_missed_numbered_title():
    # 正文「3.2.1 项目查看与处理」→ 提升：number_depth=3 + 标题
    b = RegexJudge().remediate([_t("3.2.1 项目查看与处理")])[0]
    assert b.number_depth == 3
    assert b.is_heading


def test_no_promote_long_numbered_paragraph():
    long_text = ("3.2.1 本模块提供了完整的文档查看与处理能力并且支持多种输入格式"
                 "同时具备完善的错误处理机制以及详细的日志记录功能以满足企业级使用需求")
    b = RegexJudge().remediate([_t(long_text)])[0]
    assert b.level is None and b.number_depth is None


def test_no_promote_body_after_punct():
    b = RegexJudge().remediate([_t("3.2.1 总则。本模块说明如下内容。")])[0]
    assert b.level is None and b.number_depth is None


def test_body_without_number_stays_body():
    b = RegexJudge().remediate([_t("这是一般正文")])[0]
    assert b.level is None


# ── 降误检（默认关）──

_DEMOTE_LONG = ("这是一段明显超过六十个字符长度的正文段落内容它实际上并不是一个标题"
                "而是一段完整的句子因此按照规则应当被降级处理为普通正文而不再保留标题层级。")


def test_demote_default_off():
    b = RegexJudge(demote=False).remediate([_t(_DEMOTE_LONG, level=1)])[0]
    assert b.level == 1


def test_demote_on():
    b = RegexJudge(demote=True).remediate([_t(_DEMOTE_LONG, level=1)])[0]
    assert b.level is None


def test_demote_skips_numbered():
    # 有编号的长标题即使 demote 也不降（走 ① 标 depth）
    b = RegexJudge(demote=True).remediate([_t("第一章 关于某事项的总体安排与部署说明", level=2)])[0]
    assert b.number_depth == 1
    assert b.is_heading
