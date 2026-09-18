"""Tests du catalogue : seuls les marts, avec leurs types reels et leurs descriptions.

Les artefacts dbt sont simules dans un dossier temporaire : ni warehouse ni dbt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from anamnese.catalog import CatalogueError, load_catalog


def _noeud(nom: str, dossier: str, colonnes: dict[str, str], description: str = "") -> dict:
    return {
        "resource_type": "model",
        "name": nom,
        "alias": nom,
        "fqn": ["anamnese", dossier, nom],
        "description": description,
        "columns": {colonne: {"description": texte} for colonne, texte in colonnes.items()},
    }


def _types(*colonnes: tuple[str, str]) -> dict[str, Any]:
    return {
        "columns": {
            nom: {"name": nom, "type": type_sql, "index": rang}
            for rang, (nom, type_sql) in enumerate(colonnes, start=1)
        }
    }


@pytest.fixture
def dossier_target(tmp_path: Path) -> Path:
    manifest = {
        "nodes": {
            "model.anamnese.dim_patient": _noeud(
                "dim_patient",
                "marts",
                {"sexe": "'M' ou 'F'.", "subject_id": "Cle primaire."},
                description="Une ligne par patient.",
            ),
            "model.anamnese.stg_hosp__patients": _noeud(
                "stg_hosp__patients", "staging", {"subject_id": "Interne."}
            ),
            "test.anamnese.unique_dim_patient": {
                "resource_type": "test",
                "name": "unique_dim_patient",
                "fqn": ["anamnese", "marts", "unique_dim_patient"],
            },
        }
    }
    catalogue = {
        "nodes": {
            # Ordre de la table volontairement different de celui du YAML.
            "model.anamnese.dim_patient": _types(
                ("subject_id", "BIGINT"), ("sexe", "VARCHAR"), ("nb_sejours", "BIGINT")
            ),
            "model.anamnese.stg_hosp__patients": _types(("subject_id", "BIGINT")),
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(catalogue), encoding="utf-8")
    return tmp_path


def test_ne_retient_que_les_modeles_du_dossier_marts(dossier_target: Path) -> None:
    catalogue = load_catalog(dossier_target)
    assert catalogue.tables_autorisees == {"dim_patient"}


def test_prend_les_types_reels_dans_l_ordre_de_la_table(dossier_target: Path) -> None:
    (table,) = load_catalog(dossier_target).tables
    assert [(colonne.nom, colonne.type_sql) for colonne in table.colonnes] == [
        ("subject_id", "BIGINT"),
        ("sexe", "VARCHAR"),
        ("nb_sejours", "BIGINT"),
    ]


def test_prend_les_descriptions_dans_le_manifest(dossier_target: Path) -> None:
    (table,) = load_catalog(dossier_target).tables
    descriptions = {colonne.nom: colonne.description for colonne in table.colonnes}
    assert table.description == "Une ligne par patient."
    assert descriptions["sexe"] == "'M' ou 'F'."
    assert descriptions["nb_sejours"] == ""  # colonne non documentee : vide, pas d'erreur


def test_le_contexte_decrit_tables_colonnes_types_et_descriptions(dossier_target: Path) -> None:
    contexte = load_catalog(dossier_target).build_context()
    assert "### dim_patient" in contexte
    assert "Une ligne par patient." in contexte
    assert "- sexe (VARCHAR): 'M' ou 'F'." in contexte
    assert "stg_hosp__patients" not in contexte


@pytest.mark.parametrize("fichier", ["manifest.json", "catalog.json"])
def test_signale_un_artefact_dbt_absent(dossier_target: Path, fichier: str) -> None:
    (dossier_target / fichier).unlink()
    with pytest.raises(CatalogueError, match="make docs"):
        load_catalog(dossier_target)


def test_signale_un_artefact_dbt_illisible(dossier_target: Path) -> None:
    (dossier_target / "manifest.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(CatalogueError, match="illisible"):
        load_catalog(dossier_target)


def test_signale_un_mart_absent_du_catalogue_dbt(dossier_target: Path) -> None:
    (dossier_target / "catalog.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    with pytest.raises(CatalogueError, match=r"dim_patient absent de catalog\.json"):
        load_catalog(dossier_target)


def test_signale_un_manifest_sans_mart(dossier_target: Path) -> None:
    (dossier_target / "manifest.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    with pytest.raises(CatalogueError, match="aucun mart"):
        load_catalog(dossier_target)
