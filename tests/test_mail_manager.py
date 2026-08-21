# -*- coding: utf-8 -*-
"""
Test unitari per moduli/mail_manager.py e relative route.
"""

import time
from moduli import mail_manager


def test_seed_categories_and_templates(db_isolato):
    """Verifica che al bootstrap vengano create le categorie e i template standard."""
    cats = mail_manager.list_categories(prov='torino')
    assert len(cats) >= 6
    cat_ids = [c['id'] for c in cats]
    assert 'clienti_appuntamenti' in cat_ids
    assert 'comuni_enti' in cat_ids
    assert 'distinte_enel' in cat_ids
    assert 'tecnici_operativi' in cat_ids
    assert 'solleciti' in cat_ids
    assert 'amministrazione' in cat_ids

    tpls = mail_manager.list_templates_and_snippets(prov='torino')
    assert len(tpls) >= 4
    types = [t['type'] for t in tpls]
    assert 'template' in types
    assert 'snippet' in types


def test_save_draft_and_get_message(db_isolato):
    """Verifica il salvataggio e recupero di una bozza."""
    draft_data = {
        'to': ['cliente@example.com', 'ufficio@example.com'],
        'subject': 'Richiesta appuntamento POD IT001E12345678',
        'body_html': '<p>Gentile Cliente, si richiede appuntamento per il contatore.</p>',
        'body_text': 'Gentile Cliente, si richiede appuntamento per il contatore.',
        'category_id': 'clienti_appuntamenti',
        'linked_pod': 'IT001E12345678'
    }
    res = mail_manager.save_draft(draft_data, prov='torino')
    assert res['success'] is True
    draft_id = res['id']
    
    msg = mail_manager.get_message(draft_id, prov='torino')
    assert msg is not None
    assert msg['folder'] == 'drafts'
    assert msg['subject'] == 'Richiesta appuntamento POD IT001E12345678'
    assert 'cliente@example.com' in msg['to_addrs']
    assert msg['linked_pod'] == 'IT001E12345678'
    assert msg['category_id'] == 'clienti_appuntamenti'


def test_list_messages_with_filters(db_isolato):
    """Verifica il listing e filtraggio dei messaggi."""
    db = db_isolato
    conn = db.get_conn()
    now = int(time.time())
    
    # Inserisci alcuni messaggi di test
    conn.execute(
        """INSERT INTO mail_messages (
            provincia, folder, from_addr, to_addrs, subject, snippet, category_id, is_read, is_starred,
            received_ts, created_ts, updated_ts
        ) VALUES 
        ('torino', 'inbox', 'comune.chieri@pec.it', '["tecnico@algeco.it"]', 'Ordinanza ZTL Comune di Chieri', 'Si trasmette ordinanza', 'comuni_enti', 0, 1, ?, ?, ?),
        ('torino', 'inbox', 'mario.rossi@gmail.com', '["tecnico@algeco.it"]', 'Spostamento data intervento', 'Vorrei spostare', 'clienti_appuntamenti', 1, 0, ?, ?, ?),
        ('torino', 'inbox', 'enel@e-distribuzione.it', '["tecnico@algeco.it"]', 'Distinta Maxy Lotto 4', 'Nuove distinte', 'distinte_enel', 0, 0, ?, ?, ?)
        """,
        (now, now, now, now - 100, now - 100, now - 100, now - 200, now - 200, now - 200)
    )
    
    # Listing globale inbox
    res_all = mail_manager.list_messages(folder='inbox', prov='torino')
    assert res_all['total'] >= 3
    
    # Filtro unread
    res_unread = mail_manager.list_messages(folder='inbox', unread_only=True, prov='torino')
    assert res_unread['total'] == 2
    
    # Filtro category
    res_cat = mail_manager.list_messages(folder='inbox', category_id='comuni_enti', prov='torino')
    assert res_cat['total'] == 1
    assert res_cat['messages'][0]['from_addr'] == 'comune.chieri@pec.it'
    
    # Ricerca full-text
    res_search = mail_manager.list_messages(folder='inbox', query='Chieri', prov='torino')
    assert res_search['total'] == 1
    assert 'Chieri' in res_search['messages'][0]['subject']


def test_batch_actions_and_trash(db_isolato):
    """Verifica le azioni batch (mark_read, star, move, delete)."""
    db = db_isolato
    conn = db.get_conn()
    now = int(time.time())
    
    cur1 = conn.execute(
        """INSERT INTO mail_messages (
            provincia, folder, from_addr, to_addrs, subject, is_read, is_starred,
            received_ts, created_ts, updated_ts
        ) VALUES ('torino', 'inbox', 'test1@example.com', '["to@algeco.it"]', 'Test 1', 0, 0, ?, ?, ?)""",
        (now, now, now)
    )
    id1 = cur1.lastrowid
    
    cur2 = conn.execute(
        """INSERT INTO mail_messages (
            provincia, folder, from_addr, to_addrs, subject, is_read, is_starred,
            received_ts, created_ts, updated_ts
        ) VALUES ('torino', 'inbox', 'test2@example.com', '["to@algeco.it"]', 'Test 2', 0, 0, ?, ?, ?)""",
        (now, now, now)
    )
    id2 = cur2.lastrowid
    
    # Mark read batch
    mail_manager.batch_actions([id1, id2], 'mark_read', prov='torino')
    m1 = mail_manager.get_message(id1, prov='torino')
    m2 = mail_manager.get_message(id2, prov='torino')
    assert m1['is_read'] == 1
    assert m2['is_read'] == 1
    
    # Star batch
    mail_manager.batch_actions([id1, id2], 'star', prov='torino')
    m1 = mail_manager.get_message(id1, prov='torino')
    assert m1['is_starred'] == 1
    
    # Move to trash
    mail_manager.delete_message(id1, permanent=False, prov='torino')
    m1 = mail_manager.get_message(id1, prov='torino')
    assert m1['folder'] == 'trash'
    
    # Permanent delete
    mail_manager.delete_message(id1, permanent=True, prov='torino')
    m1_deleted = mail_manager.get_message(id1, prov='torino')
    assert m1_deleted is None


def test_auto_categorization(db_isolato):
    """Verifica l'auto-categorizzazione intelligente basata su parole chiave e rubrica."""
    db = db_isolato
    conn = db.get_conn()
    
    # Inserisci un comune in comuni_info
    conn.execute(
        """INSERT OR REPLACE INTO comuni_info (
            provincia, comune, email_comune, last_updated_ts
        ) VALUES ('torino', 'Moncalieri', 'protocollo@comune.moncalieri.to.it', ?)""",
        (int(time.time()),)
    )
    
    # 1. Email da comune
    res_comune = mail_manager.auto_categorize_email(
        from_addr='protocollo@comune.moncalieri.to.it',
        to_addrs=['tecnico@algeco.it'],
        subject='Autorizzazione occupazione suolo pubblico',
        prov='torino'
    )
    assert res_comune['category_id'] == 'comuni_enti'
    assert res_comune['linked_comune'] == 'Moncalieri'
    
    # 2. Email con richiesta appuntamento
    res_app = mail_manager.auto_categorize_email(
        from_addr='utente@gmail.com',
        to_addrs=['tecnico@algeco.it'],
        subject='Richiesta spostamento appuntamento contatore',
        body_text='Buongiorno vorrei concordare una fascia oraria per la presenza.',
        prov='torino'
    )
    assert res_app['category_id'] == 'clienti_appuntamenti'
    
    # 3. Email con sollecito urgente
    res_soll = mail_manager.auto_categorize_email(
        from_addr='cliente@pec.it',
        to_addrs=['tecnico@algeco.it'],
        subject='Sollecito urgente intervento mancato',
        body_text='Si richiede con urgenza riscontro alla presente.',
        prov='torino'
    )
    assert res_soll['category_id'] == 'solleciti'


def test_scheduled_mail_queue(db_isolato, monkeypatch):
    """Verifica la programmazione e il processamento della coda email."""
    db = db_isolato
    now = int(time.time())
    
    # Simula invio SMTP sempre riuscito
    monkeypatch.setattr(mail_manager, 'send_smtp_message', lambda settings, msg, max_retries=3, save_sent=True: (True, None))
    
    # Programma email per 2 ore dopo
    res = mail_manager.send_or_schedule_mail({
        'to': ['sched@example.com'],
        'subject': 'Email programmata per dopo',
        'body_text': 'Contenuto programmato',
        'scheduled_at': now + 7200
    }, prov='torino')
    
    assert res['success'] is True
    assert res.get('scheduled') is True
    mid = res['mail_id']
    
    msg = mail_manager.get_message(mid, prov='torino')
    assert msg['folder'] == 'scheduled'
    assert msg['status'] == 'scheduled'
    
    # Esegui worker quando l'orario non è ancora scaduto -> 0 processati
    q_res1 = mail_manager.process_scheduled_queue(prov='torino')
    assert q_res1['processed'] == 0
    
    # Aggiorna il timestamp programmato nel passato
    conn = db.get_conn()
    conn.execute("UPDATE mail_messages SET scheduled_ts = ? WHERE id = ?", (now - 10, mid))
    
    # Esegui worker -> inviata!
    q_res2 = mail_manager.process_scheduled_queue(prov='torino')
    assert q_res2['processed'] == 1
    assert q_res2['sent'] == 1
    
    msg_sent = mail_manager.get_message(mid, prov='torino')
    assert msg_sent['folder'] == 'sent'
    assert msg_sent['status'] == 'sent'


def test_contacts_autocomplete_search(db_isolato):
    """Verifica la ricerca autocompletamento contatti unificata."""
    db = db_isolato
    conn = db.get_conn()
    now = int(time.time())
    
    conn.execute(
        """INSERT OR REPLACE INTO comuni_info (provincia, comune, email_uff_tecnico, last_updated_ts)
           VALUES ('torino', 'Rivoli', 'ufficiotecnico@comune.rivoli.to.it', ?)""",
        (now,)
    )
    conn.execute(
        """INSERT OR REPLACE INTO lettere_contatti (provincia, contact_key, nominativo, email, created_ts, updated_ts)
           VALUES ('torino', 'AZ-1', 'Officine Meccaniche Rivoli', 'info@officinerivoli.it', ?, ?)""",
        (now, now)
    )
    
    res = mail_manager.search_contacts_autocomplete('Rivoli', prov='torino')
    assert len(res) >= 2
    emails = [r['email'] for r in res]
    assert 'ufficiotecnico@comune.rivoli.to.it' in emails
    assert 'info@officinerivoli.it' in emails


def test_mail_flask_routes(db_isolato, monkeypatch):
    """Verifica le API REST Flask montate su mail_bp."""
    from flask import Flask
    from routes.mail import bp as mail_bp
    
    app = Flask(__name__, template_folder='../templates')
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    app.register_blueprint(mail_bp)
    
    # Mock autenticazione sessione
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user'] = 'admin'
        
    # 1. GET /api/mail/stats
    res_stats = client.get('/api/mail/stats')
    assert res_stats.status_code == 200
    d_stats = res_stats.get_json()
    assert d_stats['success'] is True
    assert 'inbox' in d_stats['stats']['folders']
    
    # 2. GET /api/mail/categories
    res_cats = client.get('/api/mail/categories')
    assert res_cats.status_code == 200
    d_cats = res_cats.get_json()
    assert d_cats['success'] is True
    assert len(d_cats['categories']) >= 6
    
    # 3. GET /api/mail/templates
    res_tpls = client.get('/api/mail/templates')
    assert res_tpls.status_code == 200
    d_tpls = res_tpls.get_json()
    assert d_tpls['success'] is True
    assert len(d_tpls['templates']) >= 5
    
    # 4. POST /api/mail/save-draft
    res_draft = client.post('/api/mail/save-draft', json={
        'to': ['cliente@example.com'],
        'subject': 'Bozza di test',
        'body_html': '<p>Ciao Mondo</p>'
    })
    assert res_draft.status_code == 200
    d_draft = res_draft.get_json()
    assert d_draft['success'] is True
    draft_id = d_draft['id']
    
    # 5. GET /api/mail/messages
    res_msgs = client.get('/api/mail/messages?folder=drafts')
    assert res_msgs.status_code == 200
    d_msgs = res_msgs.get_json()
    assert d_msgs['success'] is True
    assert any(m['id'] == draft_id for m in d_msgs['messages'])
    
    # 6. POST /api/mail/resolve-template
    res_resolve = client.post('/api/mail/resolve-template', json={
        'text': 'Contatore POD: {pod} per {nome}',
        'variables': {'pod': 'IT001E123', 'nome': 'Mario Rossi'}
    })
    assert res_resolve.status_code == 200
    d_res = res_resolve.get_json()
    assert d_res['success'] is True
    assert d_res['resolved'] == 'Contatore POD: IT001E123 per Mario Rossi'
    
    # 7. GET /api/mail/contacts-search
    res_search = client.get('/api/mail/contacts-search?q=test')
    assert res_search.status_code == 200
    d_search = res_search.get_json()
    assert d_search['success'] is True

