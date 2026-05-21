# -*- coding: utf-8 -*-
"""Sync engine — STUB post-disconnessione Google Sheets (2026-05-21).

Storicamente questo modulo conteneva:
  - pull_once: Sheets → DB (merge campo-per-campo, conflict resolution)
  - drain_once: DB → Sheets (push batch di pending_writes)
  - pattern validation, dispatch cross-colonna, auto-correzione

Il 21/05/2026 il sistema di dispatch automatico ha causato uno span massivo
(numeri di telefono spostati nella colonna SIGILLO perché matchavano il
pattern `\\d{8,14}`). A seguito di quell'incidente è stato deciso di
scollegare completamente Google Sheets: la fonte di verità è ora il DB
locale SQLite, e i dati esterni entrano via import Excel manuale.

Questo file resta come stub per non rompere gli import esistenti. Le
funzioni ritornano risultati success-like senza effetti collaterali.
"""
import logging

from . import db

logger = logging.getLogger('mappa_algeco')


def pull_once(values=None):
    """No-op: il programma non legge più da Sheets."""
    return {
        'rows': 0, 'changed': 0, 'new': 0, 'conflicts': 0,
        'orphans': 0, 'duplicates': 0, 'elapsed': 0.0,
        'skipped': True, 'reason': 'sheets_disconnected',
    }


def drain_once(*, batch_size=None):
    """No-op: il programma non scrive più su Sheets."""
    return {'drained': 0, 'skipped': True, 'reason': 'sheets_disconnected'}


def trigger_drain_now():
    """No-op: il drainer non esiste più."""
    return


def start_workers():
    """No-op: nessun worker da avviare. Esegue solo integrity check del DB."""
    integrity = db.integrity_check()
    if integrity != 'ok':
        logger.error(f"[sync_engine] PRAGMA integrity_check FAIL: {integrity}")
    else:
        logger.info(f"[sync_engine] DB integrity OK: {db.stats()}")
    logger.info("[sync_engine] modalità DB-only (Google Sheets disconnesso)")


def stop_workers():
    """No-op: nessun worker attivo."""
    return


# ─────────────────────────────────────────────────────────────────────
# Stub di compatibilità per i chiamanti residui
# (saranno rimossi quando aggiorneremo server.py / route)
# ─────────────────────────────────────────────────────────────────────
def reset_rules_cache():
    """No-op: il sistema di pattern validation è stato eliminato."""
    return
