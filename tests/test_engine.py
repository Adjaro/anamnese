"""Tests du moteur avec un faux LLM : relances, erreurs typees, confidentialite.

Aucun appel reseau. Les tests d'execution utilisent le vrai warehouse
(`make build` puis `make docs`) et sont marques `needs_warehouse`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from anamnese.catalog import Catalogue, ColonneCatalogue, TableCatalogue, load_catalog
from anamnese.config import Settings
from anamnese.engine import (
    MAX_RELANCES,
    Engine,
    QueryExecutionError,
    QuestionError,
    WarehouseUnavailableError,
    build_messages,
)
from anamnese.guardrails import SQLValidationError
from anamnese.llm import Message, ReponseLLM, extract_sql

RACINE = Path(__file__).resolve().parent.parent
WAREHOUSE = RACINE / "data" / "warehouse" / "anamnese.duckdb"
DOSSIER_TARGET = RACINE / "transform" / "target"

_WAREHOUSE_ABSENT = pytest.mark.skipif(
    not (WAREHOUSE.is_file() and (DOSSIER_TARGET / "catalog.json").is_file()),
    reason="warehouse ou catalogue dbt absent : make build && make docs",
)


def besoin_warehouse(test: Callable[..., None]) -> Callable[..., None]:
    """Marque needs_warehouse, et ignore le test si le warehouse n'est pas construit."""
    return pytest.mark.needs_warehouse(_WAREHOUSE_ABSENT(test))


CATALOGUE_MINIMAL = Catalogue(
    tables=(
        TableCatalogue(
            nom="dim_patient",
            description="Une ligne par patient.",
            colonnes=(ColonneCatalogue("subject_id", "BIGINT", "Cle primaire."),),
        ),
    )
)


class FauxLLM:
    """Renvoie des reponses predefinies et garde les conversations recues."""

    modele = "faux-modele"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)
        self.conversations: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> ReponseLLM:
        self.conversations.append(list(messages))
        return ReponseLLM(self._reponses.pop(0), self.modele, 100, 10)


def _moteur(*reponses: str, catalogue: Catalogue | None = None, **reglages: object):
    llm = FauxLLM(*reponses)
    parametres = {"ANAMNESE_DUCKDB_PATH": str(WAREHOUSE), **reglages}
    moteur = Engine(Settings(**parametres), llm, catalogue or load_catalog(DOSSIER_TARGET))
    return moteur, llm


# --------------------------------------------------------------------------- #
# Extraction du SQL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reponse", "attendu"),
    [
        ("```sql\nselect 1\n```", "select 1"),
        ("Voici la requete :\n```sql\nselect 1\n```\nBonne analyse.", "select 1"),
        ("```\nselect 2\n```", "select 2"),
        ("```SQL\nselect 3\n```", "select 3"),
        ("  select 4  ", "select 4"),
    ],
)
def test_extrait_le_sql_du_premier_bloc_de_code(reponse: str, attendu: str) -> None:
    assert extract_sql(reponse) == attendu


# --------------------------------------------------------------------------- #
# Prompt : metadonnees seulement
# --------------------------------------------------------------------------- #
def test_le_prompt_contient_le_schema_les_exemples_et_la_question() -> None:
    messages = build_messages(CATALOGUE_MINIMAL, "Combien de patients ?")
    assert messages[0].role == "system"
    assert "### dim_patient" in messages[0].contenu
    assert "{{schema}}" not in messages[0].contenu
    assert [message.role for message in messages[1:-1]] == ["user", "assistant"] * 5
    assert messages[-1] == Message("user", "Combien de patients ?")


# --------------------------------------------------------------------------- #
# Questions refusees avant tout appel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_refuse_une_question_vide_ou_trop_longue_sans_appeler_le_llm(question: str) -> None:
    llm = FauxLLM()
    moteur = Engine(Settings(), llm, CATALOGUE_MINIMAL)
    with pytest.raises(QuestionError):
        moteur.ask(question)
    assert llm.conversations == []


def test_leve_une_question_error_quand_le_llm_ne_peut_pas_repondre() -> None:
    llm = FauxLLM("CANNOT_ANSWER: aucune donnee de facturation")
    moteur = Engine(Settings(), llm, CATALOGUE_MINIMAL)
    with pytest.raises(QuestionError, match="facturation"):
        moteur.ask("Combien a coute le sejour ?")


def test_signale_un_warehouse_absent_sans_relancer() -> None:
    llm = FauxLLM("select count(*) from dim_patient")
    moteur = Engine(
        Settings(ANAMNESE_DUCKDB_PATH="data/warehouse/absent.duckdb"), llm, CATALOGUE_MINIMAL
    )
    with pytest.raises(WarehouseUnavailableError):
        moteur.ask("Combien de patients ?")
    assert len(llm.conversations) == 1


def test_abandonne_apres_le_nombre_maximal_de_relances() -> None:
    llm = FauxLLM(*["select * from secret"] * (MAX_RELANCES + 1))
    moteur = Engine(Settings(), llm, CATALOGUE_MINIMAL)
    with pytest.raises(SQLValidationError, match="secret"):
        moteur.ask("x")
    assert len(llm.conversations) == MAX_RELANCES + 1


# --------------------------------------------------------------------------- #
# Execution sur le vrai warehouse
# --------------------------------------------------------------------------- #
@besoin_warehouse
def test_repond_avec_le_sql_normalise_et_les_lignes() -> None:
    moteur, _ = _moteur("```sql\nselect sexe, count(*) as n from dim_patient group by sexe\n```")
    resultat = moteur.ask("Repartition par sexe ?")
    assert resultat.sql.startswith("SELECT sexe")
    assert resultat.colonnes == ["sexe", "n"]
    assert sorted(resultat.lignes) == [("F", 43), ("M", 57)]
    assert resultat.tentatives == 1
    assert (resultat.tokens_prompt, resultat.tokens_reponse) == (100, 10)


@besoin_warehouse
def test_relance_avec_la_raison_du_rejet_des_garde_fous() -> None:
    moteur, llm = _moteur("select * from stg_hosp__patients", "select count(*) from dim_patient")
    resultat = moteur.ask("Combien de patients ?")
    assert resultat.tentatives == 2
    assert "stg_hosp__patients" in llm.conversations[1][-1].contenu
    assert llm.conversations[1][-2] == Message("assistant", "select * from stg_hosp__patients")


@besoin_warehouse
def test_relance_avec_l_erreur_de_schema_de_duckdb() -> None:
    moteur, llm = _moteur("select colonne_inconnue from dim_patient", "select 1 from dim_patient")
    assert moteur.ask("x").tentatives == 2
    assert "colonne_inconnue" in llm.conversations[1][-1].contenu


@besoin_warehouse
def test_ne_transmet_jamais_au_llm_une_valeur_citee_par_duckdb() -> None:
    # Une erreur de conversion DuckDB cite la valeur fautive : 'NEG' est une donnee.
    moteur, llm = _moteur(
        "select cast(valeur_texte as double) from fct_lab_result where valeur_texte = 'NEG'",
        "select 1 from dim_patient",
    )
    moteur.ask("x")
    retour = llm.conversations[1][-1].contenu
    assert "ConversionException" in retour
    assert "NEG" not in retour


@besoin_warehouse
def test_tronque_le_resultat_a_max_rows() -> None:
    moteur, _ = _moteur("select * from fct_lab_result", ANAMNESE_MAX_ROWS=7)
    assert len(moteur.ask("x").lignes) == 7


@besoin_warehouse
def test_interrompt_une_requete_trop_longue() -> None:
    moteur, llm = _moteur(
        *["select count(*) from fct_lab_result as a, fct_lab_result as b"] * (MAX_RELANCES + 1),
        ANAMNESE_QUERY_TIMEOUT_SECONDS=0.3,
    )
    with pytest.raises(QueryExecutionError, match="time limit"):
        moteur.ask("x")
    assert "time limit" in llm.conversations[1][-1].contenu
