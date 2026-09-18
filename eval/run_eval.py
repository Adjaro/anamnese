"""Rejoue le jeu d'evaluation NL2SQL et ecrit un rapport (CLAUDE.md §9).

Usage :
    make eval                                        # modele de MISTRAL_MODEL
    make eval EVAL_ARGS="--modeles ministral-3b-latest,codestral-latest"

Pour chaque question, le SQL attendu et le moteur complet (LLM, garde-fous,
relances) sont executes, puis leurs RESULTATS compares : colonnes et lignes dans
n'importe quel ordre, sauf pour les questions `tri`. Le resultat genere peut
porter des colonnes en plus. Une question `ambigue` (sql_attendu nul) est reussie
si le moteur refuse d'y repondre.

Le rapport (eval/reports/, non versionne) donne le taux global, le taux par tag,
les echecs detailles, le modele et le cout. Code de sortie 1 si un appel au LLM a
echoue : le taux mesure alors l'API, pas le modele.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import yaml

from anamnese.catalog import CatalogueError
from anamnese.config import get_settings
from anamnese.engine import (
    QueryExecutionError,
    QuestionError,
    WarehouseUnavailableError,
    configure_logging,
    create_engine,
)
from anamnese.guardrails import SQLValidationError
from anamnese.llm import LLMError

RACINE = Path(__file__).resolve().parent.parent
FICHIER_QUESTIONS = RACINE / "eval" / "questions.yml"
DOSSIER_RAPPORTS = RACINE / "eval" / "reports"

# Prix en USD par million de tokens (entree, sortie), releves le 2026-09-19 sur
# https://mistral.ai/pricing/api. A mettre a jour avec la grille publiee.
PRIX_PAR_MILLION: dict[str, tuple[float, float]] = {
    "codestral-latest": (0.3, 0.9),
    "ministral-3b-latest": (0.1, 0.1),
    "ministral-8b-latest": (0.15, 0.15),
    "ministral-14b-latest": (0.2, 0.2),
    "mistral-small-latest": (0.15, 0.6),
    "mistral-medium-latest": (1.5, 7.5),
    "mistral-large-latest": (0.5, 1.5),
}

# Tolerance de comparaison des nombres : les arrondis a 2 decimales du SQL
# attendu et du SQL genere peuvent differer d'une unite sur la derniere.
TOLERANCE_ABSOLUE = 0.011
TOLERANCE_RELATIVE = 1e-3


@dataclass
class Verdict:
    id: str
    question: str
    tags: list[str]
    reussi: bool
    raison: str = ""
    sql_attendu: str | None = None
    sql_genere: str | None = None
    tentatives: int = 0
    duree_ms: int = 0
    erreur_llm: bool = False


@dataclass
class Campagne:
    modele: str
    verdicts: list[Verdict] = field(default_factory=list)
    tokens_prompt: int = 0
    tokens_reponse: int = 0
    duree_s: float = 0.0

    @property
    def taux(self) -> float:
        return sum(verdict.reussi for verdict in self.verdicts) / len(self.verdicts)

    @property
    def cout_usd(self) -> float | None:
        prix = PRIX_PAR_MILLION.get(self.modele)
        if prix is None:
            return None
        return (self.tokens_prompt * prix[0] + self.tokens_reponse * prix[1]) / 1_000_000

    def taux_par_tag(self) -> dict[str, tuple[int, int]]:
        compte: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for verdict in self.verdicts:
            for tag in verdict.tags:
                compte[tag][0] += verdict.reussi
                compte[tag][1] += 1
        return {tag: (reussis, total) for tag, (reussis, total) in sorted(compte.items())}


# --------------------------------------------------------------------------- #
# Comparaison des resultats
# --------------------------------------------------------------------------- #
def _normaliser(valeur: Any) -> Any:
    if isinstance(valeur, bool):
        return int(valeur)
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, datetime | date):
        return valeur.isoformat()
    return valeur


def _egales(gauche: Any, droite: Any) -> bool:
    if isinstance(gauche, int | float) and isinstance(droite, int | float):
        return math.isclose(gauche, droite, rel_tol=TOLERANCE_RELATIVE, abs_tol=TOLERANCE_ABSOLUE)
    return gauche == droite


def _cle_de_tri(valeur: Any) -> tuple[int, Any]:
    if valeur is None:
        return (0, 0)
    if isinstance(valeur, int | float):
        return (1, round(valeur, 1))
    return (2, str(valeur))


def _colonnes_egales(attendue: list[Any], generee: list[Any]) -> bool:
    return all(
        _egales(a, g)
        for a, g in zip(
            sorted(attendue, key=_cle_de_tri), sorted(generee, key=_cle_de_tri), strict=True
        )
    )


def compare_results(
    attendu: list[tuple[Any, ...]], genere: list[tuple[Any, ...]], ordonne: bool
) -> str | None:
    """None si les resultats concordent, sinon la raison de l'ecart."""
    attendu = [tuple(_normaliser(v) for v in ligne) for ligne in attendu]
    genere = [tuple(_normaliser(v) for v in ligne) for ligne in genere]
    if len(attendu) != len(genere):
        return f"{len(genere)} ligne(s) au lieu de {len(attendu)}"
    if not attendu:
        return None

    # Associe chaque colonne attendue a une colonne generee de memes valeurs :
    # les alias et l'ordre des colonnes sont libres, des colonnes en plus toleres.
    colonnes_generees = [list(colonne) for colonne in zip(*genere, strict=True)]
    libres = set(range(len(colonnes_generees)))
    correspondance = []
    for rang, colonne in enumerate(zip(*attendu, strict=True)):
        trouvee = next(
            (i for i in sorted(libres) if _colonnes_egales(list(colonne), colonnes_generees[i])),
            None,
        )
        if trouvee is None:
            return f"aucune colonne generee ne correspond a la colonne attendue n°{rang + 1}"
        libres.remove(trouvee)
        correspondance.append(trouvee)

    projete = [tuple(ligne[i] for i in correspondance) for ligne in genere]
    if not ordonne:
        attendu = sorted(attendu, key=lambda ligne: [_cle_de_tri(v) for v in ligne])
        projete = sorted(projete, key=lambda ligne: [_cle_de_tri(v) for v in ligne])
    for ligne_attendue, ligne_generee in zip(attendu, projete, strict=True):
        if not all(_egales(a, g) for a, g in zip(ligne_attendue, ligne_generee, strict=True)):
            return f"ligne {ligne_generee} au lieu de {ligne_attendue}" + (
                " (ordre compare)" if ordonne else ""
            )
    return None


# --------------------------------------------------------------------------- #
# Campagne
# --------------------------------------------------------------------------- #
def _executer_attendu(connexion: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    return connexion.execute(sql).fetchall()


def run_campaign(modele: str, questions: list[dict[str, Any]]) -> Campagne:
    settings = get_settings().model_copy(update={"mistral_model": modele})
    moteur = create_engine(settings)
    campagne = Campagne(modele=modele)
    connexion = duckdb.connect(
        str(settings.duckdb_path), read_only=True, config={"enable_external_access": False}
    )
    debut = time.perf_counter()
    for question in questions:
        verdict = Verdict(
            id=question["id"],
            question=question["question"],
            tags=question["tags"],
            reussi=False,
            sql_attendu=question["sql_attendu"],
        )
        ambigue = question["sql_attendu"] is None
        try:
            resultat = moteur.ask(question["question"])
        except QuestionError as erreur:
            verdict.reussi = ambigue
            verdict.raison = "" if ambigue else f"refus du moteur : {erreur}"
        except (SQLValidationError, QueryExecutionError) as erreur:
            verdict.raison = f"{type(erreur).__name__} apres relances : {erreur}"
        except LLMError as erreur:
            verdict.raison, verdict.erreur_llm = f"LLMError : {erreur}", True
        else:
            verdict.sql_genere = resultat.sql
            verdict.tentatives = resultat.tentatives
            verdict.duree_ms = resultat.duree_ms
            campagne.tokens_prompt += resultat.tokens_prompt
            campagne.tokens_reponse += resultat.tokens_reponse
            if ambigue:
                verdict.raison = "a repondu a une question qui appelait un refus"
            else:
                ecart = compare_results(
                    _executer_attendu(connexion, question["sql_attendu"]),
                    resultat.lignes,
                    ordonne="tri" in question["tags"],
                )
                verdict.reussi = ecart is None
                verdict.raison = ecart or ""
        campagne.verdicts.append(verdict)
        print(f"  {modele} {verdict.id} {'OK ' if verdict.reussi else 'KO '} {verdict.raison[:90]}")
    connexion.close()
    campagne.duree_s = time.perf_counter() - debut
    return campagne


# --------------------------------------------------------------------------- #
# Rapport
# --------------------------------------------------------------------------- #
def _pourcentage(reussis: int, total: int) -> str:
    return f"{100 * reussis / total:.0f} % ({reussis}/{total})"


def _formater_cout(campagne: Campagne) -> str:
    cout = campagne.cout_usd
    return "prix inconnu" if cout is None else f"{cout:.4f} $"


def format_report(campagnes: list[Campagne]) -> str:
    lignes = [
        "# Evaluation NL2SQL",
        "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC, {len(campagnes[0].verdicts)} questions.",
        "",
        "| Modele | Reussite | Tokens entree | Tokens sortie | Cout | Duree |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for campagne in campagnes:
        reussis = sum(verdict.reussi for verdict in campagne.verdicts)
        lignes.append(
            f"| `{campagne.modele}` | {_pourcentage(reussis, len(campagne.verdicts))} "
            f"| {campagne.tokens_prompt:,} | {campagne.tokens_reponse:,} "
            f"| {_formater_cout(campagne)} | {campagne.duree_s:.0f} s |".replace(",", " ")
        )

    tags = sorted({tag for campagne in campagnes for tag in campagne.taux_par_tag()})
    lignes += [
        "",
        "## Taux par tag",
        "",
        "| Tag | " + " | ".join(f"`{campagne.modele}`" for campagne in campagnes) + " |",
        "|---|" + "---:|" * len(campagnes),
    ]
    for tag in tags:
        cellules = [_pourcentage(*campagne.taux_par_tag()[tag]) for campagne in campagnes]
        lignes.append(f"| {tag} | " + " | ".join(cellules) + " |")

    for campagne in campagnes:
        echecs = [verdict for verdict in campagne.verdicts if not verdict.reussi]
        lignes += ["", f"## Echecs de `{campagne.modele}` ({len(echecs)})"]
        for verdict in echecs:
            lignes += [
                "",
                f"### {verdict.id} — {verdict.question}",
                "",
                f"- Tags : {', '.join(verdict.tags)}",
                f"- Raison : {verdict.raison}",
            ]
            for titre, sql in (
                ("SQL attendu", verdict.sql_attendu),
                ("SQL genere", verdict.sql_genere),
            ):
                if sql:
                    lignes += ["", f"{titre} :", "", "```sql", sql.strip(), "```"]
    return "\n".join(lignes) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--modeles",
        default=get_settings().mistral_model,
        help="modeles Mistral separes par des virgules (defaut : MISTRAL_MODEL)",
    )
    parser.add_argument("--ids", default="", help="ids de questions separes par des virgules")
    arguments = parser.parse_args()

    configure_logging("ERROR")
    questions = yaml.safe_load(FICHIER_QUESTIONS.read_text(encoding="utf-8"))
    if arguments.ids:
        retenues = set(arguments.ids.split(","))
        questions = [question for question in questions if question["id"] in retenues]

    campagnes = []
    try:
        for modele in arguments.modeles.split(","):
            print(f"== {modele}")
            campagnes.append(run_campaign(modele.strip(), questions))
    except (WarehouseUnavailableError, CatalogueError, LLMError) as erreur:
        print(f"echec : {type(erreur).__name__} : {erreur}", file=sys.stderr)
        return 1

    DOSSIER_RAPPORTS.mkdir(parents=True, exist_ok=True)
    rapport = DOSSIER_RAPPORTS / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.md"
    rapport.write_text(format_report(campagnes), encoding="utf-8")
    print()
    for campagne in campagnes:
        print(f"{campagne.modele:24} {campagne.taux:6.1%}  cout {_formater_cout(campagne)}")
    print(f"rapport : {rapport.relative_to(RACINE)}")
    erreurs_llm = any(verdict.erreur_llm for campagne in campagnes for verdict in campagne.verdicts)
    return 1 if erreurs_llm else 0


if __name__ == "__main__":
    sys.exit(main())
