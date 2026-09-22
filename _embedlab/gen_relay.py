# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step5c 中转异源生成：查询集用 deepseek-v4，V6 摘要用 qwen3.6-35b。

双异源（与彼此、与会话模型都不同源）消除「查询与摘要同源」混杂。
教训（2026-09-21 实测）：思考系模型 max_tokens 给小会只 thinking 不出正文——
deepseek-v4 查询 3072、qwen3.6-35b 摘要 8192（实测单次 thinking 吃 4272 tok）。
支持断点续跑（读已有产物跳过已完成 key）+ 每 10 表增量落盘 + 3 并发。
产出 gen/queries_relay.json + gen/summaries_relay.json + gen/relay_report.json。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

HERE = Path(__file__).parent
BASE_URL = "http://128.23.67.112:9050/v1"
Q_MODEL = "deepseek-v4"
S_MODEL = "qwen3.6-35b-a3b"
Q_TOKENS = 3072
S_TOKENS = 8192
WORKERS = 3

Q_PROMPT = """你是 RAG 检索评测的查询生成器。下面给你一张表格（| 分隔网格，·代表空）。
标题：{title}
表题：{caption}
表格：
{grid}

请生成 4 条中文检索测试问题，严格遵守：
1. 每条一句话 15-60 字，答案必须能从该表得出，问题本身不得泄露答案；
2. 第1条 同义改写型：问某单元格事实，但必须改写表头词与行词，禁止从表格复制任何连续≥5字的字符串（数字不受限）；
3. 第2条 数值比较/极值型：比较两个单元格、问最大/最小，或单位换算；
4. 第3条 跨行/跨列聚合型：对某行/列/子集求和、计数或求平均；
5. 第4条 表头语义定位型：用改写后的表头路径词问「某行某列的值」；
6. 四条覆盖表中不同区域，不许都问同一行。

只输出 JSON（不要多余文字）：
{{"queries": ["问题1", "问题2", "问题3", "问题4"]}}"""

S_PROMPT = """你是表格摘要生成器。下面给你一张表格（| 分隔网格，·代表空）。
标题：{title}
表题：{caption}
表格：
{grid}

请写一段 2-5 句的中文陈述句摘要：表的主题（从标题/表头推断）+ 2-4 个关键数值事实（带单位）+ 结构概述（几行几列、按什么组织）。
禁止疑问句；禁止从表格复制任何连续≥10字的字符串。

只输出 JSON（不要多余文字）：
{{"summary": "..."}}"""

_THINK = re.compile(r"<think>.*?</think>", re.S)


def find_json(text: str):
    text = _THINK.sub("", text)
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def call(client: OpenAI, model: str, prompt: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        stream=False,
    )
    return resp.choices[0].message.content or ""


def gen_with_retry(client, model, prompt, max_tokens, want, tries=3):
    last = ""
    for t in range(tries):
        try:
            raw = call(client, model, prompt, max_tokens)
        except Exception as e:
            last = f"EXC {e!r}"[:200]
            time.sleep(2 * (t + 1))
            continue
        last = raw[:200]
        obj = find_json(raw)
        if not obj:
            time.sleep(1)
            continue
        if want == "queries":
            qs = [str(x).strip() for x in obj.get("queries", [])]
            if len(qs) == 4 and all(qs):
                return qs, None
        else:
            s = str(obj.get("summary", "")).strip()
            if len(s) >= 40:
                return s, None
        time.sleep(1)
    return None, last


def main() -> None:
    key = (HERE / ".env").read_text(encoding="utf-8").split("=", 1)[1].strip()
    reqs = [
        json.loads(ln)
        for ln in (HERE / "gen" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]

    # 断点续跑
    q_path = HERE / "gen" / "queries_relay.json"
    s_path = HERE / "gen" / "summaries_relay.json"
    queries: dict = json.loads(q_path.read_text(encoding="utf-8")) if q_path.exists() else {}
    summaries: dict = json.loads(s_path.read_text(encoding="utf-8")) if s_path.exists() else {}
    todo = [r for r in reqs if r["key"] not in queries or r["key"] not in summaries]
    print(f"total={len(reqs)} done={len(reqs) - len(todo)} todo={len(todo)}", flush=True)

    key = (HERE / ".env").read_text(encoding="utf-8").split("=", 1)[1].strip()
    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=300)
    lock = threading.Lock()
    failures: list[dict] = []
    done = [0]
    t0 = time.time()

    def work(r: dict) -> None:
        ctx = {"title": r["title"] or "（无）", "caption": r["caption"] or "（无）",
               "grid": r["grid"]}
        qs, err = gen_with_retry(client, Q_MODEL, Q_PROMPT.format(**ctx), Q_TOKENS, "queries")
        if qs is None:
            with lock:
                failures.append({"key": r["key"], "stage": "queries", "err": err})
        s, err = gen_with_retry(client, S_MODEL, S_PROMPT.format(**ctx), S_TOKENS, "summary")
        if s is None:
            with lock:
                failures.append({"key": r["key"], "stage": "summary", "err": err})
        with lock:
            if qs:
                queries[r["key"]] = qs
            if s:
                summaries[r["key"]] = s
            done[0] += 1
            n = done[0]
            if n % 10 == 0:
                q_path.write_text(json.dumps(queries, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
                s_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
            print(f"[{n}/{len(todo)}] {time.time()-t0:.0f}s q={len(queries)} "
                  f"s={len(summaries)} fail={len(failures)}", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, todo))

    q_path.write_text(json.dumps(queries, ensure_ascii=False, indent=1), encoding="utf-8")
    s_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "gen" / "relay_report.json").write_text(
        json.dumps({"failures": failures}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"DONE keys={len(queries)} summaries={len(summaries)} "
          f"failures={len(failures)} {time.time()-t0:.0f}s", flush=True)
    sys.exit(0 if not failures else 2)


if __name__ == "__main__":
    main()
