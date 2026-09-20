fatal: detected dubious ownership in repository at '/home/user/document2chunk'
To add an exception for this directory, call:

	git config --global --add safe.directory /home/user/document2chunk
git_rev: 
health: {"status":"ok","version":"0.1.0"}

| 目录 | 文件 | HTTP | rows | blocks | warns | 关键内容 | 耗时ms |
|---|---|---|---|---|---|---|---|
| real | 20- 软弱地层挤扩锚固关键技术研究-其他.xlsx | 200 | 81 | 4 | 0 | 科技项目执行情况表-序号;科技项目执行情况表-单位名称;科技项目执行情况表-项目名称;科技项目执行情况表-项目负责人… ‖ 科技项目执行情况表-序号: 1(填写示例）; 科技项目执行情况表-单位名… ‖ blocks=4(doc) | 2639 |
| real | 2.1-2022年下半年集团审批类项目初步评审_202208251638（调序后）.xlsx | 200 | 163 | 0 | 0 | 评审;2022年下半年集团审批类项目初步评审-序号;2022年下半年集团审批类项目初步评审-课题名称;2022年下半年集团审批类项目初步评审-承担单位… ‖ 评审: 何志军; 2022年下半年集团审批类项目初步评审-序号: 18;… | 3386 |
| real | 7-广东省高速公路改扩建关键技术研究-R&D经费决算表及支出说明（必选）.xlsx | 200 | 49 | 0 | 0 | 科目;KT1（万元）;KT2（万元）;KT3（万元）… ‖ 科目: 1.人员费; KT1（万元）: 142.5; KT2（万元）: … | 128 |
| real | 项目自定义导出数据 (3) -项目清单2026.3.3.xlsx | 200 | 935 | 0 | 2 | 项目基础信息-基本信息-序号;立项年份;项目编号;项目名称… ‖ 项目基础信息-基本信息-序号: 1; 立项年份: 2008; 项目编号:… | 9895 |
| real | └ warnings | | | | 2 | 文件含外部工作簿链接，相关单元格为保存时的缓存值; 51 个公式单元格无缓存值（文件可能由程序生成），读为空值 | 9895 |
| real | 项目自定义导出数据 (基础)-最新.xlsx | 200 | 230 | 0 | 0 | 项目基础信息-基本信息-序号;项目基础信息-基本信息-立项年份;项目基础信息-基本信息-立项批次;项目基础信息-基本信息-项目编号… ‖ 项目基础信息-基本信息-序号: 1; 项目基础信息-基本信息-立项年份:… | 714 |
| synthetic | ~$a.xlsx | 400 | - | - | - | 不支持的内容类型 FileKind.UNKNOWN（ext=.xlsx）：~$a.xlsx | 1013 |
| synthetic | broken.xlsx | 400 | - | - | - | 不支持的内容类型 FileKind.UNKNOWN（ext=.xlsx）：broken.xlsx | 145 |
| synthetic | csv_empty.csv | 422 | - | - | - | csv_empty.csv: 空文件 | 51 |
| synthetic | csv_gbk_semicolon.csv | 200 | 1 | 0 | 0 | 名称;数量 ‖ 名称: 甲; 数量: 3 | 151 |
| synthetic | csv_no_header.csv | 200 | 1 | 0 | 0 | 甲;1 ‖ 甲: 乙; 1: 2 | 47 |
| synthetic | csv_utf8.csv | 200 | 2 | 0 | 0 | 名称;数量 ‖ 名称: 甲; 数量: 1 | 41 |
| synthetic | empty.xlsx | 200 | 0 | 0 | 1 |  ‖ - | 448 |
| synthetic | └ warnings | | | | 1 | sheet「空表」为空 | 448 |
| synthetic | fake_xlsx_ole2.xlsx | 200 | 5 | 0 | 0 | 科目;金额（元） ‖ 科目: 人员费; 金额（元）: 1000 | 1993 |
| synthetic | hidden_sheet.xlsx | 200 | 1 | 0 | 1 | 名称;内部标记;数量 ‖ 名称: 甲; 内部标记: X1; 数量: 1 | 51 |
| synthetic | └ warnings | | | | 1 | 隐藏 sheet「暗账」已跳过 | 51 |
| synthetic | multi_header.xlsx | 200 | 2 | 0 | 0 | 项目基础信息-基本信息-序号;项目基础信息-基本信息-项目名称;项目基础信息-基本信息-负责人;经费-预算（万元）-总额… ‖ 项目基础信息-基本信息-序号: 1; 项目基础信息-基本信息-项目名称:… | 51 |
| synthetic | percent_currency.xlsx | 200 | 1 | 0 | 0 | 部门;增长率;预算;统计日 ‖ 部门: 华东; 增长率: 12.6%; 预算: ¥1024.50; 统计… | 37 |
| synthetic | totals.xlsx | 200 | 5 | 0 | 0 | 科目;金额（元） ‖ 科目: 人员费; 金额（元）: 1000 | 47 |

chai /parse-pdf 对 xlsx：HTTP 400，detail=检测到表格格式（xlsx）：20- 软弱地层挤扩锚固关键技术研究-其他.xlsx——请改用 /parse-excel 接口解析表格文件
