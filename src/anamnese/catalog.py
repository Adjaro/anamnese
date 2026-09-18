"""Catalogue des marts : contexte envoye au LLM et liste blanche des garde-fous.

Lu dans les artefacts de `dbt docs generate` (`make docs`) :
- `manifest.json` pour la liste des marts et leurs descriptions ;
- `catalog.json` pour les types reels des colonnes, dans l'ordre de la table.

Le LLM ne voit que ces metadonnees, jamais une ligne de donnees (CLAUDE.md §2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOSSIER_MARTS = "marts"


class CatalogueError(RuntimeError):
    """Artefacts dbt absents, illisibles ou sans aucun mart."""


@dataclass(frozen=True)
class ColonneCatalogue:
    nom: str
    type_sql: str
    description: str


@dataclass(frozen=True)
class TableCatalogue:
    nom: str
    description: str
    colonnes: tuple[ColonneCatalogue, ...]


@dataclass(frozen=True)
class Catalogue:
    tables: tuple[TableCatalogue, ...]

    @property
    def tables_autorisees(self) -> frozenset[str]:
        """Liste blanche transmise a `guardrails.validate`."""
        return frozenset(table.nom for table in self.tables)

    def build_context(self) -> str:
        """Schema au format texte, injecte dans le prompt systeme."""
        blocs = []
        for table in self.tables:
            lignes = [f"### {table.nom}", table.description, "", "Columns:"]
            lignes += [
                f"- {colonne.nom} ({colonne.type_sql}): {colonne.description}"
                for colonne in table.colonnes
            ]
            blocs.append("\n".join(lignes))
        return "\n\n".join(blocs)


def _lire_json(chemin: Path) -> dict[str, Any]:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError as erreur:
        raise CatalogueError(f"{chemin} introuvable : lancer `make docs`") from erreur
    except json.JSONDecodeError as erreur:
        raise CatalogueError(f"{chemin} illisible : {erreur}") from erreur


def _is_mart(noeud: dict[str, Any]) -> bool:
    # fqn = [projet, dossier, ..., modele] : un mart vit sous models/marts/.
    return noeud.get("resource_type") == "model" and DOSSIER_MARTS in noeud["fqn"][1:-1]


def _construire_table(noeud: dict[str, Any], types: dict[str, Any]) -> TableCatalogue:
    descriptions = {
        nom.lower(): colonne.get("description", "").strip()
        for nom, colonne in noeud.get("columns", {}).items()
    }
    colonnes_reelles = sorted(types.values(), key=lambda colonne: colonne["index"])
    colonnes = tuple(
        ColonneCatalogue(
            nom=colonne["name"],
            type_sql=colonne["type"],
            description=descriptions.get(colonne["name"].lower(), ""),
        )
        for colonne in colonnes_reelles
    )
    return TableCatalogue(
        nom=noeud.get("alias") or noeud["name"],
        description=noeud.get("description", "").strip(),
        colonnes=colonnes,
    )


def load_catalog(dossier_target: Path) -> Catalogue:
    """Construit le catalogue des marts a partir des artefacts dbt."""
    manifest = _lire_json(dossier_target / "manifest.json")
    catalogue_dbt = _lire_json(dossier_target / "catalog.json")

    tables = []
    for identifiant, noeud in sorted(manifest.get("nodes", {}).items()):
        if not _is_mart(noeud):
            continue
        types = catalogue_dbt.get("nodes", {}).get(identifiant, {}).get("columns")
        if not types:
            raise CatalogueError(
                f"{noeud['name']} absent de catalog.json : relancer `make build` puis `make docs`"
            )
        tables.append(_construire_table(noeud, types))

    if not tables:
        raise CatalogueError(f"aucun mart dans {dossier_target / 'manifest.json'}")
    return Catalogue(tables=tuple(tables))
