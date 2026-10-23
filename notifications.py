"""
notifications.py — Notification optionnelle (Teams / Slack via webhook entrant)
Laisser TEAMS_WEBHOOK_URL vide pour désactiver.
"""

import os
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")


def notifier_nouveau_ticket(ticket_id, reference, type_pb, zone_dest, description):
    if not TEAMS_WEBHOOK_URL:
        return
    try:
        import requests  # importé ici : l'app fonctionne même sans le package
        texte = (f"**Nouveau ticket #{ticket_id}** → {zone_dest}\n\n"
                 f"Réf : {reference} | {type_pb}\n\n{description}")
        requests.post(TEAMS_WEBHOOK_URL, json={"text": texte}, timeout=5)
    except Exception:
        pass  # ne jamais bloquer l'app à cause d'une notification