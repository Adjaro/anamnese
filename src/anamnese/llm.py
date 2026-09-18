"""Client Mistral : envoie une conversation, renvoie le texte et la consommation.

Le modele vient toujours de la configuration (`MISTRAL_MODEL`), jamais du code :
le moteur doit fonctionner a l'identique avec ministral-3b, mistral-small ou
mistral-large (CLAUDE.md §1). Temperature 0 : une meme question doit produire
le meme SQL, condition d'une evaluation reproductible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx
from mistralai.client import Mistral
from mistralai.client.errors import NoResponseError
from mistralai.client.errors.mistralerror import MistralError

TIMEOUT_MS = 30_000
TEMPERATURE = 0.0

# Premier bloc ```sql ... ``` (ou ``` ... ```) de la reponse.
MOTIF_BLOC_SQL = re.compile(r"```(?:sql|duckdb)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """Appel au LLM impossible : cle absente, erreur reseau ou reponse vide."""


@dataclass(frozen=True)
class Message:
    role: str  # "system", "user" ou "assistant"
    contenu: str


@dataclass(frozen=True)
class ReponseLLM:
    texte: str
    modele: str
    tokens_prompt: int
    tokens_reponse: int


class ClientLLM(Protocol):
    """Ce que le moteur attend d'un LLM ; permet un faux client en test."""

    modele: str

    def complete(self, messages: list[Message]) -> ReponseLLM: ...


class ClientMistral:
    """Implementation de `ClientLLM` sur l'API Mistral."""

    def __init__(self, api_key: str | None, modele: str) -> None:
        if not api_key:
            raise LLMError("MISTRAL_API_KEY absente : la renseigner dans .env")
        self.modele = modele
        self._client = Mistral(api_key=api_key, timeout_ms=TIMEOUT_MS)

    def complete(self, messages: list[Message]) -> ReponseLLM:
        try:
            reponse = self._client.chat.complete(
                model=self.modele,
                messages=[
                    {"role": message.role, "content": message.contenu} for message in messages
                ],
                temperature=TEMPERATURE,
            )
        except (MistralError, NoResponseError, httpx.HTTPError) as erreur:
            raise LLMError(
                f"appel Mistral en echec : {type(erreur).__name__}: {erreur}"
            ) from erreur

        if not reponse.choices:
            raise LLMError("reponse Mistral sans choix")
        texte = _extraire_texte(reponse.choices[0].message.content)
        if not texte.strip():
            raise LLMError("reponse Mistral vide")
        usage = reponse.usage
        return ReponseLLM(
            texte=texte,
            modele=reponse.model or self.modele,
            tokens_prompt=usage.prompt_tokens or 0 if usage else 0,
            tokens_reponse=usage.completion_tokens or 0 if usage else 0,
        )


def _extraire_texte(contenu: object) -> str:
    # Le SDK renvoie une chaine, ou une liste de morceaux dont seuls les textes comptent.
    if isinstance(contenu, str):
        return contenu
    if isinstance(contenu, list):
        return "".join(getattr(morceau, "text", "") or "" for morceau in contenu)
    return ""


def extract_sql(texte: str) -> str:
    """SQL contenu dans la reponse : premier bloc de code, sinon le texte entier."""
    bloc = MOTIF_BLOC_SQL.search(texte)
    sql = bloc.group(1) if bloc else texte
    return sql.strip()
