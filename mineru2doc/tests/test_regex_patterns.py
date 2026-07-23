from mineru2doc.regex_patterns import (
    extract_section_number,
    has_body_after_punct,
    section_number_depth,
    split_number_and_title,
)


def test_split_number_and_title():
    assert split_number_and_title("3.2.1 项目查看与处理") == ("3.2.1", "项目查看与处理")
    assert split_number_and_title("第一章 集团立项") == ("第一章", "集团立项")
    assert split_number_and_title("（二）发展基础") == ("（二）", "发展基础")
    assert split_number_and_title("这是正文") == (None, "这是正文")


def test_extract_section_number():
    assert extract_section_number("1.1 概述") == "1.1"
    assert extract_section_number("正文无编号") is None


def test_section_number_depth():
    assert section_number_depth("1") == 1
    assert section_number_depth("1.1") == 2
    assert section_number_depth("3.2.1") == 3
    assert section_number_depth("第一章") == 1
    assert section_number_depth("第二节") == 2
    assert section_number_depth("第三条") == 2
    assert section_number_depth("第一篇") == 1
    assert section_number_depth("一、") == 1
    assert section_number_depth("（一）") == 2
    assert section_number_depth("1、") == 1
    assert section_number_depth("（1）") == 2
    assert section_number_depth("") == 0


def test_has_body_after_punct():
    assert has_body_after_punct("3.2.1 总则。本模块说明如下。") is True
    assert has_body_after_punct("3.2.1 项目查看与处理") is False
