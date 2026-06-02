"""Palette for the tally renderer."""

THEME = {
    "bg":           (12, 12, 16),
    "panel":        (20, 20, 27),
    "row_alt":      (17, 17, 23),
    "divider":      (48, 130, 200),       # accent line under column headers
    "summary_bg":   (26, 26, 34),         # totals row at the bottom
    "title":        (238, 236, 248),
    "text":         (228, 224, 240),
    "muted":        (138, 130, 158),
    "subtle":       (90, 84, 108),
    "pill_label": {
        "done": "Done",
        "yes":  "Yes",
        "no":   "No",
    },
    "pill_fg":      (255, 255, 255),
    "rank_up":      (80, 210, 130),
    "rank_down":    (230, 76, 76),
    "rank_flat":    (130, 122, 150),
    "podium": {
        1: (255, 215, 50),
        2: (200, 208, 220),
        3: (204, 128, 52),
    },
    "severity_steps": [
        (118, 110, 140),
        (232, 178, 52),
        (220, 88, 88),
    ],
    "use_display_font": True,
}
