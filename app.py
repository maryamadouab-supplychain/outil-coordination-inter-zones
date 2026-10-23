"""
app.py — Outil de coordination inter-zones (Zone_Amont <-> Zone_Aval)
Plateforme digitale de signalement et de suivi des manquants et retards de pièces.
"""

import streamlit as st
import os
import math
import io
import base64
import pandas as pd
import altair as alt
from datetime import datetime
from PIL import Image

from db import (
    init_db, init_users_table, verifier_connexion,
    migrer_colonnes_suivi, insert_reclamation, get_all_reclamations,
    prendre_en_charge, mettre_a_jour_commentaire, marquer_resolu,
    ajouter_message, get_messages,
    get_recurrence_par_reference, get_recurrence_par_type,
    get_historique_reference, get_mttr_global, get_mttr_par_type
)
from suggestions import generer_suggestions
from notifications import notifier_nouveau_ticket

# --- Configuration de la page ---
st.set_page_config(
    page_title="Coordination Inter-Zones",
    layout="wide",
    initial_sidebar_state="expanded"
)

PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)
init_db()
init_users_table()
migrer_colonnes_suivi()

ZONES = ["Zone_Amont", "Zone_Aval"]
TYPES_PROBLEME = [
    "Manque de pièces",
    "Retard de livraison",
    "Non-conformité aspect/dimension",
    "Erreur étiquetage / GALIA",
    "Autre"
]

COUT_MINUTE_LIGNE = 5.0     # € par minute d'arrêt : à ajuster avec le contrôle de gestion
SEUIL_ESCALADE_MIN = 30     # minutes avant alerte "ticket non pris en charge"

# --- Chargement de l'image d'arrière-plan (optionnelle : im4.png) ---
def get_base64_of_bin_file(bin_file):
    if os.path.exists(bin_file):
        with open(bin_file, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    return None

bg_im4_base64 = get_base64_of_bin_file("im4.png")

# --- Export Excel en binaire ---
def convertir_df_en_excel(df):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Historique_Reclamations')
    return buffer.getvalue()

# --- Gestion de l'état de connexion ---
if "utilisateur" not in st.session_state:
    st.session_state.utilisateur = None

is_logged_in = st.session_state.utilisateur is not None

# --- Styles CSS Dynamiques ---
if not is_logged_in:
    # Arrière-plan im4.png uniquement sur l'écran d'AUTHENTIFICATION
    CSS_AUTH = f"""
    <style>
        .stApp {{
            background: {f'url("data:image/png;base64,{bg_im4_base64}")' if bg_im4_base64 else '#002B49'};
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            background-attachment: fixed;
        }}
        .stApp, .stApp p, .stApp h1, .stApp h2, .stApp h3, .stApp label {{
            color: #FFFFFF !important;
        }}
        [data-testid="stForm"] {{
            background-color: rgba(0, 43, 73, 0.88) !important;
            border: 1px solid rgba(0, 163, 224, 0.4) !important;
            border-radius: 8px !important;
            padding: 2rem !important;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4) !important;
        }}
        .stButton>button {{
            background-color: #0051A5 !important;
            color: #FFFFFF !important;
            border-radius: 4px !important;
            font-weight: 600 !important;
            border: none !important;
            padding: 0.5rem 1rem !important;
            width: 100%;
        }}
        .stButton>button:hover {{
            background-color: #00A3E0 !important;
        }}
        .stTextInput input {{
            color: #000000 !important;
            background-color: #FFFFFF !important;
        }}
        [data-testid="stFormSubmitButton"] button {{
            background-color: #0051A5 !important;
            border: none !important;
            width: 100%;
        }}
        [data-testid="stFormSubmitButton"] button p {{
            color: #FFFFFF !important;
            font-weight: 600 !important;
        }}
        [data-testid="stFormSubmitButton"] button:hover {{
            background-color: #00A3E0 !important;
        }}
    </style>
    """
    st.markdown(CSS_AUTH, unsafe_allow_html=True)
else:
    # Style épuré (Bleu & Blanc) dans l'APPLICATION INTERNE
    CSS_APP = """
    <style>
        .stApp {
            background-color: #F4F6F9 !important;
        }
        [data-testid="stSidebar"] {
            background-color: #002B49 !important;
        }
        [data-testid="stSidebar"] * {
            color: #FFFFFF !important;
        }
        .stButton>button {
            background-color: #0051A5 !important;
            color: #FFFFFF !important;
            border-radius: 4px !important;
            font-weight: 600 !important;
            border: none !important;
        }
        .stButton>button:hover {
            background-color: #00A3E0 !important;
        }
    </style>
    """
    st.markdown(CSS_APP, unsafe_allow_html=True)


def formater_temps(minutes):
    """Convertit les minutes en format lisible."""
    if minutes is None or pd.isna(minutes):
        return "En cours d'estimation"
    try:
        val = float(minutes)
        if math.isnan(val):
            return "En cours d'estimation"
        m = int(val)
        h, mn = divmod(m, 60)
        return f"{h}h{mn:02d}" if h > 0 else f"{mn} min"
    except (ValueError, TypeError):
        return "En cours d'estimation"


def afficher_header():
    """Affiche l'en-tête de la plateforme."""
    title_color = "#FFFFFF" if not is_logged_in else "#002B49"
    sub_color = "#D0E1F9" if not is_logged_in else "#4A5568"
    st.markdown(f"<h2 style='color: {title_color}; margin: 0;'>Plateforme de Coordination Inter-Zones</h2>", unsafe_allow_html=True)
    st.markdown(f"<p style='color: {sub_color}; margin: 0;'>Suivi du flux de production</p>", unsafe_allow_html=True)
    st.divider()


def afficher_reclamation_card(row, mode_traitement=False, prefix="card"):
    """Affichage des cartes d'incidents avec statut, clé unique et messagerie intégrée."""
    statut = row['statut']
    
    status_badges = {
        "Ouvert": "[OUVERT]",
        "En cours": "[EN COURS]",
        "Résolu": "[RÉSOLU]"
    }
    badge = status_badges.get(statut, statut)
    titre_expander = f"Ticket #{row['id']} | Ref: {row['reference']} | {row['type_probleme']} — {badge}"

    with st.expander(titre_expander):
        tab_details, tab_tchat = st.tabs(["Détails & Actions", "Discussion Directe"])
        
        # --- ONGLET 1 : DÉTAILS ET ACTIONS ---
        with tab_details:
            c_info, c_img = st.columns([2, 1])
            with c_info:
                st.write(f"**Zone émettrice :** {row['zone_source']} ({row['emetteur']})")
                st.write(f"**Zone destinataire :** {row['zone_destination']} (Responsable : {row.get('recepteur') or 'Superviseur Zone'})")
                st.write(f"**Quantité :** {row['quantite']} pièce(s)")
                st.write(f"**Description :** {row['description']}")

                fiable_txt = "" if row.get("estimation_fiable") else " (estimation automatique)"
                st.info(f"Temps estimé de résolution : **{formater_temps(row.get('temps_estime_ia'))}**{fiable_txt}")

                if row.get("date_prise_en_charge"):
                    st.caption(f"Pris en charge le : {row['date_prise_en_charge']}")

                # NOUVEAU : durée réelle de résolution
                if pd.notna(row.get("date_resolution")) and row.get("date_resolution"):
                    try:
                        delai = (pd.to_datetime(row["date_resolution"]) - pd.to_datetime(row["date_creation"])
                                 ).total_seconds() / 60
                        st.caption(f"Résolu le {row['date_resolution']} (durée réelle : {formater_temps(delai)})")
                    except Exception:
                        pass

                if row.get("commentaire_resolution"):
                    st.success(f"Note d'avancement : {row['commentaire_resolution']}")

                if mode_traitement:
                    st.divider()
                    st.markdown("**Changer l'état du problème :**")
                    if statut == "Ouvert":
                        if st.button("Prendre en charge (Passer En cours)", key=f"btn_charge_{prefix}_{row['id']}"):
                            prendre_en_charge(row["id"])
                            st.rerun()

                    elif statut == "En cours":
                        commentaire = st.text_area(
                            "Mise à jour d'avancement / Heure de mise à disposition :",
                            value=row.get("commentaire_resolution") or "",
                            key=f"comm_{prefix}_{row['id']}"
                        )
                        b1, b2 = st.columns(2)
                        with b1:
                            if st.button("Sauvegarder la note", key=f"btn_maj_{prefix}_{row['id']}"):
                                mettre_a_jour_commentaire(row["id"], commentaire)
                                st.rerun()
                        with b2:
                            if st.button("Valider comme Résolu", key=f"btn_res_{prefix}_{row['id']}"):
                                marquer_resolu(row["id"], commentaire)
                                st.rerun()

            with c_img:
                photo_path = row.get("photo_path")
                if photo_path and isinstance(photo_path, str) and os.path.exists(photo_path):
                    st.image(photo_path, caption="Pièce jointe", use_container_width=True)
                else:
                    st.caption("Aucune photo jointe")

        # --- ONGLET 2 : MESSAGERIE DU TICKET ---
        with tab_tchat:
            st.markdown("##### Messages échangés pour ce ticket")
            df_msg = get_messages(row["id"])
            
            if df_msg.empty:
                st.caption("Aucun message partagé pour l'instant.")
            else:
                for _, msg in df_msg.iterrows():
                    est_mon_message = (msg["zone"] == st.session_state.ma_zone)
                    align = "right" if est_mon_message else "left"
                    bg_col = "#E3F2FD" if est_mon_message else "#E0E0E0"
                    
                    st.markdown(f"""
                        <div style='text-align: {align}; margin-bottom: 8px;'>
                            <div style='display: inline-block; background-color: {bg_col}; color: #000; padding: 8px 12px; border-radius: 8px; max-width: 80%;'>
                                <small style='color: #444;'><b>{msg['expediteur']} ({msg['zone']})</b> - {msg['date_envoi']}</small><br/>
                                {msg['message']}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

            st.divider()
            with st.form(key=f"form_msg_{prefix}_{row['id']}", clear_on_submit=True):
                nouveau_msg = st.text_input("Répondre / Envoyer une précision :", key=f"in_msg_{prefix}_{row['id']}")
                btn_send = st.form_submit_button("Envoyer le message")
                
                if btn_send and nouveau_msg.strip():
                    ajouter_message(
                        reclamation_id=row["id"],
                        expediteur=st.session_state.utilisateur["nom"],
                        zone=st.session_state.ma_zone,
                        message=nouveau_msg.strip()
                    )
                    st.rerun()


# ============================================================
# PAGE D'AUTHENTIFICATION
# ============================================================
if st.session_state.utilisateur is None:
    afficher_header()
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.subheader("Connexion Utilisateur")
        with st.form("form_connexion"):
            email = st.text_input("Adresse e-mail")
            code = st.text_input("Code d'accès", type="password")
            btn_login = st.form_submit_button("Se connecter")

            if btn_login:
                user = verifier_connexion(email, code)
                if user:
                    st.session_state.utilisateur = user
                    st.rerun()
                else:
                    st.error("Identifiants incorrects.")
    st.stop()

# ============================================================
# ESPACE UTILISATEUR CONNECTÉ
# ============================================================
st.session_state.ma_zone = st.session_state.utilisateur["zone"]
EST_Zone_Aval = st.session_state.ma_zone == "Zone_Aval"

# --- BARRE LATÉRALE ---
st.sidebar.markdown("### Coordination Inter-Zones")
st.sidebar.markdown(f"**Utilisateur :** {st.session_state.utilisateur['nom']}")
st.sidebar.markdown(f"**Zone :** {st.session_state.ma_zone}")

if st.sidebar.button("Déconnexion"):
    st.session_state.utilisateur = None
    st.rerun()

st.sidebar.divider()

df_all = get_all_reclamations()
nb_a_traiter = 0
if not df_all.empty:
    nb_a_traiter = len(df_all[
        (df_all["zone_destination"] == st.session_state.ma_zone) &
        (df_all["statut"] != "Résolu")
    ])

    # NOUVEAU : escalade visuelle des tickets non pris en charge
    d_att = df_all[(df_all["zone_destination"] == st.session_state.ma_zone) &
                   (df_all["statut"] == "Ouvert")].copy()
    if not d_att.empty:
        d_att["_att"] = (pd.Timestamp.now() - pd.to_datetime(d_att["date_creation"], errors="coerce")
                         ).dt.total_seconds() / 60
        n_retard = int((d_att["_att"] >= SEUIL_ESCALADE_MIN).sum())
        if n_retard > 0:
            st.sidebar.error(f"{n_retard} ticket(s) en attente depuis plus de {SEUIL_ESCALADE_MIN} min")

label_dash = "File à traiter" if not EST_Zone_Aval else "Mes réclamations"
if nb_a_traiter > 0:
    label_dash += f" ({nb_a_traiter})"

if EST_Zone_Aval:
    pages = ["Nouvelle réclamation", label_dash, "Analyse & Historique"]
else:
    pages = [label_dash, "Analyse & Historique"]

page = st.sidebar.radio("Navigation", pages)

# ============================================================
# PAGE — NOUVELLE RÉCLAMATION
# ============================================================
if page == "Nouvelle réclamation":
    afficher_header()
    st.subheader("Déclarer un manquant / retard")
    
    autre_zone = [z for z in ZONES if z != st.session_state.ma_zone][0]

    # Compteur pour vider le champ référence après un envoi
    if "form_n" not in st.session_state:
        st.session_state.form_n = 0

    # La référence est HORS du formulaire pour que l'alerte réagisse à la saisie
    reference = st.text_input(
        "Référence pièce (ex: GALIA)", key=f"ref_{st.session_state.form_n}"
    ).strip().upper()

    # --- Alerte de récurrence ---
    if reference:
        hist = get_historique_reference(reference)
        if hist is None:
            st.success("Première signalisation pour cette référence.")
        else:
            msg_alerte = (f"Référence **{reference}** déjà signalée **{hist['nb_total']} fois** "
                          f"({hist['nb_30j']} sur les 30 derniers jours). "
                          f"Dernier incident : « {hist['dernier_type']} » le {hist['dernier_date']} "
                          f"({hist['dernier_statut']}).")
            if hist["dernier_commentaire"]:
                msg_alerte += f" Dernière note de résolution : « {hist['dernier_commentaire']} »."
            if hist["nb_30j"] >= 3:
                st.error(msg_alerte)
            else:
                st.warning(msg_alerte)

    with st.form("form_reclamation", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            type_probleme = st.selectbox("Type de problème", TYPES_PROBLEME)
            zone_destination = st.selectbox("Zone destinataire", ZONES, index=ZONES.index(autre_zone))
        with col2:
            quantite = st.number_input("Quantité concernée", min_value=1, value=1, step=1)
            temps_perdu = st.number_input("Temps d'arrêt estimé (minutes)", min_value=0.0, step=5.0)
            recepteur = st.text_input("Destinataire / GAP Leader")

        description = st.text_area("Description du problème")

        st.markdown("**Photo complémentaire**")
        tab_cam, tab_up = st.tabs(["Prendre une photo", "Importer un fichier"])
        with tab_cam:
            photo_camera = st.camera_input("Prise de vue")
        with tab_up:
            photo_upload = st.file_uploader("Fichier image", type=["jpg", "png", "jpeg"])

        btn_envoyer = st.form_submit_button("Transmettre la demande")

        if btn_envoyer:
            if not reference or not description:
                st.error("Veuillez remplir la référence et la description.")
            else:
                photo_path = None
                photo_src = photo_camera if photo_camera is not None else photo_upload
                if photo_src is not None:
                    img = Image.open(photo_src)
                    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{reference}.jpg"
                    photo_path = os.path.join(PHOTO_DIR, filename)
                    img.convert("RGB").save(photo_path, "JPEG", quality=85)

                new_id = insert_reclamation(
                    type_probleme, st.session_state.ma_zone, zone_destination,
                    reference, int(quantite), description, temps_perdu,
                    photo_path, st.session_state.utilisateur["nom"], recepteur
                )
                notifier_nouveau_ticket(new_id, reference, type_probleme, zone_destination, description)
                st.session_state.form_n += 1      # vide le champ référence
                st.success(f"Réclamation #{new_id} enregistrée avec succès.")

# ============================================================
# PAGE — DASHBOARD FILE À TRAITER / SUIVI
# ============================================================
elif page == label_dash:
    afficher_header()
    df = get_all_reclamations()

    if EST_Zone_Aval:
        st.subheader("Suivi des demandes émises")
        df_mine = df[df["zone_source"] == st.session_state.ma_zone] if not df.empty else df
        
        t1, t2 = st.tabs(["En cours / Ouverts", "Historique complet"])
        with t1:
            df_act = df_mine[df_mine["statut"] != "Résolu"] if not df_mine.empty else df_mine
            if df_act.empty:
                st.info("Aucune demande en cours.")
            else:
                for _, r in df_act.iterrows():
                    afficher_reclamation_card(r, mode_traitement=False, prefix="Zone_Aval_act")
        with t2:
            if df_mine.empty:
                st.info("Aucun historique disponible.")
            else:
                for _, r in df_mine.iterrows():
                    afficher_reclamation_card(r, mode_traitement=False, prefix="Zone_Aval_all")
    else:
        st.subheader("File de traitement des manquants")
        df_mine = df[df["zone_destination"] == st.session_state.ma_zone] if not df.empty else df
        
        t1, t2 = st.tabs(["À traiter (Ouverts / En cours)", "Résolus"])
        with t1:
            df_act = df_mine[df_mine["statut"] != "Résolu"] if not df_mine.empty else df_mine
            if df_act.empty:
                st.info("Aucun manquant à traiter.")
            else:
                for _, r in df_act.iterrows():
                    afficher_reclamation_card(r, mode_traitement=True, prefix="Zone_Amont_act")
        with t2:
            df_res = df_mine[df_mine["statut"] == "Résolu"] if not df_mine.empty else df_mine
            if df_res.empty:
                st.info("Aucun ticket résolu.")
            else:
                for _, r in df_res.iterrows():
                    afficher_reclamation_card(r, mode_traitement=True, prefix="Zone_Amont_res")

# ============================================================
# PAGE — ANALYSE & HISTORIQUE
# ============================================================
elif page == "Analyse & Historique":
    afficher_header()
    st.subheader("Analyse de récurrence et indicateurs")

    df = get_all_reclamations()

    if df.empty:
        st.info("Pas encore de données enregistrées.")
    else:
        m1, m2, m3, m4, m5 = st.columns(5)
        total_tickets = len(df)
        resolus = len(df[df["statut"] == "Résolu"])
        taux_res = round((resolus / total_tickets) * 100, 1) if total_tickets > 0 else 0
        temps_total = pd.to_numeric(df["temps_perdu_estime"], errors="coerce").fillna(0).sum()
        mttr = get_mttr_global()

        m1.metric("Total incidents", total_tickets)
        m2.metric("Résolus", resolus)
        m3.metric("Taux de résolution", f"{taux_res}%")
        m4.metric("Temps moyen de résolution", formater_temps(mttr) if mttr else "n/d")
        m5.metric("Coût des arrêts", f"{temps_total * COUT_MINUTE_LIGNE:,.0f} €".replace(",", " "),
                  help=f"{int(temps_total)} min × {COUT_MINUTE_LIGNE} €/min")

        st.divider()

        df_ref = get_recurrence_par_reference()
        df_type = get_recurrence_par_type()

        c_g1, c_g2 = st.columns(2)
        with c_g1:
            st.markdown("### Récurrence par référence")
            if not df_ref.empty:
                st.bar_chart(df_ref.set_index("reference")["nb_occurrences"], color="#00A3E0")
        
        with c_g2:
            st.markdown("### Répartition par type de problème")
            if not df_type.empty:
                st.bar_chart(df_type.set_index("type_probleme")["nb_occurrences"], color="#0051A5")

        # --- NOUVEAU : Pareto 80/20 ---
        st.divider()
        st.markdown("### Pareto 80/20 : références par temps d'arrêt")
        df_par = df_ref[df_ref["temps_perdu_total"] > 0].sort_values("temps_perdu_total", ascending=False).copy()
        if df_par.empty:
            st.caption("Pas encore de temps d'arrêt renseigné.")
        else:
            df_par["cumul_pct"] = df_par["temps_perdu_total"].cumsum() / df_par["temps_perdu_total"].sum() * 100
            base = alt.Chart(df_par.head(15)).encode(x=alt.X("reference:N", sort=None, title="Référence"))
            barres = base.mark_bar(color="#0051A5").encode(y=alt.Y("temps_perdu_total:Q", title="Minutes perdues"))
            ligne = base.mark_line(color="#E4572E", point=True).encode(
                y=alt.Y("cumul_pct:Q", title="% cumulé", scale=alt.Scale(domain=[0, 100])))
            st.altair_chart(alt.layer(barres, ligne).resolve_scale(y="independent"), use_container_width=True)

        # --- NOUVEAU : Heatmap jour × heure ---
        st.markdown("### Quand les incidents arrivent-ils ? (jour × heure)")
        d_heat = df.copy()
        d_heat["_d"] = pd.to_datetime(d_heat["date_creation"], errors="coerce")
        d_heat = d_heat.dropna(subset=["_d"])
        if d_heat.empty:
            st.caption("Pas encore de données.")
        else:
            jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
            d_heat["jour"] = d_heat["_d"].dt.dayofweek.map(lambda i: jours[i])
            d_heat["heure"] = d_heat["_d"].dt.hour
            heat = d_heat.groupby(["jour", "heure"]).size().reset_index(name="nb")
            st.altair_chart(
                alt.Chart(heat).mark_rect().encode(
                    x=alt.X("heure:O", title="Heure"),
                    y=alt.Y("jour:N", sort=jours, title=None),
                    color=alt.Color("nb:Q", scale=alt.Scale(scheme="blues"), title="Incidents"),
                    tooltip=["jour", "heure", "nb"],
                ).properties(height=250),
                use_container_width=True,
            )

        # --- NOUVEAU : MTTR par type ---
        st.markdown("### Temps moyen de résolution par type")
        df_mttr = get_mttr_par_type()
        if df_mttr.empty:
            st.caption("Disponible dès que des tickets seront résolus avec la nouvelle version.")
        else:
            st.bar_chart(df_mttr.set_index("type_probleme")["mttr_min"], color="#00A3E0")

        st.divider()
        st.markdown("### Recommandations d'actions")
        suggestions = generer_suggestions(df_ref, df_type, df)
        if suggestions:
            for s in suggestions:
                texte = f"**[{s['priorite']} · {s['type']}]** — {s['message']}"
                if s["priorite"] == "Critique":
                    st.error(texte)
                elif s["priorite"] == "Élevée":
                    st.warning(texte)
                else:
                    st.info(texte)
        else:
            st.info("Aucune récurrence critique détectée.")

        st.divider()
        with st.expander("Consulter la base de données brute"):
            st.dataframe(df, use_container_width=True)
            
            st.markdown("### Exporter l'historique")
            col_exp1, col_exp2 = st.columns(2)
            
            # Export CSV
            with col_exp1:
                csv_data = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="Exporter en CSV (.csv)",
                    data=csv_data,
                    file_name=f"historique_reclamations_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            # Export Excel (.xlsx)
            with col_exp2:
                try:
                    excel_data = convertir_df_en_excel(df)
                    st.download_button(
                        label="Exporter en Excel (.xlsx)",
                        data=excel_data,
                        file_name=f"historique_reclamations_{datetime.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                except Exception:
                    st.warning("Pour exporter en Excel, installez le package 'openpyxl' (`pip install openpyxl`).")