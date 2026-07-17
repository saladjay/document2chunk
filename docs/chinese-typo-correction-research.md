# 中文错别字纠正方案调研

> 日期：2026-07-17
> 动机：document2chunk 的 OCR 链路（远程 PaddleOCR 服务）产出 markdown/文本后，识别结果存在**形近字/音近字错别字**。调研在 IR/导出前加一个**可选的中文错别字纠正后处理**的可行方案（从最轻量到大模型，逐档评估）。
> 结论先行：OCR 场景错误以**形近字**为主，建议默认档用 **pycorrector（MacBERT）**，高质量档可选 **通用 LLM few-shot**，兜底用小型形近字混淆表。

---

## 1. 背景：OCR 错字的特点

| 错误来源 | 主要类型 | 举例 |
|---|---|---|
| **OCR 识别** | **形近字**（字形相似） | `账→帐`、`己→已→巳`、`未→末`、`土→士` |
| 拼音输入法 / ASR | 音近 / 同音字 | `配→陪`、`做→作` |
| 手写 / 拍照 | 形近 + 噪声 | 笔画粘连、断笔 |

- OCR 纠错应做成**与识别引擎解耦的后处理模块**（不动识别引擎，只在文本层修字），契合 document2chunk 的 pipeline 架构。
- 可借用的额外信号：**字符识别置信度**（PaddleOCR 返回里有 `score`，低置信字优先纠）。
- 经典流程：**错误检测 → 候选召回（形近/音近混淆集）→ 候选排序（语言模型/语义）**。

---

## 2. 方案分层（从轻到重）

### 2.1 规则 + 混淆集（最轻，毫秒级）

- 自建「形近字 / 音近字」混淆表 + `pypinyin` 取拼音 + 困惑度/词频过滤。
- 优点：零模型、极快、可离线、完全可控。缺点：覆盖率低、需人工维护、无语义。
- 适用：**高频固定错字**（如某类公文中反复出现的 `帐/账`），或作为其他方案的快速兜底。

### 2.2 统计语言模型（KenLM，传统但稳）

- n-gram 困惑度判异常字 → 候选（拼音/形近）→ 困惑度排序。
- pycorrector 内置 KenLM 路径；轻量、可解释；对短文本/未登录词较弱。

### 2.3 小模型（最常用的「简单」档）—— **推荐默认**

- **[pycorrector](https://github.com/shibing624/pycorrector)**（shibing624，2k★+）：`pip install pycorrector`。
  - 内置 **KenLM / ConvSeq2Seq / BERT / MacBERT / ELECTRA / ERNIE / GPT** 多种模型。
  - 明确覆盖 **音似 + 形似 + 语法** 三类，文档把 **OCR 识别错误、ASR 错误**列为应用场景 → 与本项目最贴合。
  - 工作原理：语言模型检测错位 → 拼音/笔画生成候选 → 困惑度过滤排序。
- **MacBERT** 是当前中文拼写纠错（CSC）的主流强基线（基于 BERT 的掩码纠错，针对中文错字优化）。
- 数据集：**SIGHAN CSC**（标准评测）、**Wang271K**（含 OCR 形近错误的自动生成语料）。

### 2.4 通用大模型 + Prompt（零部署、强语义）

- 用 GPT-4 / Claude / DeepSeek / Qwen 等，配 **few-shot 思维链（CoT）** 提示。
- [CCL 2024 评测结论](https://aclanthology.org/2024.ccl-1.62.pdf)：**国内大模型（少样本 CoT）在中文纠错上整体优于国外模型**，词序/拼写纠正准确率高；[Suda&阿里 CCL2023 报告](https://aclanthology.org/anthology-files/anthology-files/pdf/ccl/2023.ccl-3.25.pdf) 指出大模型对复杂语法错误的检测**精确率很高，部分已超 SOTA**。
- ⚠️ 两大坑：
  1. **过度润色 / 改写**（多篇报告点名）——须强约束 prompt「只纠错、不改写、保持原意」；
  2. **自我纠错不可靠**（推理任务自纠成功率反降，[参考](https://www.woshipm.com/ai/5926090.html)）——不能让模型自己判自己，要给明确的纠错任务 + diff 输出。
- 成本：逐段调 LLM 慢/贵；整文档量大时不划算。

### 2.5 专用微调纠错 LLM

- **[GrammarGPT](https://hub.baai.org/view/28836)**：基于 ~1k 并行语料监督微调的中文语法纠错开源 LLM。
- **[TW-NLP/ChineseErrorCorrector](https://github.com/TW-NLP/ChineseErrorCorrector)**：覆盖 CSC + CGEC，号称开源最强、[宣称超华为 17 点](https://www.cnblogs.com/TW-NLP/p/18756992)。
- 优点：比通用 LLM 更聚焦、过度改写更少。缺点：需部署一个 LLM（算力/显存）。

### 2.6 训练-free LLM 纠错（前沿）

- **[C2EC（ACL 2025）](https://aclanthology.org/2025.acl-long.678/)**：免训练，覆盖三类字符错误，直接用现成 LLM。
- **[CEC-Zero（AAAI 2026）](https://ojs.aaai.org/index.php/AAAI/article/view/39534/43495)**：零监督 RL 框架，免人工标注。

---

## 3. OCR 场景特别考虑

- **错误以形近字为主**（不是音近），所以**形似特征**是关键（[来也科技 OCR 纠错](https://laiye.com/news/post/2522.html)、[南大学报：语境 + 字形/拼音融合](http://home.ustc.edu.cn/~sa517494/files/nanda24.pdf)）。
- **置信度可用**：PaddleOCR 每行/每字带 `score`，低置信字优先纠、高置信字不动，能大幅降低误改。
- **解耦后处理**：纠错模块放在识别之后、IR 组装/导出之前，只改文本层、不动结构/bbox/provenance。

---

## 4. 评价指标

- **Precision（精确率）/ Recall（召回率）/ F0.5**。
- 纠错任务**偏精确率**（怕把对的改错），故主看 **F0.5**（β=0.5，精确率权重更高）。
- 误判代价：OCR 场景里「错改一个对的字」往往比「漏纠一个错字」更伤语义，所以宁可保守。

---

## 5. 方案对比

| 方案 | 准确率 | 速度 | 成本 | 可控性 | 部署 | 适用 |
|---|---|---|---|---|---|---|
| 规则 + 混淆集 | 低（覆盖率有限） | 极快（ms） | 0 | 高 | 零依赖 | 高频固定错字、兜底 |
| KenLM 统计模型 | 中 | 快 | 低 | 中 | 轻量 | 通用、离线 |
| **pycorrector / MacBERT** | **较高（CSC 主流）** | **中（GPU 更佳）** | **低（一个包+权重）** | **中高** | **pip + 模型** | **OCR 形近字（默认推荐）** |
| 通用 LLM few-shot | 高（含语义/语序） | 慢（逐段 API） | 高（token） | 低（过度改写风险） | 远程 API | 复杂语境、高质量档 |
| 专用微调 LLM（GrammarGPT 等） | 高 | 慢 | 中（需 GPU） | 中 | 需部署 LLM | 语法纠错为主 |
| 训练-free LLM（C2EC/CEC-Zero） | 高（前沿） | 慢 | 中 | 中 | 现成 LLM | 研究性、免训练 |

---

## 6. 落到 document2chunk 的建议

OCR 输出已是「markdown + 段落 + bbox + 置信度」，纠错适合做成**可选后处理**，不改 IR 结构、只在文本层修字。推荐**分档 + 开关**：

```
corrector 模块（新增，可选）
  correct(text, *, mode="macbert"|"llm"|"rule", confidence=None) -> (corrected, diff)
```

- **默认档（先做）**：`pycorrector` MacBERT，逐段过；**配 OCR 置信度阈值**——只对低置信片段触发，降低误改与开销。本地、快、可控、可离线。
- **高质量档（可选）**：通用 LLM few-shot（复用现有远程服务调用范式），强约束 prompt「只纠错、不改写」+ 输出 diff。处理语义/语序错误。
- **兜底**：小型形近字混淆表（高频固定错字）。

接入点：在 `OcrExtractor` 结果（或 `export` 前）可选启用（如 `options.correct_typos=True` / `options.corrector_mode=...`）；**纠错前后文本留痕**（写入 `metadata` 或 debug dump），便于回溯与可视化（与现有「graceful degradation + debug 可视化」思路一致）。

> 注意：纠错不应破坏 IR 语义——只修 `RunNode.text` / `ParagraphNode.text` 等文本字段，不动 `provenance`/结构/ID。误改风险高的字（专名、数字、编号）建议白名单跳过。

---

## 7. Sources

### 开源工具 / 模型
- [pycorrector（GitHub，shibing624）](https://github.com/shibing624/pycorrector) — 中文文本纠错工具（音似/形似/语法，含 BERT/MacBERT）
- [pycorrector（PyPI）](https://pypi.org/project/pycorrector/)
- [pycorrector 使用教程（CSDN）](https://blog.csdn.net/LuohenYJ/article/details/133235908)
- [pycorrector 深度介绍（知乎）](https://zhuanlan.zhihu.com/p/381811993)
- [TW-NLP/ChineseErrorCorrector（GitHub）](https://github.com/TW-NLP/ChineseErrorCorrector) — CSC+CGEC 综合平台
- [开源最强中文纠错大模型（介绍）](https://www.cnblogs.com/TW-NLP/p/18756992)
- [GrammarGPT（BAAI）](https://hub.baai.org/view/28836) — 监督微调中文语法纠错 LLM
- [Chinese-text-correction-papers（论文清单）](https://github.com/nghuyong/Chinese-text-correction-papers)

### 综述 / 论文
- [Chinese Spelling Correction: A Comprehensive Survey（arXiv 2025）](https://arxiv.org/html/2502.11508v1)
- [大语言模型在中文文本纠错任务的评测（CCL 2024）](https://aclanthology.org/2024.ccl-1.62.pdf)
- [Suda & Alibaba 文本纠错系统（CCL 2023）](https://aclanthology.org/anthology-files/anthology-files/pdf/ccl/2023.ccl-3.25.pdf)
- [C2EC：Training-free LLM 中文纠错（ACL 2025）](https://aclanthology.org/2025.acl-long.678/)
- [CEC-Zero：零监督字符纠错（AAAI 2026）](https://ojs.aaai.org/index.php/AAAI/article/view/39534/43495)
- [Chinese Text Error Correction Based on LLMs（ACM 2025）](https://dl.acm.org/doi/full/10.1145/3778450.3778486)
- [LSTM-Enhanced Transformer CSC（Cambridge 2025）](https://www.cambridge.org/core/journals/natural-language-processing/article/chinese-spelling-correction-based-on-long-shortterm-memory-networkenhanced-transformer-and-dynamic-adaptive-weighted-multitask-learning/7DEDCD30E4EFA51546CB265CDCDE1DE9)
- [Research Progress on Chinese and English Text Error Correction（ITM 2025）](https://www.itm-conferences.org/articles/itmconf/pdf/2025/04/itmconf_iwadi2024_02009.pdf)

### OCR 场景 / 技术实践
- [NLP 中文拼写/语法纠错介绍与综述（知乎）](https://zhuanlan.zhihu.com/p/571152299) ｜ [腾讯云镜像](https://cloud.tencent.com/developer/article/2052483)
- [基于语义的 OCR 纠错实现（来也科技）](https://laiye.com/news/post/2522.html)
- [中文文本纠错——OCR 地名纠错（CSDN）](https://blog.csdn.net/weixin_41819299/article/details/111601806)
- [基于语境与文本结构融合的 CSC（南大学报）](http://home.ustc.edu.cn/~sa517494/files/nanda24.pdf) ｜ [期刊版](https://jns.nju.edu.cn/CN/10.13232/j.cnzi.jnju.2024.03.009)
- [中文文本智能纠错知多少（51CTO）](https://www.51cto.com/article/715865.html)
- [基于拼音输入法的纠错语料自动生成 / Wang271K（OpenBayes）](https://openbayes.com/console/open-tutorials/containers/vOJGMcsBqbf)
- [文字语义纠错技术探索与实践（达观数据）](https://www.datagrand.com/blog/%E6%96%87%E5%AD%97%E8%AF%AD%E4%B9%89%E7%BA%A0%E9%94%99%E6%8A%80%E6%9C%AF%E6%8E%A2%E7%B4%A2%E4%B8%8E%E5%AE%9E%E8%B7%B5-%E5%BC%A0%E5%81%A5.html)
- [中文文本纠错算法——错别字纠正的二三事（知乎）](https://zhuanlan.zhihu.com/p/40806718)
- [基于深度学习的中文文本错误识别与纠正模型总结](https://fengchao.pro/blog/chinese-text-correction/)

### 注意事项
- [GPT-4 不知道自己错了：LLM 自我纠错成功率极低](https://www.woshipm.com/ai/5926090.html)
- [基于类 ChatGPT 大模型的中文语法纠错语料构建（专利 CN117272984A）](https://patents.google.com/patent/CN117272984A/zh)
