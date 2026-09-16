# VML 验证实验记录：DOC_TARGET 默认值定版（docx）

- 日期：2026-09-16
- 环境：112 容器 document2chunk（LibreOffice 25.2.3.2 520(Build:2)，Dockerfile.d2c 新镜像，feat/legacy-format-support @ f56fc1c）
- 样本：`D:\temp\404_test\feasibility.doc`（OLE2 二进制 doc，2,669,085 字节，Word COM 转出、含 7 张图——本期错标 404 案例原件）
- 目的：实证 LibreOffice 容器内 `.doc → .docx` 的图片标记语言，定版 `DOCUMENT2CHUNK_DOC_TARGET` 默认值

## 命令

```bash
# 探测脚本（容器内）
cat > /tmp/vml_probe.py <<'EOF'
import re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1] if len(sys.argv) > 1 else "/tmp/probe_out/feasibility.docx")
xml = z.read("word/document.xml").decode("utf-8")
print("a:blip DrawingML:", len(re.findall(r"<a:blip", xml)))
print("v:imagedata VML:", len(re.findall(r"<v:imagedata", xml)))
print("w:pict VML容器:", len(re.findall(r"<w:pict", xml)))
styles = z.read("word/styles.xml").decode("utf-8")
print("styles.xml Heading styleIds:", sorted(set(re.findall(r'w:styleId="([^"]*[Hh]eading[^"]*)"', styles))))
print("word/media 条目数:", len([n for n in z.namelist() if n.startswith("word/media/")]))
EOF

# 传输 + 容器内转换与探测
scp feasibility.doc vml_probe.py root@128.23.67.112:/tmp/
ssh root@128.23.67.112 'docker cp /tmp/vml_probe.py document2chunk:/tmp/ && \
  docker cp /tmp/feasibility.doc document2chunk:/tmp/ && \
  docker exec document2chunk sh -c "soffice --headless \
    -env:UserInstallation=file:///tmp/probe_profile --convert-to docx \
    --outdir /tmp/probe_out /tmp/feasibility.doc && python /tmp/vml_probe.py /tmp/probe_out/feasibility.docx"'
```

## 计数结果（112 容器 LibreOffice 25.2.3.2）

| 指标 | 计数 |
|---|---|
| `<a:blip` DrawingML | **7** |
| `<v:imagedata` VML | 0 |
| `<w:pict` VML 容器 | 0 |
| styles.xml Heading styleIds | `Heading, Heading1, Heading2, Heading3, Heading4, TableHeading`（保留） |
| word/media 条目 | 7（与 a:blip 一一对应） |

转换 filter：`Office Open XML Text`（标准 OOXML 导出，非 MS Word 2003 筛选器）。

## 对照：Word COM 直转 docx（此前 404 案例的错标原件）

本地 2026-08-31 留档的 `feasibility.docx`（Word COM 产物、真 zip、被改名错标上传的那类文件）：

- a:blip = 0，v:imagedata = 7，w:pict = 7，styles.xml 无任何 Heading styleId

即 memory `doc-vml-image-gap` 记录的「Word COM 转出的 docx 图片全 VML、d2c 提不出图」针对的是 **Word COM 的 OOXML 导出**；而本服务的转换器是容器内 LibreOffice，其 OOXML 导出走 DrawingML。两者不是同一条产物路径，原担心的「转换产物仍是 VML → docx 路提不出图」不成立。

（注：用户直接上传 Word COM 产出的真 docx 仍是 VML，DocxExtractor 提不出其中的图——那是 docx 提取器自身的缺口，不在本期老格式归一化范围，见遗留问题。）

## 判据应用与结论

按 spec 判据：`a:blip > 0 → DrawingML → 默认 docx 保持`。

- **`DOCUMENT2CHUNK_DOC_TARGET` 默认值定版：`docx`**（serve.py `_doc_target_ext` 与 Dockerfile.d2c ENV 均维持现状，不改代码）
- 理由：
  1. 容器 LibreOffice 转出的 docx 图片全为 DrawingML（7/7），media 齐全——docx 路可提取图片；
  2. Heading1-4 样式完整保留——docx 路多级标题结构有据（比 pdf 路靠字号猜标题更稳）;
  3. pdf 目标仍可通过环境变量 `DOCUMENT2CHUNK_DOC_TARGET=pdf` 一键切换（对扫描感强/版式复杂的 doc 备用），默认不切。
- 端到端佐证：部署后错标件（OLE2 内容、.docx 后缀）走 112 `/parse-pdf` 返回 200，result.md 含多级标题与 images/ 图片引用（见 task-6-report.md 验收 ①）。

## 遗留

- 真 zip 的 Word COM VML docx 上传件：图片提取缺口仍在（DocxExtractor 不解析 `w:pict/v:imagedata`），二期评估在 docx extractor 补 VML 或对无 a:blip 但有 v:imagedata 的 docx 触发 LibreOffice 重导出。
