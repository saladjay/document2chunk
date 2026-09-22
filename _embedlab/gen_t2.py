# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step7b T2/T3 查询生成：deepseek-v4，每表 2 问。

T2 联合推理：答案必须同时用到表格数据与前后正文（gold=表 chunk+节 chunk）。
T3 上下文定位：答案在正文中，表格只是背景/干扰（gold=节 chunk）。
复用 gen_relay 的中转客户端与 JSON 容错；增量落盘+断点续跑。
产出 gen/t2_queries.json：{key: {"t2": 问题, "t3": 问题, "context_left/right": [...]}}。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from gen_relay import find_json
from openai import OpenAI

HERE = Path(__file__).parent
BASE_URL = "http://128.23.67.112:9050/v1"
MODEL = "deepseek-v4"
TOKENS = 3072

COMMON = """你是 RAG 检索评测的查询生成器。下面给你一张表格和它前后的正文。
标题：{title}
表格（| 分隔网格，·代表空）：
{grid}

【表前正文】
{left}

【表后正文】
{right}
"""

T2_PROMPT = COMMON + """请生成 1 条中文「联合推理」检索问题，严格要求：
- 答案必须同时用到 ①表格中的数据和 ②正文中的事实/规则/说明，缺一不可；
- 问题本身不泄露答案；15-60 字；禁止从材料复制连续 ≥6 字的字符串（数字不受限）；
- 是用户会自然问出的话，不要出现「根据表格」「根据正文」这类元描述。

只输出 JSON：{{"question": "..."}}"""

T3_PROMPT = COMMON + """请生成 1 条中文「上下文定位」检索问题，严格要求：
- 答案完全在正文里（不需要读表格就能回答），表格只是话题背景；
- 问题围绕表格所在章节的话题展开（这样表格 chunk 才可能成为干扰项）；
- 问题本身不泄露答案；15-60 字；禁止从材料复制连续 ≥6 字的字符串（数字不受限）。

只输出 JSON：{{"question": "..."}}"""


def ask(client: OpenAI, prompt: str) -> str | None:
    for t in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL, max_tokens=TOKENS, stream=False,
                messages=[{"role": "user", "content": prompt}])
            obj = find_json(resp.choices[0].message.content or "")
            if obj and str(obj.get("question", "")).strip():
                return str(obj["question"]).strip()
        except Exception:
            pass
        time.sleep(2 * (t + 1))
    return None


def main() -> None:
    key = (HERE / ".env").read_text(encoding="utf-8").split("=", 1)[1].strip()
    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=300)
    reqs = [
        json.loads(ln)
        for ln in (HERE / "gen" / "requests_t2.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    out_path = HERE / "gen" / "t2_queries.json"
    out: dict = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    failures = []
    t0 = time.time()
    for i, r in enumerate(reqs, 1):
        if r["key"] in out and all(out[r["key"]].get(k) for k in ("t2", "t3")):
            continue
        ctx = {
            "title": r["title"] or "（无）",
            "grid": r["grid"],
            "left": "\n".join(r["context_left"]) or "（无）",
            "right": "\n".join(r["context_right"]) or "（无）",
        }
        q2 = ask(client, T2_PROMPT.format(**ctx))
        q3 = ask(client, T3_PROMPT.format(**ctx))
        if q2 is None or q3 is None:
            failures.append({"key": r["key"], "t2": q2, "t3": q3})
        cur = out.setdefault(r["key"], {})
        if q2:
            cur["t2"] = q2
        if q3:
            cur["t3"] = q3
        cur["context_left"] = r["context_left"]
        cur["context_right"] = r["context_right"]
        cur["role"] = r["role"]
        if i % 10 == 0:
            out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                encoding="utf-8")
            print(f"[{i}/{len(reqs)}] {time.time()-t0:.0f}s done={len(out)} "
                  f"fail={len(failures)}", flush=True)
        time.sleep(0.2)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "gen" / "t2_report.json").write_text(
        json.dumps({"failures": failures}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"DONE keys={len(out)} failures={len(failures)} {time.time()-t0:.0f}s",
          flush=True)


if __name__ == "__main__":
    main()
