# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step7c T2/T3 多 gold 评测。

gold 语义（审核五层类型学）：
- T2 联合推理：gold = {表 chunk} ∪ {含上下文段落的节 chunk}；口径 relaxed(任一命中)/
  strict(全命中)/first-hit(最高名次命中，最贴近生产 top-k 有用性)
- T3 上下文定位：gold = {节 chunk}；表 chunk 进入 top-k 记为「侵入」（干扰度）
用法: .venv/Scripts/python.exe eval_t2.py --arm bge|qwen
产物: results/metrics_t2_{arm}.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from embed_eval import ARMS, Embedder, pipe_plain, worlds_for
from serialize import v1_html, v2_pipe_expanded, v3_kv, v4_xml, v5_caption

HERE = Path(__file__).parent
KS = [1, 5, 10, 15, 20]


def render(world: str, role: str, table: dict, title: str, caption: str,
           summary: str) -> str:
    if world == "v1":
        return v1_html(table) if role == "merged" else pipe_plain(table)
    if world == "v2":
        return v2_pipe_expanded(table)
    if world == "v3":
        return v3_kv(table)
    if world == "v4":
        return v4_xml(table)
    if world == "v5":
        sign = caption or title or "表格"
        body = v1_html(table) if role == "merged" else pipe_plain(table)
        return f"表格：{sign}\n" + body
    if world == "v6":
        return summary
    raise KeyError(world)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=list(ARMS), required=True)
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    pool = json.loads((HERE / "pool_base.json").read_text(encoding="utf-8"))
    queries = json.loads((HERE / "gen" / "t2_queries.json").read_text(encoding="utf-8"))
    summaries = json.loads((HERE / "gen" / "summaries_relay.json").read_text(encoding="utf-8"))
    sample_meta: dict = {}
    for dp in HERE.glob("material/*.json"):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        sample_meta.update(doc["samples"])

    base_chunks = [c for c in pool if c["kind"] != "sample_table"]
    base_row = {c["id"]: i for i, c in enumerate(base_chunks)}
    sections_by_doc: dict[str, list[int]] = {}
    for i, c in enumerate(base_chunks):
        if c["kind"] == "section":
            sections_by_doc.setdefault(c["id"].rsplit("::", 1)[0], []).append(i)

    # gold 集构造：T2={表+节}, T3={节}
    gold: dict[str, dict] = {}
    for key, q in queries.items():
        fname = key.rsplit("::", 1)[0]
        secs = []
        for para in q.get("context_left", []) + q.get("context_right", []):
            frag = para[:40].strip()
            if not frag:
                continue
            for ci in sections_by_doc.get(fname, []):
                if frag in base_chunks[ci]["text"]:
                    if ci not in secs:
                        secs.append(ci)
                    break
        gold[key] = {
            "table_rows": [len(base_chunks) + 0],  # 占位，world 循环内替换
            "section_rows": secs,
            "type": "T2" if q.get("t2") else "T3",
        }

    emb = Embedder(ARMS[args.arm], batch=args.batch, sleep=0.05)
    cache = HERE / "results" / f"base_emb_{ARMS[args.arm]}.npy"
    base_emb = np.load(cache)
    assert base_emb.shape[0] == len(base_chunks)

    # 各 world 的样本表文本与行号
    world_texts: dict[str, dict[str, str]] = {}
    for key, meta in sample_meta.items():
        prefix = (meta["title"] + "\n") if meta["title"] else ""
        for w in worlds_for(meta["role"]):
            world_texts.setdefault(w, {})[key] = prefix + render(
                w, meta["role"], meta["table"], meta["title"],
                meta.get("caption", ""), summaries.get(key, ""))

    q_texts, q_keys, q_types = [], [], []
    for key, q in queries.items():
        for tname in ("t2", "t3"):
            if q.get(tname):
                q_texts.append(q[tname])
                q_keys.append(key)
                q_types.append(tname.upper())
    q_emb = emb(q_texts)
    print(f"arm={args.arm} queries={len(q_texts)} (T2={q_types.count('T2')}, "
          f"T3={q_types.count('T3')})", flush=True)

    results = {"arm": args.arm, "model": ARMS[args.arm],
               "query_set": "gen/t2_queries.json", "worlds": {}}
    sec_rows = [i for i, c in enumerate(base_chunks) if c["kind"] == "section"]
    for w, texts in world_texts.items():
        semb = emb(list(texts.values()))
        ids = list(texts.keys())
        sample_row = {f"tbl::{k}": len(base_chunks) + i for i, k in enumerate(ids)}
        M = np.vstack([base_emb, semb])
        rec: dict = {}
        for tname in ("T2", "T3"):
            idxs = [i for i, t in enumerate(q_types)
                    if t == tname and q_keys[i] in texts]  # world 覆盖过滤（v2 无 simple）
            if not idxs:
                continue
            stats = {f"{m}@{k}": [] for m in ("relaxed", "strict", "first") for k in KS}
            intrude = []
            for i in idxs:
                g = gold[q_keys[i]]
                grows = [sample_row[f"tbl::{q_keys[i]}"]] if tname == "T2" else []
                grows += g["section_rows"]
                if not grows:
                    continue
                S = q_emb[i] @ M.T
                order = np.argsort(-S)
                rank = {int(r): int((order == r).nonzero()[0][0]) + 1 for r in grows}
                # 表 chunk 名次（T3 侵入用）
                trow = sample_row.get(f"tbl::{q_keys[i]}")
                trank = int((order == trow).nonzero()[0][0]) + 1 if trow is not None else None
                for k in KS:
                    hits = [rank[r] <= k for r in rank]
                    stats[f"relaxed@{k}"].append(any(hits))
                    stats[f"strict@{k}"].append(all(hits))
                    stats[f"first@{k}"].append(min(rank.values()) <= k)
                if tname == "T3" and trank is not None:
                    intrude.append(trank <= 10)
            sub = {}
            for m in ("relaxed", "strict", "first"):
                for k in KS:
                    v = stats[f"{m}@{k}"]
                    sub[f"{m}@{k}"] = float(np.mean(v)) if v else None
            sub["n"] = len(idxs)
            if tname == "T3":
                sub["table_intrusion@10"] = float(np.mean(intrude)) if intrude else None
            rec[tname] = sub
        results["worlds"][w] = rec
        # 方法 C：表 chunk 与散文 chunk 的平均余弦（区分度，越小越「异质」）
        rec["table_section_cos"] = float(
            (semb @ base_emb[sec_rows].T).mean())
        t2 = rec.get("T2", {})
        print(f"[{w}] T2 first@10={t2.get('first@10')} "
              f"relaxed@10={t2.get('relaxed@10')} strict@10={t2.get('strict@10')} "
              f"T3 intr@10={rec.get('T3', {}).get('table_intrusion@10')}", flush=True)

    out = HERE / "results" / f"metrics_t2_{args.arm}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
