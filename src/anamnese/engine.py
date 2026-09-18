"""Moteur NL2SQL : question -> contexte -> LLM -> garde-fous -> execution read_only.

Usage en ligne de commande : `python -m anamnese.engine "<question>"`.

Un SQL rejete par les garde-fous ou en echec a l'execution est renvoye au LLM
avec la raison, au plus `MAX_RELANCES` fois. Le LLM ne recoit jamais de donnees :
seuls le schema, la question et des messages d'erreur sans valeur (CLAUDE.md §2).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import duckdb
import structlog
import yaml

from anamnese.catalog import Catalogue, CatalogueError, load_catalog
from anamnese.config import Settings, get_settings
from anamnese.guardrails import SQLValidationError, validate
from anamnese.llm import ClientLLM, ClientMistral, LLMError, Message, extract_sql

MAX_RELANCES = 2
LONGUEUR_MAX_QUESTION = 1000
MARQUEUR_SANS_REPONSE = "CANNOT_ANSWER:"
MARQUEUR_SCHEMA = "{{schema}}"

# Erreurs DuckDB qui ne decrivent que le schema ou la syntaxe : leur message peut
# etre renvoye au LLM. Les autres (conversion, depassement...) peuvent citer une
# valeur de la base : seul leur type est transmis.
ERREURS_DUCKDB_SANS_DONNEES = (
    duckdb.BinderException,
    duckdb.CatalogException,
    duckdb.ParserException,
)

logger = structlog.get_logger(__name__)


class QuestionError(ValueError):
    """Question vide, trop longue, ou jugee sans reponse possible par le LLM."""


class WarehouseUnavailableError(RuntimeError):
    """Fichier DuckDB absent ou illisible : lancer `make build`."""


class QueryExecutionError(RuntimeError):
    """SQL valide mais en echec a l'execution apres toutes les relances."""


@dataclass(frozen=True)
class Resultat:
    question: str
    sql: str
    colonnes: list[str]
    lignes: list[tuple[Any, ...]]
    duree_ms: int
    tentatives: int
    modele: str
    tokens_prompt: int
    tokens_reponse: int


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def _lire_prompt(nom: str) -> str:
    return files("anamnese.prompts").joinpath(nom).read_text(encoding="utf-8")


def build_messages(catalogue: Catalogue, question: str) -> list[Message]:
    """Prompt systeme avec le schema, exemples few-shot, puis la question."""
    systeme = _lire_prompt("system.md").replace(MARQUEUR_SCHEMA, catalogue.build_context())
    messages = [Message("system", systeme)]
    for exemple in yaml.safe_load(_lire_prompt("examples.yml")):
        messages.append(Message("user", exemple["question"]))
        messages.append(Message("assistant", f"```sql\n{exemple['sql'].strip()}\n```"))
    messages.append(Message("user", question))
    return messages


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _message_pour_le_llm(erreur: Exception) -> str:
    if isinstance(erreur, duckdb.InterruptException):
        return "The query exceeded the time limit and was interrupted. Write a cheaper query."
    if isinstance(erreur, SQLValidationError):
        return f"The query was rejected by the SQL guardrails: {erreur}"
    if isinstance(erreur, ERREURS_DUCKDB_SANS_DONNEES):
        return f"DuckDB could not run the query: {erreur}"
    return f"DuckDB could not run the query ({type(erreur).__name__})."


class Engine:
    """Orchestration d'une question ; une instance par processus suffit."""

    def __init__(self, settings: Settings, client: ClientLLM, catalogue: Catalogue) -> None:
        self._settings = settings
        self._client = client
        self._catalogue = catalogue

    def _executer(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        chemin = self._settings.duckdb_path
        if not chemin.is_file():
            raise WarehouseUnavailableError(f"warehouse absent : {chemin}")
        try:
            connexion = duckdb.connect(
                str(chemin), read_only=True, config={"enable_external_access": False}
            )
        except duckdb.Error as erreur:
            raise WarehouseUnavailableError(f"warehouse illisible : {erreur}") from erreur

        # DuckDB n'a pas de timeout de requete : un minuteur interrompt la connexion.
        minuteur = threading.Timer(self._settings.query_timeout_seconds, connexion.interrupt)
        minuteur.start()
        try:
            # `sql` sort de validate() : une seule requete SELECT normalisee.
            relation = connexion.execute(
                f"select * from ({sql}) limit ?",  # noqa: S608
                [self._settings.max_rows],
            )
            colonnes = [description[0] for description in relation.description]
            return colonnes, relation.fetchall()
        finally:
            minuteur.cancel()
            connexion.close()

    def ask(self, question: str) -> Resultat:
        """Repond a une question ; leve une erreur typee si c'est impossible."""
        question = question.strip()
        if not question:
            raise QuestionError("question vide")
        if len(question) > LONGUEUR_MAX_QUESTION:
            raise QuestionError(f"question de plus de {LONGUEUR_MAX_QUESTION} caracteres")

        debut = time.perf_counter()
        messages = build_messages(self._catalogue, question)
        tokens_prompt = tokens_reponse = 0
        derniere_erreur: Exception | None = None

        for tentative in range(1, MAX_RELANCES + 2):
            reponse = self._client.complete(messages)
            tokens_prompt += reponse.tokens_prompt
            tokens_reponse += reponse.tokens_reponse
            if reponse.texte.strip().startswith(MARQUEUR_SANS_REPONSE):
                raison = reponse.texte.strip().removeprefix(MARQUEUR_SANS_REPONSE).strip()
                logger.info("question_sans_reponse", question=question, raison=raison)
                raise QuestionError(raison or "question sans reponse possible")

            sql_brut = extract_sql(reponse.texte)
            try:
                sql = validate(sql_brut, self._catalogue.tables_autorisees)
                colonnes, lignes = self._executer(sql)
            except (SQLValidationError, duckdb.Error) as erreur:
                derniere_erreur = erreur
                logger.warning(
                    "tentative_rejetee",
                    question=question,
                    tentative=tentative,
                    sql=sql_brut,
                    verdict=type(erreur).__name__,
                    raison=_message_pour_le_llm(erreur),
                )
                messages += [
                    Message("assistant", reponse.texte),
                    Message(
                        "user", f"{_message_pour_le_llm(erreur)} Reply with a corrected query."
                    ),
                ]
                continue

            duree_ms = round((time.perf_counter() - debut) * 1000)
            logger.info(
                "question_resolue",
                question=question,
                sql=sql,
                verdict="ok",
                tentatives=tentative,
                nb_lignes=len(lignes),
                duree_ms=duree_ms,
                modele=reponse.modele,
            )
            return Resultat(
                question=question,
                sql=sql,
                colonnes=colonnes,
                lignes=lignes,
                duree_ms=duree_ms,
                tentatives=tentative,
                modele=reponse.modele,
                tokens_prompt=tokens_prompt,
                tokens_reponse=tokens_reponse,
            )

        if isinstance(derniere_erreur, SQLValidationError):
            raise derniere_erreur
        raise QueryExecutionError(_message_pour_le_llm(derniere_erreur)) from derniere_erreur


def configure_logging(niveau: str) -> None:
    """Logs JSON sur la sortie d'erreur : question, SQL, verdict, duree. Jamais de lignes."""
    logging.basicConfig(level=niveau, stream=sys.stderr, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(niveau)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def create_engine(settings: Settings | None = None) -> Engine:
    """Moteur branche sur Mistral et sur le catalogue dbt courant."""
    settings = settings or get_settings()
    secret = settings.mistral_api_key.get_secret_value() if settings.mistral_api_key else None
    return Engine(
        settings=settings,
        client=ClientMistral(api_key=secret, modele=settings.mistral_model),
        catalogue=load_catalog(settings.dbt_target_path),
    )


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #
def main() -> int:
    if len(sys.argv) != 2:
        print('usage : python -m anamnese.engine "<question>"', file=sys.stderr)
        return 2
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        resultat = create_engine(settings).ask(sys.argv[1])
    except (
        QuestionError,
        SQLValidationError,
        QueryExecutionError,
        WarehouseUnavailableError,
        CatalogueError,
        LLMError,
    ) as erreur:
        print(f"{type(erreur).__name__} : {erreur}", file=sys.stderr)
        return 1

    print(f"-- {resultat.modele}, {resultat.tentatives} tentative(s), {resultat.duree_ms} ms")
    print(resultat.sql)
    print()
    print(" | ".join(resultat.colonnes))
    for ligne in resultat.lignes:
        print(" | ".join("" if valeur is None else str(valeur) for valeur in ligne))
    return 0


if __name__ == "__main__":
    sys.exit(main())
