"""Tests de l'API : codes de retour de CLAUDE.md §10 et forme des reponses.

Le moteur est remplace par un moteur a faux LLM : aucun appel reseau.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anamnese import api
from anamnese.catalog import load_catalog
from anamnese.config import Settings
from anamnese.engine import Engine, QueryExecutionError, WarehouseUnavailableError
from anamnese.guardrails import SQLValidationError
from anamnese.llm import LLMError, ReponseLLM

RACINE = Path(__file__).resolve().parent.parent
WAREHOUSE = RACINE / "data" / "warehouse" / "anamnese.duckdb"
DOSSIER_TARGET = RACINE / "transform" / "target"
WAREHOUSE_PRESENT = WAREHOUSE.is_file() and (DOSSIER_TARGET / "catalog.json").is_file()


class FauxLLM:
    modele = "faux-modele"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)

    def complete(self, _messages: object) -> ReponseLLM:
        return ReponseLLM(self._reponses.pop(0), self.modele, 1, 1)


class MoteurQuiEchoue:
    def __init__(self, erreur: Exception) -> None:
        self._erreur = erreur

    def ask(self, _question: str) -> None:
        raise self._erreur


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.app)


def _brancher(monkeypatch: pytest.MonkeyPatch, moteur: object) -> None:
    monkeypatch.setattr(api, "_moteur", lambda: moteur)


def test_health_repond_meme_sans_warehouse(client: TestClient) -> None:
    reponse = client.get("/health")
    assert reponse.status_code == 200
    assert set(reponse.json()) == {"status", "warehouse", "catalogue"}


@pytest.mark.parametrize("corps", [{}, {"question": 42}, {"autre": "x"}])
def test_un_corps_de_requete_invalide_renvoie_400(client: TestClient, corps: dict) -> None:
    assert client.post("/ask", json=corps).status_code == 400


@pytest.mark.parametrize(
    ("erreur", "code"),
    [
        (SQLValidationError("table non autorisee : secret"), 422),
        (QueryExecutionError("echec d'execution"), 422),
        (WarehouseUnavailableError("warehouse absent"), 503),
        (LLMError("Mistral injoignable"), 502),
    ],
)
def test_chaque_erreur_du_moteur_a_son_code_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, erreur: Exception, code: int
) -> None:
    _brancher(monkeypatch, MoteurQuiEchoue(erreur))
    reponse = client.post("/ask", json={"question": "Combien de patients ?"})
    assert reponse.status_code == code
    assert reponse.json()["detail"] == str(erreur)


@pytest.mark.needs_warehouse
@pytest.mark.skipif(not WAREHOUSE_PRESENT, reason="make build && make docs")
def test_une_question_vide_ou_sans_reponse_renvoie_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    moteur = Engine(Settings(), FauxLLM("CANNOT_ANSWER: hors schema"), load_catalog(DOSSIER_TARGET))
    _brancher(monkeypatch, moteur)
    assert client.post("/ask", json={"question": "   "}).status_code == 400
    reponse = client.post("/ask", json={"question": "Quel medecin ?"})
    assert (reponse.status_code, reponse.json()["detail"]) == (400, "hors schema")


@pytest.mark.needs_warehouse
@pytest.mark.skipif(not WAREHOUSE_PRESENT, reason="make build && make docs")
def test_ask_renvoie_sql_colonnes_lignes_et_duree(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    moteur = Engine(
        Settings(ANAMNESE_DUCKDB_PATH=str(WAREHOUSE)),
        FauxLLM("select sexe, count(*) as n, max(deces_date) as d from dim_patient group by sexe"),
        load_catalog(DOSSIER_TARGET),
    )
    _brancher(monkeypatch, moteur)
    reponse = client.post("/ask", json={"question": "Repartition par sexe ?"})
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["colonnes"] == ["sexe", "n", "d"]
    assert sorted(ligne[:2] for ligne in corps["lignes"]) == [["F", 43], ["M", 57]]
    assert all(isinstance(ligne[2], str) for ligne in corps["lignes"])  # date -> ISO
    assert corps["sql"].startswith("SELECT")
    assert {"duree_ms", "tentatives", "modele"} <= set(corps)


@pytest.mark.needs_warehouse
@pytest.mark.skipif(not WAREHOUSE_PRESENT, reason="make build && make docs")
def test_catalog_tables_liste_les_marts(client: TestClient) -> None:
    reponse = client.get("/catalog/tables")
    assert reponse.status_code == 200
    assert "fct_admission" in {table["nom"] for table in reponse.json()}
