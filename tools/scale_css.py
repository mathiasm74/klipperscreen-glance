#!/usr/bin/env python3
"""Scale the glance stylesheet for a non-reference screen size.

The theme is designed at 1024x600. This multiplies every px value by
min(width/1024, height/600), flooring at 1px, so an 800x480 HDMI5 gets a
proportionally smaller version of the same design. Font sizes scale with
everything else; hairline borders (<=2px) are kept as-is so keylines
don't vanish.

    scale_css.py <style.css> <width> <height> > scaled.css
"""
import re
import sys

REF_W, REF_H = 1024, 600


def main():
    path, width, height = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    factor = min(width / REF_W, height / REF_H)
    css = open(path).read()

    def sub(m):
        v = int(m.group(1))
        if v <= 2:                      # keylines stay hairline
            return m.group(0)
        return f"{max(1, round(v * factor))}px"

    sys.stdout.write(re.sub(r"(\d+)px", sub, css))


if __name__ == "__main__":
    main()
