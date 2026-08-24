# -*- coding: utf-8 -*-
"""深入分析指引文档中所有锚定图片的段落分布和重叠关系。"""
import zipfile
from pathlib import Path
from lxml import etree
from collections import defaultdict

src = Path(r"D:\project\kxx\04需求开发\005其他\download_chunk_from_coreagent\data_files")
files = [f for f in src.glob("*指引*") if "未整完" in f.name and not f.name.startswith("~$")]
target = files[0]
print(f"File: {target.name}\n")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

with zipfile.ZipFile(target) as z:
    doc = etree.fromstring(z.read("word/document.xml"), parser=etree.XMLParser(recover=True))
    rels = etree.fromstring(z.read("word/_rels/document.xml.rels"), parser=etree.XMLParser(recover=True))

rid_map = {}
for rel in rels:
    rid_map[rel.get("Id", "")] = rel.get("Target", "")

body = doc.find(f"{{{W}}}body")

# Scan ALL body children (including sdt) to find anchors
all_anchors = []
child_idx = 0
for child in body:
    tag = etree.QName(child).localname
    if tag == "sdt":
        content = child.find(f"{{{W}}}sdtContent")
        if content is not None:
            for sub in content:
                if etree.QName(sub).localname == "p":
                    # Use .// to find ALL anchors including inside AlternateContent
                    anchors = sub.findall(f".//{{{WP}}}anchor")
                    for anc in anchors:
                        blip = anc.find(f".//{{{A}}}blip")
                        if blip is not None:
                            rid = blip.get(f"{{{R}}}embed", "?")
                            all_anchors.append({
                                "child": f"sdt/p", "rid": rid,
                                "target": rid_map.get(rid, "?"),
                                "behind": anc.get("behindDoc", "0"),
                            })
        child_idx += 1
        continue
    if tag != "p":
        child_idx += 1
        continue
    
    # Find ALL anchors including inside mc:AlternateContent
    anchors = child.findall(f".//{{{WP}}}anchor")
    text = "".join(t.text or "" for t in child.iter(f"{{{W}}}t"))[:50]
    
    for anc in anchors:
        blip = anc.find(f".//{{{A}}}blip")
        if blip is None:
            continue
        rid = blip.get(f"{{{R}}}embed", "?")
        tgt = rid_map.get(rid, "?")
        behind = anc.get("behindDoc", "0")
        
        extent = anc.find(f"{{{WP}}}extent")
        cx = int(extent.get("cx", 0)) if extent is not None else 0
        cy = int(extent.get("cy", 0)) if extent is not None else 0
        
        ph = anc.find(f"{{{WP}}}positionH")
        pv = anc.find(f"{{{WP}}}positionV")
        ph_offset = None
        pv_offset = None
        ph_rel = pv_rel = "?"
        if ph is not None:
            ph_rel = ph.get("relativeFrom", "?")
            off = ph.find(f"{{{WP}}}posOffset")
            if off is not None and off.text:
                ph_offset = int(off.text)
        if pv is not None:
            pv_rel = pv.get("relativeFrom", "?")
            off = pv.find(f"{{{WP}}}posOffset")
            if off is not None and off.text:
                pv_offset = int(off.text)
        
        all_anchors.append({
            "child": child_idx, "rid": rid, "target": tgt,
            "behind": behind, "cx": cx, "cy": cy,
            "ph_rel": ph_rel, "pv_rel": pv_rel,
            "ph": ph_offset, "pv": pv_offset,
            "text": text,
        })
    child_idx += 1

print(f"Total anchored images: {len(all_anchors)}\n")

# Group by child index to find multi-image paragraphs
by_child = defaultdict(list)
for a in all_anchors:
    by_child[a["child"]].append(a)

print("=== All anchored images ===\n")
for child_key in sorted(by_child.keys(), key=lambda x: (isinstance(x, str), x)):
    imgs = by_child[child_key]
    text = imgs[0].get("text", "")
    print(f"Child {child_key} ({len(imgs)} images): |{text[:40]}|")
    for img in imgs:
        behind = "behind" if img["behind"] == "1" else "front"
        cx_in = img.get("cx", 0) / 914400
        cy_in = img.get("cy", 0) / 914400
        ph_str = f"{img['ph_rel']}:{img['ph']}" if img.get("ph") is not None else "?"
        pv_str = f"{img['pv_rel']}:{img['pv']}" if img.get("pv") is not None else "?"
        print(f"  {img['target']:25s} {behind:6s} {cx_in:.1f}x{cy_in:.1f}in  posH={ph_str} posV={pv_str}")

# Check consecutive paragraphs for background+foreground pattern
print("\n\n=== Potential overlap groups (nearby paragraphs) ===\n")
sorted_children = sorted(by_child.keys(), key=lambda x: (isinstance(x, str), x))
for i, key in enumerate(sorted_children):
    imgs = by_child[key]
    fronts = [img for img in imgs if img["behind"] == "0"]
    backs = [img for img in imgs if img["behind"] == "1"]
    
    # Check if next child has complementary layer
    if i + 1 < len(sorted_children):
        next_key = sorted_children[i + 1]
        next_imgs = by_child[next_key]
        next_fronts = [img for img in next_imgs if img["behind"] == "0"]
        next_backs = [img for img in next_imgs if img["behind"] == "1"]
        
        # Current has back, next has front (or vice versa)
        if backs and next_fronts:
            print(f"OVERLAP: child {key} (back) + child {next_key} (front)")
            for b in backs:
                print(f"  BACK:  {b['target']}")
            for f in next_fronts:
                print(f"  FRONT: {f['target']}")
        if fronts and next_backs:
            print(f"OVERLAP: child {key} (front) + child {next_key} (back)")
