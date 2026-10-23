"""
suggestions.py — Moteur de suggestions V2 (règles métier, sans IA générative)

Analyse l'historique des réclamations et propose des actions prioritaires :
  1. Références récurrentes (stock de sécurité, cause dominante, temps d'arrêt)
  2. Types de problèmes dominants (actions adaptées à chaque type)
  3. Tickets en attente de prise en charge trop longtemps
  4. Réactivité moyenne de prise en charge
  5. Tendance à la hausse sur la période récente

Chaque suggestion est un dictionnaire :
  {"type": ..., "priorite": "Critique|Élevée|Moyenne|Info", "reference": ..., "message": ...}
"""

import pandas as pd

# ---------------------------------------------------------------
# Seuils configurables
# ---------------------------------------------------------------
SEUIL_RECURRENCE = 3                    # occurrences à partir desquelles on alerte
SEUIL_CRITIQUE = 5                      # occurrences à partir desquelles c'est critique
SEUIL_TEMPS_PERDU_MIN = 60              # temps d'arrêt cumulé (min) jugé important
SEUIL_ATTENTE_PRISE_EN_CHARGE_MIN = 30  # ticket "Ouvert" trop longtemps (min)
SEUIL_REACTIVITE_MOYENNE_MIN = 45       # délai moyen de prise en charge jugé trop long (min)
FENETRE_TENDANCE_JOURS = 7              # période récente comparée à la période précédente

ORDRE_PRIORITE = {"Critique": 0, "Élevée": 1, "Moyenne": 2, "Info": 3}

ACTIONS_PAR_TYPE = {
    "Manque de pièces": (
        "Vérifier le plan de production et le niveau de stock bord de ligne ; "
        "envisager un stock tampon ou des transferts plus fréquents."
    ),
    "Retard de livraison": (
        "Analyser le planning de transfert des box entre zones : goulot "
        "d'étranglement possible, fréquence et ponctualité des navettes."
    ),
    "Non-conformité": (
        "Renforcer le contrôle qualité en amont, avant le transfert, "
        "et vérifier le standard d'aspect et de dimension."
    ),
    "Erreur étiquetage": (
        "Revoir le process d'étiquetage et le contrôle GALIA avant expédition ; "
        "vérifier l'impression et la lecture des étiquettes."
    ),
}
ACTION_PAR_DEFAUT = "Analyser la cause racine récurrente avec les deux zones."


# ---------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------
def _action_pour_type(type_pb):
    """Retourne l'action associée à un type, par correspondance sur le début du libellé."""
    for cle, action in ACTIONS_PAR_TYPE.items():
        if str(type_pb).startswith(cle):
            return action
    return ACTION_PAR_DEFAUT


def _valeur(row, colonne, defaut=None):
    """Lit une colonne d'une ligne sans planter si elle est absente ou vide."""
    try:
        val = row[colonne]
    except (KeyError, IndexError):
        return defaut
    return defaut if pd.isna(val) else val


def _preparer(df):
    """Copie le DataFrame et convertit les dates. Retourne None si inutilisable."""
    if df is None or df.empty:
        return None
    d = df.copy()
    d["_creation"] = pd.to_datetime(d.get("date_creation"), errors="coerce")
    d["_prise"] = pd.to_datetime(d.get("date_prise_en_charge"), errors="coerce")
    return d


# ---------------------------------------------------------------
# 1. Références récurrentes
# ---------------------------------------------------------------
def _suggestions_references(df_ref, df_all):
    resultats = []
    if df_ref is None or df_ref.empty:
        return resultats

    for _, row in df_ref.iterrows():
        nb = int(_valeur(row, "nb_occurrences", 0))
        if nb < SEUIL_RECURRENCE:
            continue

        ref = row["reference"]
        qte_moy = _valeur(row, "quantite_moyenne")
        temps_perdu = float(_valeur(row, "temps_perdu_total", 0) or 0)

        details = [f"signalée {nb} fois"]
        if qte_moy is not None:
            details.append(f"quantité moyenne manquante : {round(float(qte_moy), 1)} pièce(s)")
        if temps_perdu > 0:
            details.append(f"temps d'arrêt cumulé : {int(temps_perdu)} min")

        # Cause dominante pour cette référence
        cause = ""
        if df_all is not None and not df_all.empty:
            sous = df_all[df_all["reference"] == ref]
            if not sous.empty:
                types = sous["type_probleme"].value_counts()
                cause = (
                    f" Cause dominante : « {types.index[0]} » "
                    f"({int(types.iloc[0])}/{len(sous)} signalements)."
                )

        if nb >= SEUIL_CRITIQUE:
            priorite = "Critique"
        elif temps_perdu >= SEUIL_TEMPS_PERDU_MIN:
            priorite = "Élevée"
        else:
            priorite = "Moyenne"

        resultats.append({
            "type": "Référence récurrente",
            "priorite": priorite,
            "reference": ref,
            "message": (
                f"Référence {ref} : " + ", ".join(details) + "." + cause +
                " → Envisager un stock de sécurité dédié et une analyse de cause racine."
            ),
        })
    return resultats


# ---------------------------------------------------------------
# 2. Types de problèmes dominants
# ---------------------------------------------------------------
def _suggestions_types(df_type):
    resultats = []
    if df_type is None or df_type.empty:
        return resultats

    for _, row in df_type.iterrows():
        nb = int(_valeur(row, "nb_occurrences", 0))
        if nb < SEUIL_RECURRENCE:
            continue

        type_pb = row["type_probleme"]
        resultats.append({
            "type": "Type de problème dominant",
            "priorite": "Élevée" if nb >= SEUIL_CRITIQUE else "Moyenne",
            "reference": "-",
            "message": (
                f"Le type « {type_pb} » revient {nb} fois. → {_action_pour_type(type_pb)}"
            ),
        })
    return resultats


# ---------------------------------------------------------------
# 3. Tickets ouverts depuis trop longtemps
# ---------------------------------------------------------------
def _suggestions_attente(d):
    resultats = []
    if d is None or "statut" not in d:
        return resultats

    maintenant = pd.Timestamp.now()
    ouverts = d[(d["statut"] == "Ouvert") & d["_creation"].notna()].copy()
    if ouverts.empty:
        return resultats

    ouverts["_attente_min"] = (maintenant - ouverts["_creation"]).dt.total_seconds() / 60
    en_retard = ouverts[ouverts["_attente_min"] >= SEUIL_ATTENTE_PRISE_EN_CHARGE_MIN]
    if en_retard.empty:
        return resultats

    en_retard = en_retard.sort_values("_attente_min", ascending=False)
    liste = ", ".join(
        f"#{int(r['id'])} ({r['reference']}, {int(r['_attente_min'])} min)"
        for _, r in en_retard.head(5).iterrows()
    )
    plus = f" et {len(en_retard) - 5} autre(s)" if len(en_retard) > 5 else ""
    pire = en_retard["_attente_min"].max()

    resultats.append({
        "type": "Tickets en attente",
        "priorite": "Critique" if pire >= 2 * SEUIL_ATTENTE_PRISE_EN_CHARGE_MIN else "Élevée",
        "reference": "-",
        "message": (
            f"{len(en_retard)} ticket(s) non pris en charge depuis plus de "
            f"{SEUIL_ATTENTE_PRISE_EN_CHARGE_MIN} min : {liste}{plus}. "
            f"→ Prendre en charge ou escalader auprès du responsable de zone."
        ),
    })
    return resultats


# ---------------------------------------------------------------
# 4. Réactivité moyenne
# ---------------------------------------------------------------
def _suggestions_reactivite(d):
    resultats = []
    if d is None:
        return resultats

    pris = d[d["_creation"].notna() & d["_prise"].notna()].copy()
    if len(pris) < 3:
        return resultats  # pas assez de données pour conclure

    pris["_delai_min"] = (pris["_prise"] - pris["_creation"]).dt.total_seconds() / 60
    moyenne = pris["_delai_min"].mean()

    if moyenne >= SEUIL_REACTIVITE_MOYENNE_MIN:
        resultats.append({
            "type": "Réactivité de prise en charge",
            "priorite": "Moyenne",
            "reference": "-",
            "message": (
                f"Délai moyen de prise en charge : {int(moyenne)} min "
                f"(objectif : moins de {SEUIL_REACTIVITE_MOYENNE_MIN} min, "
                f"sur {len(pris)} tickets). → Revoir l'organisation de la veille "
                f"sur les signalements dans la zone destinataire."
            ),
        })
    return resultats


# ---------------------------------------------------------------
# 5. Tendance à la hausse
# ---------------------------------------------------------------
def _suggestions_tendance(d):
    resultats = []
    if d is None or "type_probleme" not in d:
        return resultats

    maintenant = pd.Timestamp.now()
    fenetre = pd.Timedelta(days=FENETRE_TENDANCE_JOURS)
    d = d[d["_creation"].notna()]

    recent = d[d["_creation"] >= maintenant - fenetre]
    precedent = d[(d["_creation"] < maintenant - fenetre) &
                  (d["_creation"] >= maintenant - 2 * fenetre)]

    for type_pb, nb_recent in recent["type_probleme"].value_counts().items():
        nb_prec = int((precedent["type_probleme"] == type_pb).sum())
        if nb_recent >= SEUIL_RECURRENCE and nb_recent > 1.5 * nb_prec:
            resultats.append({
                "type": "Tendance à la hausse",
                "priorite": "Élevée",
                "reference": "-",
                "message": (
                    f"« {type_pb} » : {int(nb_recent)} signalements sur les "
                    f"{FENETRE_TENDANCE_JOURS} derniers jours contre {nb_prec} sur la "
                    f"période précédente. → {_action_pour_type(type_pb)}"
                ),
            })
    return resultats


# ---------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------
def generer_suggestions(df_recurrence_reference, df_recurrence_type, df_reclamations=None):
    """
    Retourne la liste des suggestions, triées de la plus à la moins prioritaire.

    df_reclamations est optionnel : s'il est fourni, il permet les analyses
    avancées (cause dominante, délais, tendances).
    """
    d = _preparer(df_reclamations)

    suggestions = []
    suggestions += _suggestions_references(df_recurrence_reference, df_reclamations)
    suggestions += _suggestions_types(df_recurrence_type)
    suggestions += _suggestions_attente(d)
    suggestions += _suggestions_reactivite(d)
    suggestions += _suggestions_tendance(d)

    suggestions.sort(key=lambda s: ORDRE_PRIORITE.get(s["priorite"], 99))
    return suggestions