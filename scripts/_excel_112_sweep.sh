#!/bin/bash
# Excel 轨A 112 端到端测试扫：real/ + synthetic/ 全样本打 /parse-excel，产出 markdown 原始结果。
# 用法（在 112 上）：bash _excel_112_sweep.sh [样本根目录] [API]
BASE=${1:-/home/user/document2chunk-test/excel}
API=${2:-http://127.0.0.1:9301}
OUT=$BASE/report_raw.md
RESP=/tmp/resp_excel.json

{
echo "git_rev: $(cd /home/user/document2chunk && git log --oneline -1)"
echo "health: $(curl -s $API/health)"
echo
echo "| 目录 | 文件 | HTTP | rows | blocks | warns | 关键内容 | 耗时ms |"
echo "|---|---|---|---|---|---|---|---|"

for dir in real synthetic; do
  for f in "$BASE/$dir"/*; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    t0=$(date +%s%3N)
    code=$(curl -s -o $RESP -w "%{http_code}" -F "file=@$f" "$API/parse-excel")
    t1=$(date +%s%3N)
    RESP=$RESP python3 - "$dir" "$name" "$code" "$((t1 - t0))" << 'PYEOF'
import json, os, sys
d, name, code, ms = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    body = json.load(open(os.environ["RESP"], encoding="utf-8"))
    if code == "200":
        rows = body.get("rows", [])
        blocks = body.get("blocks", [])
        warns = body.get("warnings", [])
        keys = list(rows[0]["data"].keys()) if rows else []
        ks = ";".join(keys[:4]) + ("…" if len(keys) > 4 else "")
        text0 = (rows[0]["text"][:36] + "…") if rows and len(rows[0]["text"]) > 36 else (rows[0]["text"] if rows else "-")
        detail = f"{ks} ‖ {text0}"
        if blocks:
            detail += f" ‖ blocks={len(blocks)}({blocks[0]['kind']})"
        print(f"| {d} | {name} | {code} | {len(rows)} | {len(blocks)} | {len(warns)} | {detail} | {ms} |")
        if warns:
            wj = "; ".join(warns)
            wj = wj[:90] + "…" if len(wj) > 90 else wj
            print(f"| {d} | └ warnings | | | | {len(warns)} | {wj} | {ms} |")
    else:
        det = str(body.get("detail", body))[:100]
        print(f"| {d} | {name} | {code} | - | - | - | {det} | {ms} |")
except Exception as e:
    print(f"| {d} | {name} | {code} | ERR | | | {e} | {ms} |")
PYEOF
  done
done

# chai 端点对表格仍应 400 并指向 /parse-excel
f=$(ls "$BASE"/real/*.xlsx | head -1)
code=$(curl -s -o $RESP -w "%{http_code}" -F "file=@$f" "$API/parse-pdf")
detail=$(python3 -c "import json;print(json.load(open('$RESP',encoding='utf-8')).get('detail','')[:80])" 2>/dev/null)
echo
echo "chai /parse-pdf 对 xlsx：HTTP $code，detail=$detail"
} > "$OUT" 2>&1
echo "raw report -> $OUT"
cat "$OUT"
