"""Reconstruit le warehouse DuckDB sans jamais exposer un fichier a moitie construit.

Usage : `make build`.

dbt ecrit dans `data/warehouse/.build/anamnese.duckdb` ; si le build et ses tests
passent, ce fichier remplace `anamnese.duckdb` par un rename atomique (CLAUDE.md §7).
Le fichier temporaire garde le meme nom dans un dossier voisin : dbt-duckdb deduit
le nom du catalogue du nom de fichier, et un suffixe `.tmp` le casserait.
Un lecteur deja ouvert, comme l'API, garde l'ancienne version jusqu'a sa prochaine
ouverture ; un build en echec laisse le warehouse en place intact.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_DBT = RACINE / "transform"
WAREHOUSE = RACINE / "data" / "warehouse" / "anamnese.duckdb"
# Meme systeme de fichiers que WAREHOUSE : condition de l'atomicite du rename.
WAREHOUSE_TEMPORAIRE = WAREHOUSE.parent / ".build" / WAREHOUSE.name

# dbt vient du groupe uv `dbt`, installe dans le meme environnement que ce script.
EXECUTABLE_DBT = Path(sys.executable).parent / "dbt"


class ConstructionError(RuntimeError):
    """Build dbt en echec, ou warehouse en cours d'ecriture par un autre processus."""


def _chemin_wal(base: Path) -> Path:
    return base.with_name(base.name + ".wal")


def _supprimer_temporaire() -> None:
    WAREHOUSE_TEMPORAIRE.unlink(missing_ok=True)
    _chemin_wal(WAREHOUSE_TEMPORAIRE).unlink(missing_ok=True)


def _lancer_dbt(*sous_commande: str) -> None:
    environnement = os.environ | {"ANAMNESE_DBT_DUCKDB_PATH": str(WAREHOUSE_TEMPORAIRE)}
    commande = [
        str(EXECUTABLE_DBT),
        *sous_commande,
        "--project-dir",
        str(DOSSIER_DBT),
        "--profiles-dir",
        str(DOSSIER_DBT),
    ]
    # Commande et arguments fixes, aucune entree exterieure : pas d'injection possible.
    resultat = subprocess.run(commande, cwd=DOSSIER_DBT, env=environnement, check=False)  # noqa: S603
    if resultat.returncode != 0:
        raise ConstructionError(
            f"dbt {' '.join(sous_commande)} a echoue (code {resultat.returncode})"
        )


def _construire_temporaire() -> None:
    """Build et tests dbt, puis catalogue des types, sur le fichier temporaire.

    Le catalogue (catalog.json) est produit avant le swap : l'API lit ainsi un
    contexte qui correspond exactement au warehouse mis en place, et `docs
    generate` n'ouvre jamais en ecriture le fichier que l'API lit.
    """
    _lancer_dbt("build")
    _lancer_dbt("docs", "generate")


def _consolider_temporaire() -> None:
    """Integre un eventuel WAL au fichier : seul le .duckdb est deplace par le swap."""
    with duckdb.connect(str(WAREHOUSE_TEMPORAIRE)) as connexion:
        connexion.execute("checkpoint")
    if _chemin_wal(WAREHOUSE_TEMPORAIRE).exists():
        raise ConstructionError(f"WAL residuel apres checkpoint : {WAREHOUSE_TEMPORAIRE}")


def swap_warehouse() -> Path:
    """Construit le warehouse dans un fichier temporaire puis le met en place atomiquement."""
    if not EXECUTABLE_DBT.exists():
        raise ConstructionError("dbt introuvable : lancer `uv sync --all-groups`")
    # Un WAL a cote du warehouse signale un ecrivain actif (CLAUDE.md §7 : un seul
    # ecrivain) ; il serait rejoue sur le nouveau fichier et le corromprait.
    if _chemin_wal(WAREHOUSE).exists():
        raise ConstructionError(f"{_chemin_wal(WAREHOUSE).name} present : un processus ecrit")

    WAREHOUSE_TEMPORAIRE.parent.mkdir(parents=True, exist_ok=True)
    _supprimer_temporaire()
    try:
        _construire_temporaire()
        _consolider_temporaire()
    except (ConstructionError, duckdb.Error):
        _supprimer_temporaire()
        raise
    # Path.replace est os.replace : rename atomique sur un meme systeme de fichiers.
    WAREHOUSE_TEMPORAIRE.replace(WAREHOUSE)
    return WAREHOUSE


def main() -> int:
    try:
        warehouse = swap_warehouse()
    except (ConstructionError, duckdb.Error) as erreur:
        print(f"echec, warehouse inchange : {erreur}", file=sys.stderr)
        return 1
    print(f"warehouse en place : {warehouse.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
