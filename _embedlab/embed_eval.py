# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step6 评测（中转 API 版）：六变体 × Recall@{1,5,10,15,20}/MRR。

用法（lab venv，cwd=_embedlab）:
  .venv/Scripts/python.exe embed_eval.py --arm bge  --tag relay
  .venv/Scripts/python.exe embed_eval.py --arm qwen --tag relay
  # 稳健性对照（换查询集，V6 摘要不变，背景池走缓存）：
  .venv/Scripts/python.exe embed_eval.py --arm bge --tag relay \\
      --queries gen/queries_session.json

arm→模型：bge→bge-m3(1024维，=下游 Milvus 生产配置)；qwen→qwen3-vl-embedding-8b(4096维)。
背景池嵌入按 (tag,arm) 缓存到 results/base_emb_{tag}_{arm}.npy。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from openai import OpenAI
from serialize import v1_html, v2_pipe_expanded, v3_kv, v4_xml, v5_caption

HERE = Path(__file__).parent
BASE_URL = "http://128.23.67.112:9050/v1"
ARMS = {"bge": "bge-m3", "qwen": "qwen3-vl-embedding-8b"}
KS = [1, 5, 10, 15, 20]
QTYPE = ["paraphrase", "compare", "aggregate", "locate"]
MERGED_WORLDS = ["v1", "v2", "v3", "v4", "v5", "v6"]
SIMPLE_WORLDS = ["v1", "v3", "v4", "v5", "v6"]


def pipe_plain(table: dict) -> str:
    rows = table["rows"]
    if not rows:
        return ""
    lines = []
    for i, row in enumerate(rows):
        cells = [(c.get("t", "") or " ").replace("|", "\\|") for c in row["cells"]]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in cells) + " |")
    return "\n".join(lines)


def v5_caption_pipe(table: dict, title: str, caption: str) -> str:
    sign = caption or title or "表格"
    return f"表格：{sign}\n" + pipe_plain(table)


def render(world: str, role: str, table: dict, title: str, caption: str,
           summary: str) -> str:
    if world == "v1":
        return pipe_plain(table) if role == "simple" else v1_html(table)
    if world == "v2":
        return v2_pipe_expanded(table)
    if world == "v3":
        return v3_kv(table)
    if world == "v4":
        return v4_xml(table)
    if world == "v5":
        return (v5_caption_pipe(table, title, caption) if role == "simple"
                else v5_caption(table, title, caption))
    if world == "v6":
        if not summary:
            raise ValueError("v6 摘要缺失")
        return summary
    raise KeyError(world)


def worlds_for(role: str) -> list[str]:
    return SIMPLE_WORLDS if role == "simple" else MERGED_WORLDS


class Embedder:
    def __init__(self, model: str, batch: int, sleep: float):
        key = (HERE / ".env").read_text(encoding="utf-8").split("=", 1)[1].strip()
        self.client = OpenAI(base_url=BASE_URL, api_key=key, timeout=120)
        self.model = model
        self.batch = batch
        self.sleep = sleep

    def __call__(self, texts: list[str], show: bool = False) -> np.ndarray:
        out: list[np.ndarray] = []
        t0 = time.time()
        i = 0
        CHAR_BUDGET = 6000   # 上游按整批 token 计 8192 上限；中文 ~1.1 tok/char
        MAX_CHARS = 6000     # 单条硬截断（超长表弥散影响记入报告）
        while i < len(texts):
            batch: list[str] = []
            chars = 0
            while i < len(texts) and len(batch) < self.batch:
                t = texts[i][:MAX_CHARS]
                if batch and chars + len(t) > CHAR_BUDGET:
                    break
                batch.append(t)
                chars += len(t)
                i += 1
            for attempt in range(4):
                try:
                    resp = self.client.embeddings.create(
                        model=self.model, input=batch)
                    break
                except Exception as e:
                    if attempt == 3:
                        raise
                    wait = 3 * (attempt + 1)
                    print(f"  retry {attempt + 1} after {wait}s: {e!r}"[:120],
                          flush=True)
                    time.sleep(wait)
            vecs = sorted(resp.data, key=lambda d: d.index)
            out.append(np.array([d.embedding for d in vecs], dtype=np.float32))
            if show and len(out) % 20 == 0:
                print(f"  embed {i}/{len(texts)} "
                      f"{time.time() - t0:.0f}s", flush=True)
            time.sleep(self.sleep)
        M = np.vstack(out)
        return M / np.linalg.norm(M, axis=1, keepdims=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=list(ARMS), required=True)
    ap.add_argument("--tag", default="relay")
    ap.add_argument("--queries", default="gen/queries_relay.json")
    ap.add_argument("--summaries", default="gen/summaries_relay.json")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    model = ARMS[args.arm]

    pool = json.loads((HERE / "pool_base.json").read_text(encoding="utf-8"))
    queries = json.loads((HERE / args.queries).read_text(encoding="utf-8"))
    summaries = json.loads((HERE / args.summaries).read_text(encoding="utf-8"))
    sample_meta: dict = {}
    for dp in HERE.glob("material/*.json"):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        sample_meta.update(doc["samples"])

    base_chunks = [c for c in pool if c["kind"] != "sample_table"]
    emb = Embedder(model, args.batch, args.sleep)
    cache = HERE / "results" / f"base_emb_{ARMS[args.arm]}.npy"  # 缓存按模型，不按 tag
    cache.parent.mkdir(exist_ok=True)
    if cache.exists() and not args.no_cache:
        base_emb = np.load(cache)
        assert base_emb.shape[0] == len(base_chunks), "cache stale"
        print(f"base cache hit {base_emb.shape}", flush=True)
    else:
        base_emb = emb([c["text"] for c in base_chunks], show=True)
        np.save(cache, base_emb)

    q_texts, q_gold, q_role, q_type, q_key = [], [], [], [], []
    for key, qs in queries.items():
        for i, q in enumerate(qs):
            q_texts.append(q)
            q_gold.append(f"tbl::{key}")
            q_key.append(key)
            q_role.append(sample_meta[key]["role"])
            q_type.append(QTYPE[i] if i < 4 else "extra")
    q_emb = emb(q_texts)
    print(f"arm={args.arm}({model}) base={len(base_chunks)} "
          f"queries={len(q_texts)} qset={args.queries}", flush=True)

    world_texts: dict[str, dict[str, str]] = {}
    for key, meta in sample_meta.items():
        prefix = (meta["title"] + "\n") if meta["title"] else ""
        for w in worlds_for(meta["role"]):
            world_texts.setdefault(w, {})[key] = prefix + render(
                w, meta["role"], meta["table"], meta["title"],
                meta.get("caption", ""), summaries.get(key, ""))

    results = {"model": model, "arm": args.arm, "tag": args.tag,
               "query_set": args.queries, "worlds": {}}
    ranks_out = {}
    for w, texts in world_texts.items():
        semb = emb(list(texts.values()))
        ids = list(texts.keys())
        M = np.vstack([base_emb, semb])
        sample_row = {f"tbl::{k}": len(base_chunks) + i for i, k in enumerate(ids)}
        # 每个 world 只对它覆盖的表计分（v2 无 simple、simple 无 v2）
        idxs = np.array([i for i, k in enumerate(q_key) if k in texts])
        gold_rows = np.array([sample_row[q_gold[i]] for i in idxs])
        S = (q_emb @ M.T)[idxs]
        order = np.argsort(-S, axis=1)
        ranks = np.array(
            [(order[j] == gold_rows[j]).nonzero()[0][0] + 1
             for j in range(len(idxs))])
        rec = {f"r@{k}": float((ranks <= k).mean()) for k in KS}
        rec["mrr"] = float((1.0 / ranks).mean())
        rec["n"] = int(len(ranks))
        idx_role = [q_role[i] for i in idxs]
        idx_type = [q_type[i] for i in idxs]
        for role in ("merged", "simple"):
            mask = np.array([r == role for r in idx_role])
            if not mask.any():
                continue
            sub = {f"r@{k}": float((ranks[mask] <= k).mean()) for k in KS}
            sub["mrr"] = float((1.0 / ranks[mask]).mean())
            sub["n"] = int(mask.sum())
            rec[role] = sub
        mask_m = np.array([r == "merged" for r in idx_role])
        for qt in QTYPE:
            m2 = mask_m & np.array([t == qt for t in idx_type])
            if m2.any():
                rec[f"merged:{qt}"] = {f"r@{k}": float((ranks[m2] <= k).mean())
                                       for k in KS}
        results["worlds"][w] = rec
        ranks_out[w] = ranks.tolist()
        print(f"[{w}] " + " ".join(f"r@{k}={rec[f'r@{k}']:.3f}" for k in KS)
              + f" mrr={rec['mrr']:.3f}", flush=True)

    out = HERE / "results"
    (out / f"metrics_{args.tag}_{args.arm}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"ranks_{args.tag}_{args.arm}_{Path(args.queries).stem}.json").write_text(
        json.dumps(ranks_out), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
