"""Charcoal + Bone theme: applies ttk.Style configuration for every custom
style the app uses (buttons, labels, treeview, combobox, progressbar, scrollbar,
update banner).

Only style/options are handled here. Image loading (window icons, header mark)
stays in `ModderApp` because those PhotoImages must be retained on the app
instance to avoid Tk's opportunistic GC.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .constants import PALETTE


def apply_theme(root: tk.Tk) -> ttk.Style:
    """Configure root + ttk style with the Charcoal/Bone palette. Returns the
    `ttk.Style` so the caller can tweak further if needed."""
    root.configure(bg=PALETTE["char_900"])

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=PALETTE["char_900"], foreground=PALETTE["bone"],
                font=("Segoe UI", 10))
    s.configure("TFrame", background=PALETTE["char_900"])
    s.configure("TLabel", background=PALETTE["char_900"], foreground=PALETTE["bone"])
    s.configure("Brand.TLabel",
                background=PALETTE["char_900"], foreground=PALETTE["bone"],
                font=("Segoe UI", 18, "bold"))
    s.configure("Pill.TLabel",
                background=PALETTE["char_700"], foreground=PALETTE["accent"],
                font=("Segoe UI", 9, "bold"), padding=(6, 2))
    s.configure("Tagline.TLabel",
                background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                font=("Segoe UI", 9, "italic"))
    s.configure("StatusHeadline.TLabel",
                background=PALETTE["char_900"], foreground=PALETTE["bone"],
                font=("Segoe UI", 10))
    s.configure("StatusDot.TLabel",
                background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                font=("Segoe UI", 14))
    s.configure("Disclosure.TLabel",
                background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                font=("Segoe UI", 9))
    s.configure("Sep.TFrame", background=PALETTE["char_500"])
    s.configure("UpdateBanner.TFrame", background=PALETTE["warn"])
    s.configure("UpdateBanner.TLabel",
                background=PALETTE["warn"], foreground=PALETTE["char_900"],
                font=("Segoe UI", 10, "bold"))
    s.configure("UpdateBanner.TButton",
                background=PALETTE["char_900"], foreground=PALETTE["bone"],
                borderwidth=0, focusthickness=0, padding=(10, 4),
                font=("Segoe UI", 9, "bold"))
    s.map("UpdateBanner.TButton",
          background=[("active", PALETTE["char_700"])],
          foreground=[("active", PALETTE["bone"])])
    s.configure("TButton",
                background=PALETTE["char_500"], foreground=PALETTE["bone"],
                borderwidth=0, focusthickness=0, padding=(12, 6))
    s.map("TButton",
          background=[("active", PALETTE["char_300"]),
                      ("disabled", PALETTE["char_700"])],
          foreground=[("disabled", PALETTE["bone_dim"])])
    s.configure("Primary.TButton",
                background=PALETTE["accent"], foreground=PALETTE["char_900"],
                borderwidth=0, focusthickness=0, padding=(14, 6),
                font=("Segoe UI", 10, "bold"))
    s.map("Primary.TButton",
          background=[("active", PALETTE["bone_dim"]),
                      ("disabled", PALETTE["char_700"])],
          foreground=[("disabled", PALETTE["bone_dim"])])
    s.configure("Link.TButton",
                background=PALETTE["char_900"], foreground=PALETTE["bone_dim"],
                borderwidth=0, focusthickness=0, padding=(4, 2),
                font=("Segoe UI", 9))
    s.map("Link.TButton",
          background=[("active", PALETTE["char_900"])],
          foreground=[("active", PALETTE["bone"])])
    s.configure("Treeview",
                background=PALETTE["char_700"], fieldbackground=PALETTE["char_700"],
                foreground=PALETTE["bone"], rowheight=24, borderwidth=0)
    s.configure("Treeview.Heading",
                background=PALETTE["char_500"], foreground=PALETTE["bone"],
                font=("Segoe UI", 10, "bold"), borderwidth=0)
    s.map("Treeview",
          background=[("selected", PALETTE["accent"])],
          foreground=[("selected", PALETTE["char_900"])])
    s.configure("Vertical.TScrollbar",
                background=PALETTE["char_500"], troughcolor=PALETTE["char_700"],
                borderwidth=0, arrowcolor=PALETTE["bone"])
    s.configure("Horizontal.TProgressbar",
                background=PALETTE["accent"], troughcolor=PALETTE["char_700"],
                borderwidth=0, lightcolor=PALETTE["accent"],
                darkcolor=PALETTE["accent"])
    s.configure("TCombobox",
                fieldbackground=PALETTE["char_700"], background=PALETTE["char_500"],
                foreground=PALETTE["bone"], arrowcolor=PALETTE["bone"],
                selectbackground=PALETTE["char_700"],
                selectforeground=PALETTE["bone"],
                insertcolor=PALETTE["bone"])
    s.map("TCombobox",
          fieldbackground=[("readonly", PALETTE["char_700"]),
                           ("disabled", PALETTE["char_700"])],
          foreground=[("readonly", PALETTE["bone"]),
                      ("disabled", PALETTE["bone_dim"])],
          selectbackground=[("readonly", PALETTE["char_700"])],
          selectforeground=[("readonly", PALETTE["bone"])],
          arrowcolor=[("disabled", PALETTE["bone_dim"])])

    # The dropdown listbox is a plain tk.Listbox owned by the Combobox
    # popdown toplevel — ttk.Style doesn't reach it. Configure via the
    # option database instead.
    root.option_add("*TCombobox*Listbox.background", PALETTE["char_700"])
    root.option_add("*TCombobox*Listbox.foreground", PALETTE["bone"])
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["char_900"])
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.relief", "flat")

    return s
