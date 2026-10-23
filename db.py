"""
db.py — Gestion de la base de données SQLite pour l'outil de coordination inter-zones
"""

import sqlite3
import pandas as pd
from datetime import datetime

DB_NAME = "reclamations.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Table principale des réclamations
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reclamations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_creation TEXT,
            type_probleme TEXT,
            zone_source TEXT,
            zone_destination TEXT,
            reference TEXT,
            quantite INTEGER,
            description TEXT,
            temps_perdu_estime REAL,
            photo_path TEXT,
            emetteur TEXT,
            recepteur TEXT,
            statut TEXT DEFAULT 'Ouvert',
            date_prise_en_charge TEXT,
            commentaire_resolution TEXT,
            temps_estime_ia REAL,
            estimation_fiable INTEGER DEFAULT 0,
            date_resolution TEXT
        )
    ''')

    # Table de messagerie inter-zones par ticket
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages_reclamation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reclamation_id INTEGER,
            expediteur TEXT,
            zone TEXT,
            message TEXT,
            date_envoi TEXT,
            FOREIGN KEY (reclamation_id) REFERENCES reclamations (id)
        )
    ''')

    conn.commit()
    conn.close()


def init_users_table():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS utilisateurs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            code TEXT,
            nom TEXT,
            zone TEXT
        )
    ''')

    # Comptes de démonstration (à remplacer en production)
    utilisateurs_defaut = [
        ("Zone_Aval@example.com", "1234", "Opérateur Zone_Aval", "Zone_Aval"),
        ("Zone_Amont@example.com", "1234", "Opérateur Zone_Amont", "Zone_Amont")
    ]

    for email, code, nom, zone in utilisateurs_defaut:
        cursor.execute('''
            INSERT OR IGNORE INTO utilisateurs (email, code, nom, zone)
            VALUES (?, ?, ?, ?)
        ''', (email, code, nom, zone))

    conn.commit()
    conn.close()


def migrer_colonnes_suivi():
    """Ajoute les colonnes de suivi si elles n'existent pas déjà."""
    conn = get_connection()
    cursor = conn.cursor()

    cols_a_ajouter = [
        ("statut", "TEXT DEFAULT 'Ouvert'"),
        ("date_prise_en_charge", "TEXT"),
        ("commentaire_resolution", "TEXT"),
        ("temps_estime_ia", "REAL"),
        ("estimation_fiable", "INTEGER DEFAULT 0"),
        ("recepteur", "TEXT"),
        ("temps_perdu_estime", "REAL"),
        ("date_resolution", "TEXT")          # NOUVEAU : date réelle de résolution
    ]

    cursor.execute("PRAGMA table_info(reclamations)")
    existantes = [col[1] for col in cursor.fetchall()]

    for col, type_col in cols_a_ajouter:
        if col not in existantes:
            try:
                cursor.execute(f"ALTER TABLE reclamations ADD COLUMN {col} {type_col}")
            except Exception:
                pass

    conn.commit()
    conn.close()


def verifier_connexion(email, code):
    conn = get_connection()
    cursor = conn.cursor()
    email = (email or "").strip().lower()
    code = (code or "").strip()
    cursor.execute("SELECT id, email, nom, zone FROM utilisateurs WHERE LOWER(email) = ? AND code = ?", (email, code))
    user = cursor.fetchone()
    conn.close()

    if user:
        return {"id": user[0], "email": user[1], "nom": user[2], "zone": user[3]}
    return None


def estimer_temps_resolution(type_probleme, reference, quantite):
    """
    Estime le temps de résolution à partir de l'historique réel :
    1) moyenne sur (référence + type), 2) moyenne sur le type seul,
    3) sinon formule de secours. Retourne (minutes, fiable 0/1).
    """
    conn = get_connection()
    requete = '''
        SELECT AVG((julianday(date_resolution) - julianday(date_creation)) * 1440), COUNT(*)
        FROM reclamations
        WHERE statut = 'Résolu' AND date_resolution IS NOT NULL AND {}
    '''
    essais = [
        ("reference = ? AND type_probleme = ?", (reference, type_probleme)),
        ("type_probleme = ?", (type_probleme,)),
    ]
    for cond, params in essais:
        moy, nb = conn.execute(requete.format(cond), params).fetchone()
        if nb >= 3 and moy is not None:
            conn.close()
            return round(moy, 0), 1
    conn.close()
    return 15.0 + quantite * 5.0, 0


def insert_reclamation(type_probleme, zone_source, zone_destination, reference, quantite, description, temps_perdu, photo_path, emetteur, recepteur):
    conn = get_connection()
    cursor = conn.cursor()

    date_actuelle = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Estimation basée sur l'historique réel (formule de secours si pas assez de données)
    temps_estime, fiable = estimer_temps_resolution(type_probleme, reference, quantite)

    cursor.execute('''
        INSERT INTO reclamations (
            date_creation, type_probleme, zone_source, zone_destination,
            reference, quantite, description, temps_perdu_estime, photo_path,
            emetteur, recepteur, statut, temps_estime_ia, estimation_fiable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Ouvert', ?, ?)
    ''', (date_actuelle, type_probleme, zone_source, zone_destination, reference,
          quantite, description, temps_perdu, photo_path, emetteur, recepteur,
          temps_estime, fiable))

    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_all_reclamations():
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM reclamations ORDER BY id DESC", conn)
    conn.close()
    return df


def prendre_en_charge(reclamation_id):
    conn = get_connection()
    cursor = conn.cursor()
    date_prise = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE reclamations
        SET statut = 'En cours', date_prise_en_charge = ?
        WHERE id = ?
    ''', (date_prise, reclamation_id))
    conn.commit()
    conn.close()


def mettre_a_jour_commentaire(reclamation_id, commentaire):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE reclamations
        SET commentaire_resolution = ?
        WHERE id = ?
    ''', (commentaire, reclamation_id))
    conn.commit()
    conn.close()


def marquer_resolu(reclamation_id, commentaire):
    conn = get_connection()
    cursor = conn.cursor()
    date_res = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE reclamations
        SET statut = 'Résolu', commentaire_resolution = ?, date_resolution = ?
        WHERE id = ?
    ''', (commentaire, date_res, reclamation_id))
    conn.commit()
    conn.close()


def ajouter_message(reclamation_id, expediteur, zone, message):
    conn = get_connection()
    cursor = conn.cursor()
    date_envoi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO messages_reclamation (reclamation_id, expediteur, zone, message, date_envoi)
        VALUES (?, ?, ?, ?, ?)
    ''', (reclamation_id, expediteur, zone, message, date_envoi))
    conn.commit()
    conn.close()


def get_messages(reclamation_id):
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM messages_reclamation WHERE reclamation_id = ? ORDER BY id ASC",
        conn,
        params=(reclamation_id,)
    )
    conn.close()
    return df


def get_recurrence_par_reference():
    """Récurrence par référence, avec quantités et temps d'arrêt cumulés."""
    conn = get_connection()
    df = pd.read_sql_query('''
        SELECT
            reference,
            COUNT(*) AS nb_occurrences,
            ROUND(AVG(quantite), 1) AS quantite_moyenne,
            SUM(quantite) AS quantite_totale,
            ROUND(SUM(COALESCE(temps_perdu_estime, 0)), 1) AS temps_perdu_total,
            MAX(date_creation) AS derniere_occurrence
        FROM reclamations
        GROUP BY reference
        ORDER BY nb_occurrences DESC
    ''', conn)
    conn.close()
    return df


def get_recurrence_par_type():
    conn = get_connection()
    df = pd.read_sql_query('''
        SELECT type_probleme, COUNT(*) as nb_occurrences
        FROM reclamations
        GROUP BY type_probleme
        ORDER BY nb_occurrences DESC
    ''', conn)
    conn.close()
    return df


# ============================================================
# NOUVEAU : indicateurs de résolution et historique par référence
# ============================================================
def get_mttr_global():
    """Temps moyen de résolution (minutes) sur tous les tickets résolus."""
    conn = get_connection()
    moy = conn.execute('''
        SELECT AVG((julianday(date_resolution) - julianday(date_creation)) * 1440)
        FROM reclamations WHERE statut = 'Résolu' AND date_resolution IS NOT NULL
    ''').fetchone()[0]
    conn.close()
    return moy


def get_mttr_par_type():
    conn = get_connection()
    df = pd.read_sql_query('''
        SELECT type_probleme, COUNT(*) AS nb_resolus,
               ROUND(AVG((julianday(date_resolution) - julianday(date_creation)) * 1440), 0) AS mttr_min
        FROM reclamations
        WHERE statut = 'Résolu' AND date_resolution IS NOT NULL
        GROUP BY type_probleme
    ''', conn)
    conn.close()
    return df


def get_historique_reference(reference):
    """Résumé de l'historique d'une référence (pour l'alerte à la saisie)."""
    df = get_all_reclamations()
    if df.empty or not reference:
        return None
    sous = df[df["reference"] == reference].copy()
    if sous.empty:
        return None
    sous["_d"] = pd.to_datetime(sous["date_creation"], errors="coerce")
    nb_30j = int((sous["_d"] >= pd.Timestamp.now() - pd.Timedelta(days=30)).sum())
    dernier = sous.sort_values("id", ascending=False).iloc[0]
    commentaire = dernier.get("commentaire_resolution")
    return {
        "nb_total": len(sous),
        "nb_30j": nb_30j,
        "dernier_type": dernier["type_probleme"],
        "dernier_statut": dernier["statut"],
        "dernier_date": dernier["date_creation"],
        "dernier_commentaire": commentaire if isinstance(commentaire, str) and commentaire.strip() else None,
    }