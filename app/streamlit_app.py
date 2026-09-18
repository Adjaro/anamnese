"""Interface Streamlit : un client HTTP de l'API, jamais de DuckDB ni de LLM ici.

Lancement : `make app` (API sur ANAMNESE_API_URL, par defaut http://localhost:8000).
"""

from __future__ import annotations

from typing import Any

import httpx
import pandas as pd
import streamlit as st

from anamnese.config import get_settings

TIMEOUT_SECONDES = 120.0
EXEMPLES = (
    "Combien de patients sont morts pendant un séjour à l'hôpital ?",
    "Quelle est la durée moyenne de séjour en réanimation, en jours, par unité d'entrée ?",
    "Quels sont les 5 diagnostics les plus fréquents ?",
)


def _poser_question(question: str) -> dict[str, Any]:
    """Appelle POST /ask ; renvoie la reponse, ou {'erreur': message}."""
    try:
        reponse = httpx.post(
            f"{get_settings().api_url}/ask",
            json={"question": question},
            timeout=TIMEOUT_SECONDES,
        )
    except httpx.HTTPError as erreur:
        return {"erreur": f"API injoignable : {erreur}"}
    try:
        corps = reponse.json()
    except ValueError:
        # Reponse non JSON : ce n'est pas l'API anamnese qui a repondu.
        return {"erreur": f"{reponse.status_code} — reponse inattendue de {reponse.url}"}
    if reponse.is_success:
        return corps
    return {"erreur": f"{reponse.status_code} — {corps.get('detail', reponse.text)}"}


def _afficher(reponse: dict[str, Any], cle: str) -> None:
    if "erreur" in reponse:
        st.error(reponse["erreur"])
        return
    st.caption(
        f"{len(reponse['lignes'])} ligne(s) · {reponse['duree_ms']} ms · "
        f"{reponse['modele']} · {reponse['tentatives']} tentative(s)"
    )
    with st.expander("SQL généré"):
        st.code(reponse["sql"], language="sql", wrap_lines=True)
    tableau = pd.DataFrame(reponse["lignes"], columns=reponse["colonnes"])
    st.dataframe(tableau, width="stretch", hide_index=True)
    st.download_button(
        "Télécharger en CSV",
        data=tableau.to_csv(index=False).encode("utf-8"),
        file_name="anamnese.csv",
        mime="text/csv",
        key=f"csv-{cle}",
    )


st.set_page_config(page_title="anamnese", page_icon="🩺", layout="wide")
st.title("anamnese")
st.write(
    "Posez une question en français sur MIMIC-IV demo (100 patients). "
    "Les dates sont décalées par la désidentification : seules les durées ont un sens."
)

historique: list[tuple[str, dict[str, Any]]] = st.session_state.setdefault("historique", [])

with st.form("question", clear_on_submit=False):
    question = st.text_area("Question", placeholder=EXEMPLES[0], height=80)
    envoyee = st.form_submit_button("Demander", type="primary")

if envoyee and question.strip():
    with st.spinner("Traduction en SQL et exécution…"):
        historique.insert(0, (question.strip(), _poser_question(question.strip())))

if historique:
    derniere_question, derniere_reponse = historique[0]
    st.subheader(derniere_question)
    _afficher(derniere_reponse, "derniere")

if len(historique) > 1:
    st.divider()
    st.subheader("Historique de la session")
    for rang, (question_passee, reponse_passee) in enumerate(historique[1:], start=1):
        with st.expander(question_passee):
            _afficher(reponse_passee, f"historique-{rang}")

with st.sidebar:
    st.header("Exemples")
    for exemple in EXEMPLES:
        st.markdown(f"- {exemple}")
