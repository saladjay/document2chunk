# -*- coding: utf-8 -*-
"""预热背景池嵌入缓存：warm_cache.py bge|qwen → results/base_emb_relay_<arm>.npy"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from embed_eval import ARMS, Embedder

HERE = Path(__file__).parent


def main() -> None:
    arm = sys.argv[1]
    pool = json.loads((HERE / "pool_base.json").read_text(encoding="utf-8"))
    base = [c["text"] for c in pool if c["kind"] != "sample_table"]
    cache = HERE / "results" / f"base_emb_relay_{arm}.npy"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        print(f"cache exists {cache}")
        return
    e = Embedder(ARMS[arm], batch=32, sleep=0.05)
    M = e(base, show=True)
    np.save(cache, M)
    print(f"saved {cache} {M.shape}")


if __name__ == "__main__":
    main()
