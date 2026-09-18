"""Tests des invariants du depot.

Ces tests ne dependent ni du warehouse, ni du LLM, ni du reseau : ils tournent
des l'etape 0 et gardent le depot conforme a CLAUDE.md quand les hooks locaux
ne sont pas installes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

DOSSIERS_DE_CODE = ("src", "scripts", "eval", "app", "tests")
DOSSIERS_IGNORES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "data",
    "target",
    "dbt_packages",
    "node_modules",
    "htmlcov",
}

NOMS_FOURRE_TOUT = {
    "utils",
    "util",
    "helpers",
    "helper",
    "common",
    "misc",
    "tools",
    "temp",
    "tmp",
    "test2",
    "final",
    "new",
}

EXTENSIONS_DE_DONNEES = {".duckdb", ".csv", ".parquet", ".wal"}

MOTIF_VARIABLE_ENV = re.compile(r"\b((?:MISTRAL|ANAMNESE)_[A-Z0-9_]+)\b")

MOTIFS_DE_SECRET = (
    re.compile(r"\b[A-Za-z0-9]{32,}\b"),  # cle API brute
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),  # prefixe usuel de cle
)


def _fichiers(extension: str) -> list[Path]:
    """Tous les fichiers du depot portant cette extension, hors dossiers ignores."""
    return [
        chemin
        for chemin in RACINE.rglob(f"*{extension}")
        if not DOSSIERS_IGNORES & set(chemin.relative_to(RACINE).parts)
    ]


# --------------------------------------------------------------------------- #
# Donnees et secrets — CLAUDE.md §2, regles 2 et 3
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("motif", ["data/", ".env", "*.duckdb", "transform/target/"])
def test_gitignore_couvre_les_chemins_sensibles(motif: str) -> None:
    contenu = (RACINE / ".gitignore").read_text(encoding="utf-8")
    assert motif in contenu, f"{motif} doit etre ignore par git (CLAUDE.md §2)"


def test_aucun_fichier_de_donnees_hors_du_dossier_data() -> None:
    trouves = [
        chemin.relative_to(RACINE)
        for extension in EXTENSIONS_DE_DONNEES
        for chemin in _fichiers(extension)
    ]
    assert not trouves, f"fichiers de donnees hors de data/ : {trouves}"


def test_env_example_ne_contient_aucune_valeur_de_secret() -> None:
    for ligne in (RACINE / ".env.example").read_text(encoding="utf-8").splitlines():
        if not ligne.strip() or ligne.lstrip().startswith("#") or "=" not in ligne:
            continue
        _, _, valeur = ligne.partition("=")
        for motif in MOTIFS_DE_SECRET:
            assert not motif.search(valeur), f".env.example ressemble a un secret : {ligne}"


def test_le_fichier_env_reel_n_est_pas_versionne() -> None:
    assert not (RACINE / ".env").exists() or ".env" in (RACINE / ".gitignore").read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Nomenclature — CLAUDE.md §10
# --------------------------------------------------------------------------- #
def test_aucun_nom_de_fichier_fourre_tout() -> None:
    trouves = [
        chemin.relative_to(RACINE)
        for chemin in _fichiers(".py")
        if chemin.stem.lower() in NOMS_FOURRE_TOUT
    ]
    assert not trouves, f"noms interdits (CLAUDE.md §10) : {trouves}"


def test_aucun_suffixe_de_version_dans_un_nom_de_fichier() -> None:
    motif = re.compile(r"_v\d+$")
    trouves = [
        chemin.relative_to(RACINE)
        for chemin in _fichiers(".py") + _fichiers(".sql")
        if motif.search(chemin.stem)
    ]
    assert not trouves, f"le versionnement est le role de git, pas du nom : {trouves}"


def test_les_fichiers_de_test_suivent_la_convention() -> None:
    trouves = [
        chemin.name
        for chemin in (RACINE / "tests").glob("*.py")
        if chemin.name != "__init__.py" and not chemin.name.startswith("test_")
    ]
    assert not trouves, f"tests/ ne doit contenir que des test_*.py : {trouves}"


# --------------------------------------------------------------------------- #
# Variables d'environnement — CLAUDE.md §10
# --------------------------------------------------------------------------- #
def _variables_declarees() -> set[str]:
    contenu = (RACINE / ".env.example").read_text(encoding="utf-8")
    return {
        ligne.split("=", 1)[0].strip()
        for ligne in contenu.splitlines()
        if "=" in ligne and not ligne.lstrip().startswith("#")
    }


def _variables_utilisees() -> set[str]:
    utilisees: set[str] = set()
    for dossier in DOSSIERS_DE_CODE:
        racine_dossier = RACINE / dossier
        if not racine_dossier.exists():
            continue
        for chemin in racine_dossier.rglob("*.py"):
            if DOSSIERS_IGNORES & set(chemin.parts):
                continue
            utilisees |= set(MOTIF_VARIABLE_ENV.findall(chemin.read_text(encoding="utf-8")))
    return utilisees


def test_toute_variable_env_utilisee_est_documentee() -> None:
    manquantes = _variables_utilisees() - _variables_declarees()
    assert not manquantes, (
        f"variables absentes de .env.example (CLAUDE.md §10) : {sorted(manquantes)}"
    )


# --------------------------------------------------------------------------- #
# Arborescence — CLAUDE.md §4
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "chemin",
    [
        "CLAUDE.md",
        "PLAN.md",
        "README.md",
        "Makefile",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".sqlfluff",
        "src/anamnese",
        "transform/models",
        "tests",
        "eval",
        "app",
        "scripts",
        "docker",
    ],
)
def test_arborescence_conforme(chemin: str) -> None:
    assert (RACINE / chemin).exists(), f"{chemin} manquant (CLAUDE.md §4)"
