"""API HTTP du moteur NL2SQL.

- `POST /ask` : question en francais -> SQL, colonnes, lignes, duree.
- `GET /health` : l'API repond ; indique si le warehouse et le catalogue sont la.
- `GET /catalog/tables` : marts interrogeables et leur description.

Codes d'erreur (CLAUDE.md §10) : 400 question invalide ou sans reponse, 422 SQL
rejete par les garde-fous ou en echec apres relances, 503 warehouse ou catalogue
absent, 502 echec du LLM. L'API demarre meme sans warehouse : seul `/ask` echoue.

Le warehouse est rouvert a chaque requete : un `make build` est vu sans
redemarrage. Le catalogue est lu une fois ; un nouveau mart demande un redemarrage.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from anamnese.catalog import CatalogueError, load_catalog
from anamnese.config import get_settings
from anamnese.engine import (
    LONGUEUR_MAX_QUESTION,
    Engine,
    QueryExecutionError,
    QuestionError,
    WarehouseUnavailableError,
    configure_logging,
    create_engine,
)
from anamnese.guardrails import SQLValidationError
from anamnese.llm import LLMError

configure_logging(get_settings().log_level)
app = FastAPI(title="anamnese", summary="Questions en francais sur MIMIC-IV demo.")


@app.exception_handler(RequestValidationError)
def _requete_invalide(_requete: Request, erreur: RequestValidationError) -> JSONResponse:
    # 422 est reserve au SQL rejete (CLAUDE.md §10) : un corps de requete
    # invalide est une question invalide, donc 400.
    champs = ", ".join(".".join(str(e) for e in detail["loc"]) for detail in erreur.errors())
    return JSONResponse(status_code=400, content={"detail": f"requete invalide : {champs}"})


class QuestionRequest(BaseModel):
    # Longueur verifiee par le moteur, pour un seul message d'erreur.
    question: str = Field(description=f"Question en francais, {LONGUEUR_MAX_QUESTION} car. max.")


class AskResponse(BaseModel):
    sql: str
    colonnes: list[str]
    lignes: list[list[Any]]
    duree_ms: int
    tentatives: int
    modele: str


class HealthResponse(BaseModel):
    status: str
    warehouse: bool
    catalogue: bool


class TableResponse(BaseModel):
    nom: str
    description: str
    colonnes: list[str]


@lru_cache(maxsize=1)
def _moteur() -> Engine:
    return create_engine(get_settings())


@app.get("/health")
def health() -> HealthResponse:
    settings = get_settings()
    warehouse = settings.duckdb_path.is_file()
    catalogue = (settings.dbt_target_path / "manifest.json").is_file()
    return HealthResponse(
        status="ok" if warehouse and catalogue else "degrade",
        warehouse=warehouse,
        catalogue=catalogue,
    )


@app.get("/catalog/tables")
def catalog_tables() -> list[TableResponse]:
    try:
        catalogue = load_catalog(get_settings().dbt_target_path)
    except CatalogueError as erreur:
        raise HTTPException(status_code=503, detail=str(erreur)) from erreur
    return [
        TableResponse(
            nom=table.nom,
            description=table.description,
            colonnes=[colonne.nom for colonne in table.colonnes],
        )
        for table in catalogue.tables
    ]


@app.post("/ask")
def ask(requete: QuestionRequest) -> AskResponse:
    try:
        resultat = _moteur().ask(requete.question)
    except QuestionError as erreur:
        raise HTTPException(status_code=400, detail=str(erreur)) from erreur
    except (SQLValidationError, QueryExecutionError) as erreur:
        raise HTTPException(status_code=422, detail=str(erreur)) from erreur
    except (WarehouseUnavailableError, CatalogueError) as erreur:
        raise HTTPException(status_code=503, detail=str(erreur)) from erreur
    except LLMError as erreur:
        raise HTTPException(status_code=502, detail=str(erreur)) from erreur
    return AskResponse(
        sql=resultat.sql,
        colonnes=resultat.colonnes,
        lignes=jsonable_encoder([list(ligne) for ligne in resultat.lignes]),
        duree_ms=resultat.duree_ms,
        tentatives=resultat.tentatives,
        modele=resultat.modele,
    )
