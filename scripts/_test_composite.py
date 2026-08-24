# -*- coding: utf-8 -*-
"""Test image compositing on the guide document."""
from pathlib import Path
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.ir import ImageNode

src = Path(r"D:\project\kxx\04需求开发\005其他\download_chunk_from_coreagent\data_files")
files = [f for f in src.glob("*指引*") if "未整完" in f.name and not f.name.startswith("~$")]

out = Path(r"D:\temp\指引_composite_test2")
out.mkdir(parents=True, exist_ok=True)

ext = DocxExtractor()
result = ext.extract(str(files[0]), image_dir=str(out / "images"))

imgs = [b for b in result.content if isinstance(b, ImageNode)]
print(f"Content blocks: {len(result.content)}")
print(f"Images: {len(imgs)}")
composited = [img for img in imgs if img.metadata.get("composited")]
print(f"Composited: {len(composited)}")
for img in composited:
    oc = img.metadata.get("overlay_count")
    print(f"  {img.image_id:35s} overlays={oc}")

# Show all images with anchor info
print("\n=== All images ===")
for img in imgs:
    comp = "COMPOSITED" if img.metadata.get("composited") else ""
    behind = img.anchor_behind_doc
    print(f"  {img.image_id:35s} behind={str(behind):5s} {comp}")

img_dir = out / "images"
composites = [f for f in img_dir.iterdir() if "composite" in f.name]
print(f"\nComposite files: {len(composites)}")
for f in sorted(composites):
    print(f"  {f.name:35s} {f.stat().st_size:>8,} bytes")
