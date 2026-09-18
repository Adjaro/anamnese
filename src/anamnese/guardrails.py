"""Validation du SQL genere par le LLM avant toute execution (CLAUDE.md §3).

Seul module du projet ou un bug est une faille : toute requete qui n'est pas
prouvee inoffensive est rejetee. La liste blanche de tables est la defense
principale ; l'execution en `read_only` sans acces externe est la seconde.

`validate` accepte une seule requete de lecture (SELECT ou UNION) ne lisant que
des tables autorisees ou des CTE qu'elle declare, et renvoie le SQL normalise
par sqlglot, jamais la chaine brute du LLM.
"""

from __future__ import annotations

from collections.abc import Collection

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECTE = "duckdb"

# Racines acceptees : une requete de lecture, simple ou composee par UNION.
RACINES_AUTORISEES = (exp.Select, exp.Union)

# Noeuds interdits n'importe ou dans l'arbre. Au-dela de la specification :
# `Into` (SELECT ... INTO cree une table), et toute instruction de session,
# d'administration ou d'acces fichier que sqlglot sait reconnaitre.
NOEUDS_INTERDITS: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Into,
    exp.Merge,
    exp.Copy,
    exp.Attach,
    exp.Detach,
    exp.Install,
    exp.Pragma,
    exp.Set,
    exp.Use,
    exp.Export,
    exp.TruncateTable,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Kill,
    exp.Describe,
    exp.Summarize,
)

# Fonctions d'acces au systeme de fichiers, au reseau, a l'environnement ou au
# catalogue, et d'execution de SQL dynamique. Comparaison en minuscules.
FONCTIONS_INTERDITES = frozenset(
    {
        "attach",
        "copy",
        "current_setting",
        "getenv",
        "glob",
        "httpfs",
        "install",
        "load",
        "query",
        "query_table",
        "sniff_csv",
    }
)
PREFIXES_FONCTIONS_INTERDITES = (
    "read_",  # read_csv, read_csv_auto, read_parquet, read_json, read_text, read_blob...
    "parquet_",
    "duckdb_",
    "pragma_",
    "sqlite_",
    "postgres_",
    "mysql_",
    "iceberg_",
    "delta_",
)

# Seul schema accepte en qualification explicite : celui ou dbt construit les marts.
SCHEMA_AUTORISE = "main"


class SQLValidationError(ValueError):
    """SQL rejete par les garde-fous ; le message dit pourquoi, sans le SQL."""


def validate(sql: str, allowed_tables: Collection[str]) -> str:
    """Valide une requete du LLM et renvoie sa forme normalisee par sqlglot.

    Leve `SQLValidationError` a la premiere regle violee.
    """
    requete = _parser_requete_unique(sql)
    if not isinstance(requete, RACINES_AUTORISEES):
        raise SQLValidationError(
            f"seules les requetes SELECT ou UNION sont acceptees, pas {requete.key.upper()}"
        )
    _verifier_noeuds(requete)
    _verifier_fonctions(requete)
    _verifier_tables(requete, {table.lower() for table in allowed_tables})
    return requete.sql(dialect=DIALECTE, comments=False)


# --------------------------------------------------------------------------- #
# Regles
# --------------------------------------------------------------------------- #
def _parser_requete_unique(sql: str) -> exp.Expression:
    try:
        instructions = [
            instruction
            for instruction in sqlglot.parse(sql, read=DIALECTE)
            if instruction is not None
        ]
    except SqlglotError as erreur:
        raise SQLValidationError(f"SQL invalide : {erreur}") from erreur
    if not instructions:
        raise SQLValidationError("requete vide")
    if len(instructions) > 1:
        raise SQLValidationError(
            f"une seule requete est acceptee, {len(instructions)} ont ete fournies"
        )
    return instructions[0]


def _verifier_noeuds(requete: exp.Expression) -> None:
    for noeud in requete.walk():
        if isinstance(noeud, NOEUDS_INTERDITS):
            raise SQLValidationError(f"instruction interdite : {noeud.key.upper()}")


def _nom_de_fonction(fonction: exp.Func) -> str:
    if isinstance(fonction, exp.Anonymous):
        return fonction.name.lower()
    return fonction.sql_name().lower()


def _is_fonction_interdite(nom: str) -> bool:
    return nom in FONCTIONS_INTERDITES or nom.startswith(PREFIXES_FONCTIONS_INTERDITES)


def _verifier_fonctions(requete: exp.Expression) -> None:
    for fonction in requete.find_all(exp.Func):
        nom = _nom_de_fonction(fonction)
        if _is_fonction_interdite(nom):
            raise SQLValidationError(f"fonction interdite : {nom}")


def _verifier_tables(requete: exp.Expression, tables_autorisees: set[str]) -> None:
    for table in requete.find_all(exp.Table):
        # Une fonction en position de table (read_csv(...), range(...), glob(...),
        # duckdb_tables()...) n'est jamais une table de la liste blanche.
        if not isinstance(table.this, exp.Identifier):
            raise SQLValidationError("fonction en position de table interdite")
        nom = table.name.lower()
        if table.catalog or (table.db and table.db.lower() != SCHEMA_AUTORISE):
            raise SQLValidationError(f"table hors du schema {SCHEMA_AUTORISE} : {table.sql()}")
        if not table.db and _is_reference_cte(table):
            continue
        if nom not in tables_autorisees:
            raise SQLValidationError(f"table non autorisee : {nom}")


def _is_reference_cte(table: exp.Table) -> bool:
    """Vrai si `table` designe une CTE visible a son emplacement, et non une table.

    Resolution par portee, du plus proche au plus lointain : un nom de CTE ne vaut
    que dans la requete qui la declare. Dans un WITH non recursif, le corps d'une
    CTE ne voit que les CTE declarees avant elle ; il ne se voit pas lui-meme.
    Sans cela, `with secret as (select * from secret) ...` ou une CTE declaree
    dans une sous-requete masqueraient une vraie table non autorisee.
    """
    nom = table.name.lower()
    enfant: exp.Expression = table
    parent = table.parent
    while parent is not None:
        clause_with = _clause_with(parent)
        if clause_with is not None:
            ctes = list(clause_with.expressions)
            if enfant is clause_with:
                # La table est dans le corps d'une CTE de ce WITH : on cherche laquelle.
                visibles = _ctes_visibles_depuis(clause_with, ctes, table)
            else:
                visibles = ctes
            if nom in {cte.alias_or_name.lower() for cte in visibles}:
                return True
        enfant, parent = parent, parent.parent
    return False


def _clause_with(noeud: exp.Expression) -> exp.With | None:
    # Recherche par type et non par cle : sqlglot a renomme l'argument `with` en
    # `with_` au fil des versions.
    if isinstance(noeud, exp.With):
        return None
    return next((valeur for valeur in noeud.args.values() if isinstance(valeur, exp.With)), None)


def _ctes_visibles_depuis(
    clause_with: exp.With, ctes: list[exp.CTE], table: exp.Table
) -> list[exp.CTE]:
    if clause_with.args.get("recursive"):
        return ctes
    for rang, cte in enumerate(ctes):
        if table is cte or any(noeud is table for noeud in cte.walk()):
            return ctes[:rang]
    return []
