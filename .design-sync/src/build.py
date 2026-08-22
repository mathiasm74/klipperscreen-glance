#!/usr/bin/env python3
"""Assemble the hand-authored ds-bundle preview cards.

Each card source in cards/ is full HTML with three placeholders:
  {{FONTS}}  -> base64 @font-face rules (self-contained cards)
  {{TOKENS}} -> the :root token block from ds-bundle/styles.css
  {{SHARED}} -> shared.css screen chrome
"""
import base64
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).resolve().parent
REPO = SRC.parent.parent
BUNDLE = REPO / "ds-bundle"

CARDS = {
    "Colors.html": "components/Foundations/Colors/Colors.html",
    "Type.html": "components/Foundations/Type/Type.html",
    "JobStatus.html": "components/Screens/JobStatus/JobStatus.html",
    "Home.html": "components/Screens/Home/Home.html",
    "Move.html": "components/Screens/Move/Move.html",
}


def font_css():
    rules = []
    for fam, fname in (("Anton", "Anton-Regular.ttf"),
                       ("Space Grotesk", "SpaceGrotesk.ttf")):
        b64 = base64.b64encode((BUNDLE / "fonts" / fname).read_bytes()).decode()
        rules.append(
            f'@font-face {{ font-family: "{fam}"; '
            f'src: url(data:font/ttf;base64,{b64}) format("truetype"); }}')
    return "\n".join(rules)


def tokens_css():
    css = (BUNDLE / "styles.css").read_text()
    m = re.search(r":root \{.*?\n\}", css, re.S)
    if not m:
        sys.exit("no :root block in styles.css")
    return m.group(0)


def main():
    fonts = font_css()
    tokens = tokens_css()
    shared = (SRC / "shared.css").read_text()
    for src_name, dest_rel in CARDS.items():
        src = SRC / "cards" / src_name
        if not src.exists():
            print(f"skip (missing): {src_name}")
            continue
        html = src.read_text()
        for key, val in (("{{FONTS}}", fonts), ("{{TOKENS}}", tokens),
                         ("{{SHARED}}", shared)):
            if key not in html:
                sys.exit(f"{src_name}: missing placeholder {key}")
            html = html.replace(key, val)
        dest = BUNDLE / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html)
        print(f"built {dest_rel} ({len(html) // 1024} KiB)")


if __name__ == "__main__":
    main()
