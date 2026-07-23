from mineru2doc.model import TEXT, Block
from mineru2doc.normalize import normalize_levels


def _h(level=None, number_depth=None, text="h"):
    # 编号标题：level 是占位（非 None），normalize 据 number_depth 相对重定
    if number_depth is not None and level is None:
        level = number_depth
    return Block(type=TEXT, text=text, level=level, number_depth=number_depth)


def test_first_heading_becomes_one():
    assert normalize_levels([_h(number_depth=1)])[0].level == 1
    assert normalize_levels([_h(level=3)])[0].level == 1


def test_numbered_nests_under_unnumbered_title():
    # 标题(H1) 之下的 一、(d1) → H2
    out = normalize_levels([_h(level=1, text="标题"), _h(number_depth=1, text="一、")])
    assert [b.level for b in out] == [1, 2]


def test_relative_depth_stack():
    # 一、(d1) → （一）(d2) → 二、(d1)
    out = normalize_levels([_h(number_depth=1), _h(number_depth=2), _h(number_depth=1)])
    assert [b.level for b in out] == [1, 2, 1]


def test_three_levels_nest():
    out = normalize_levels([_h(number_depth=1), _h(number_depth=2), _h(number_depth=3)])
    assert [b.level for b in out] == [1, 2, 3]


def test_reset_on_new_top_section():
    # 标题H1 → 一、(d1, H2) → 新标题回到H1(重置) → 一、(d1, H2)
    out = normalize_levels([
        _h(level=1, text="A"),
        _h(number_depth=1, text="一、"),
        _h(level=1, text="B"),
        _h(number_depth=1, text="一、"),
    ])
    assert [b.level for b in out] == [1, 2, 1, 2]


def test_unnumbered_jump_clamp():
    out = normalize_levels([_h(level=1), _h(level=3)])
    assert [b.level for b in out] == [1, 2]
