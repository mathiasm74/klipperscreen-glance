# -*- coding: utf-8 -*-
# Shared scale for the glance panels. The layout is designed at 1024x600;
# panels route their absolute pixel sizes through px() so smaller screens
# (e.g. an 800x480 HDMI5) shrink proportionally. The stylesheet is scaled
# separately at install time by tools/scale_css.py with the same reference.

REF_W, REF_H = 1024, 600


def scale(screen):
    return min(screen.width / REF_W, screen.height / REF_H)


def px(screen, value):
    return max(1, round(value * scale(screen)))
