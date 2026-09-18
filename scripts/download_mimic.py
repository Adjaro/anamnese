"""Telecharge MIMIC-IV demo 2.2 depuis PhysioNet, puis inventorie les CSV reels.

Usage : `make data`.

Le telechargement est idempotent. Un fichier present avec la taille annoncee par
le serveur n'est pas retelecharge ; un fichier interrompu reprend la ou il s'est
arrete (`<nom>.part` et en-tete `Range`). Chaque fichier telecharge est verifie
contre la somme SHA-256 publiee par PhysioNet avant d'etre mis en place.

L'inventaire `data/raw/_inventory.md` est ensuite reconstruit a partir des
fichiers : colonnes, types inferes par DuckDB sur le fichier entier, nombre de
lignes. C'est la reference de tous les modeles dbt (CLAUDE.md §2, regle 1).
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import duckdb
import requests
from tqdm import tqdm

URL_BASE = "https://physionet.org/files/mimic-iv-demo/2.2/"
FICHIER_SOMMES = "SHA256SUMS.txt"

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_RAW = RACINE / "data" / "raw"
FICHIER_INVENTAIRE = DOSSIER_RAW / "_inventory.md"
MODULES_INVENTORIES = ("hosp", "icu")

TIMEOUT_SECONDES = 60
TAILLE_BLOC_OCTETS = 1 << 16


class TelechargementError(RuntimeError):
    """Fichier distant absent, tronque ou dont la somme SHA-256 ne correspond pas."""


@dataclass(frozen=True)
class FichierDistant:
    """Un fichier publie par PhysioNet, repere par son chemin relatif a `URL_BASE`."""

    chemin: str
    sha256: str

    @property
    def url(self) -> str:
        return URL_BASE + self.chemin

    @property
    def destination(self) -> Path:
        return DOSSIER_RAW / self.chemin


# --------------------------------------------------------------------------- #
# Telechargement
# --------------------------------------------------------------------------- #
def _lire_catalogue_distant(session: requests.Session) -> list[FichierDistant]:
    """Liste des fichiers publies, lue dans le SHA256SUMS.txt de PhysioNet."""
    reponse = session.get(URL_BASE + FICHIER_SOMMES, timeout=TIMEOUT_SECONDES)
    reponse.raise_for_status()
    fichiers = []
    for ligne in reponse.text.splitlines():
        if not ligne.strip():
            continue
        somme, chemin = ligne.split(maxsplit=1)
        chemin_relatif = PurePosixPath(chemin.strip())
        if chemin_relatif.is_absolute() or ".." in chemin_relatif.parts:
            raise TelechargementError(f"chemin refuse dans {FICHIER_SOMMES} : {chemin}")
        fichiers.append(FichierDistant(chemin=chemin_relatif.as_posix(), sha256=somme.lower()))
    if not fichiers:
        raise TelechargementError(f"{FICHIER_SOMMES} est vide")
    return fichiers


def _lire_taille_distante(session: requests.Session, fichier: FichierDistant) -> int:
    reponse = session.head(fichier.url, allow_redirects=True, timeout=TIMEOUT_SECONDES)
    reponse.raise_for_status()
    taille = reponse.headers.get("Content-Length")
    if taille is None:
        raise TelechargementError(f"taille inconnue pour {fichier.chemin}")
    return int(taille)


def _calculer_sha256(chemin: Path) -> str:
    empreinte = hashlib.sha256()
    with chemin.open("rb") as entree:
        for bloc in iter(lambda: entree.read(TAILLE_BLOC_OCTETS), b""):
            empreinte.update(bloc)
    return empreinte.hexdigest()


def _telecharger_fichier(
    session: requests.Session, fichier: FichierDistant, taille: int, barre: tqdm
) -> None:
    """Telecharge vers `<nom>.part` en reprenant l'existant, verifie, puis renomme."""
    destination = fichier.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_name(destination.name + ".part")

    deja_recu = partiel.stat().st_size if partiel.exists() else 0
    if deja_recu > taille:
        partiel.unlink()
        deja_recu = 0

    if deja_recu < taille:
        entetes = {"Range": f"bytes={deja_recu}-"} if deja_recu else {}
        with session.get(
            fichier.url, headers=entetes, stream=True, timeout=TIMEOUT_SECONDES
        ) as reponse:
            reponse.raise_for_status()
            # Un serveur qui ignore Range renvoie 200 et le fichier entier.
            if deja_recu and reponse.status_code != requests.codes.partial_content:
                deja_recu = 0
            barre.update(deja_recu)
            with partiel.open("ab" if deja_recu else "wb") as sortie:
                for bloc in reponse.iter_content(TAILLE_BLOC_OCTETS):
                    sortie.write(bloc)
                    barre.update(len(bloc))
    else:
        barre.update(deja_recu)

    somme = _calculer_sha256(partiel)
    if somme != fichier.sha256:
        partiel.unlink()
        raise TelechargementError(
            f"somme SHA-256 invalide pour {fichier.chemin} : {somme} au lieu de {fichier.sha256}"
        )
    partiel.replace(destination)


def download_mimic() -> None:
    """Met `data/raw/` en conformite avec la publication PhysioNet."""
    with requests.Session() as session:
        catalogue = _lire_catalogue_distant(session)
        a_telecharger: list[tuple[FichierDistant, int]] = []
        for fichier in catalogue:
            taille = _lire_taille_distante(session, fichier)
            if fichier.destination.exists() and fichier.destination.stat().st_size == taille:
                continue
            a_telecharger.append((fichier, taille))

        print(
            f"{len(catalogue)} fichiers publies, "
            f"{len(catalogue) - len(a_telecharger)} deja presents, "
            f"{len(a_telecharger)} a telecharger"
        )
        if not a_telecharger:
            return

        with tqdm(
            total=sum(taille for _, taille in a_telecharger),
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="MIMIC-IV demo",
        ) as barre:
            for fichier, taille in a_telecharger:
                barre.set_postfix_str(fichier.chemin)
                _telecharger_fichier(session, fichier, taille, barre)


# --------------------------------------------------------------------------- #
# Inventaire
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TableInventoriee:
    nom: str
    nb_lignes: int
    colonnes: list[tuple[str, str]]


def _inventorier_csv(connexion: duckdb.DuckDBPyConnection, chemin: Path) -> TableInventoriee:
    # sample_size=-1 : inference sur le fichier entier, pas sur un echantillon.
    relation = connexion.read_csv(str(chemin), sample_size=-1)
    (nb_lignes,) = relation.aggregate("count(*)").fetchone()
    colonnes = [
        (nom, str(type_)) for nom, type_ in zip(relation.columns, relation.types, strict=True)
    ]
    nom = chemin.relative_to(DOSSIER_RAW).as_posix().removesuffix(".csv.gz")
    return TableInventoriee(nom=nom, nb_lignes=nb_lignes, colonnes=colonnes)


def _formater_entier(valeur: int) -> str:
    return f"{valeur:,}".replace(",", " ")


def _formater_inventaire(tables: list[TableInventoriee]) -> str:
    genere_le = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lignes = [
        "# Inventaire MIMIC-IV demo 2.2",
        "",
        f"Genere par `scripts/download_mimic.py` le {genere_le}, DuckDB {duckdb.__version__}.",
        "Ne pas editer a la main : relancer `make data`.",
        "",
        "Types **inferes** par `read_csv` sur le fichier entier (`sample_size=-1`).",
        "Ce sont des constats, pas des contrats : les sources dbt declarent leurs types",
        "explicitement (CLAUDE.md §7). Les dates sont decalees par la desidentification.",
        "",
        "## Sommaire",
        "",
        "| Table | Lignes | Colonnes |",
        "|---|---:|---:|",
    ]
    lignes += [
        f"| `{table.nom}` | {_formater_entier(table.nb_lignes)} | {len(table.colonnes)} |"
        for table in tables
    ]
    for table in tables:
        lignes += [
            "",
            f"## `{table.nom}`",
            "",
            f"{_formater_entier(table.nb_lignes)} lignes.",
            "",
            "| # | Colonne | Type infere |",
            "|---:|---|---|",
        ]
        lignes += [
            f"| {rang} | `{nom}` | `{type_}` |"
            for rang, (nom, type_) in enumerate(table.colonnes, start=1)
        ]
    return "\n".join(lignes) + "\n"


def write_inventory() -> Path:
    """Reecrit `data/raw/_inventory.md` a partir des CSV reellement presents."""
    chemins = sorted(
        chemin
        for module in MODULES_INVENTORIES
        for chemin in (DOSSIER_RAW / module).glob("*.csv.gz")
    )
    if not chemins:
        raise TelechargementError(f"aucun CSV dans {DOSSIER_RAW} : telechargement incomplet")
    with duckdb.connect() as connexion:
        tables = [
            _inventorier_csv(connexion, chemin) for chemin in tqdm(chemins, desc="inventaire")
        ]
    FICHIER_INVENTAIRE.write_text(_formater_inventaire(tables), encoding="utf-8")
    return FICHIER_INVENTAIRE


def main() -> int:
    DOSSIER_RAW.mkdir(parents=True, exist_ok=True)
    try:
        download_mimic()
        inventaire = write_inventory()
    except (requests.RequestException, TelechargementError) as erreur:
        print(f"echec : {erreur}", file=sys.stderr)
        return 1
    print(f"inventaire ecrit : {inventaire.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
