# -*- coding: utf-8 -*-
"""Regressioni del tema UI condiviso."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_FILES = (
    *sorted((ROOT / "static").glob("*.css")),
    *sorted((ROOT / "static").glob("*.js")),
    *sorted((ROOT / "templates").glob("*.html")),
    *sorted((ROOT / "templates_router").glob("*.html")),
    ROOT / "moduli" / "gestionale.py",
)

RIFERIMENTI_TEMA_VETRO = (
    "backdrop-filter",
    "data-design",
    "algeco_design",
    "toggledesign",
    "designtoggle",
    "--panel-blur",
    "--panel-saturate",
    "acrylic",
)
_RE_GLASS = re.compile(r"(?<![\w-])glass(?![\w-])")


def _contiene_riferimento_vetro(contenuto, riferimento):
    if riferimento == "glass":
        return bool(_RE_GLASS.search(contenuto))
    return riferimento in contenuto


def test_tema_vetro_assente_dai_sorgenti_ui():
    trovati = []
    for path in UI_FILES:
        contenuto = path.read_text(encoding="utf-8").casefold()
        for riferimento in RIFERIMENTI_TEMA_VETRO:
            if _contiene_riferimento_vetro(contenuto, riferimento):
                trovati.append(f"{path.relative_to(ROOT)}: {riferimento}")

    assert not trovati, "Riferimenti al tema vetro ancora presenti:\n" + "\n".join(trovati)


def test_mappa_persiste_il_tema_anche_nel_cookie_condiviso():
    mappa_js = (ROOT / "static" / "mappa.js").read_text(encoding="utf-8")
    mappa_html = (ROOT / "templates" / "mappa.html").read_text(encoding="utf-8")

    assert "setSharedCookie('algeco_theme', theme)" in mappa_js
    assert "document.documentElement.setAttribute('data-theme', theme)" in mappa_js
    assert "document.body.setAttribute('data-theme', theme)" in mappa_js
    assert "document.documentElement.setAttribute('data-theme', t)" in mappa_html


def test_freccia_select_non_viene_duplicata_dal_tema_chiaro():
    theme_css = (ROOT / "static" / "theme.css").read_text(encoding="utf-8")
    filone_html = (ROOT / "templates" / "filone.html").read_text(encoding="utf-8")

    assert theme_css.count("--select-chevron: url(") == 2
    assert "background-image: var(--select-chevron)" in theme_css
    assert "background-repeat: no-repeat" in theme_css
    assert "background-size: 12px 12px" in theme_css
    assert not re.search(
        r'\[data-theme="light"\]\s+select\s*\{[^}]*background-image',
        theme_css,
        re.DOTALL,
    )

    quick_controls = re.search(
        r"\.quick-input,\s*\.quick-select\s*\{(?P<body>[^}]*)\}",
        filone_html,
        re.DOTALL,
    )
    assert quick_controls
    assert "background-color:" in quick_controls.group("body")
    assert not re.search(r"(?<!-)background\s*:", quick_controls.group("body"))
    assert ".quick-select { padding-right: 32px; }" in filone_html
