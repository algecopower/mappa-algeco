# -*- coding: utf-8 -*-
"""
Test per gestione template settings, recapiti provinciali, CSRF e risoluzione firme.
"""
import os
import secrets
from flask import Flask
from moduli import mail_manager
from moduli.safe_json import json_write
import router


def test_save_and_get_template_settings(tmp_path, monkeypatch):
    """Verifica salvataggio recapiti provinciali e sincronizzazione."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data'), exist_ok=True)
    os.makedirs(str(tmp_path / 'data' / 'pavia'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {'settings': {'firma_html': 'Test {email_tecnico} {telefono_tecnico}'}}, indent=4)

    prov_json = str(tmp_path / 'data' / 'provinces.json')
    json_write(prov_json, {
        'TORINO': {'nome': 'Torino'},
        'PAVIA': {'nome': 'Pavia'}
    }, indent=4)

    # Salva nuovi recapiti per Pavia
    settings_payload = {
        'province': {
            'PAVIA': {'nome': 'Pavia', 'email': 'tecnico_pv1@algecopower.it', 'telefono': '0382 123456'},
            'TORINO': {'nome': 'Torino', 'email': 'tecnico_to2@algecopower.it', 'telefono': '011 025 2702'}
        },
        'firma_html': '<p>{email_tecnico} - {telefono_tecnico}</p>'
    }

    res = mail_manager.save_template_settings(settings_payload, prov='pavia')
    assert res['success'] is True

    # Verifica get_template_settings legge correttamente i valori salvati
    st = mail_manager.get_template_settings(prov='pavia')
    assert st['province']['PAVIA']['telefono'] == '0382 123456'
    assert st['province']['TORINO']['telefono'] == '011 025 2702'
    assert st['firma_html'] == '<p>{email_tecnico} - {telefono_tecnico}</p>'


def test_resolve_signature_html(tmp_path, monkeypatch):
    """Verifica risoluzione dinamica di email e telefono nella firma."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'settings': {
            'province': {
                'PAVIA': {'nome': 'Pavia', 'email': 'tecnico_pv1@algecopower.it', 'telefono': '0382 999888'}
            },
            'firma_html': 'Tel: <a href="tel:+39{telefono_tecnico_clean}">{telefono_tecnico}</a> Mail: {email_tecnico}'
        }
    }, indent=4)

    prov_json = str(tmp_path / 'data' / 'provinces.json')
    json_write(prov_json, {'PAVIA': {'nome': 'Pavia'}}, indent=4)

    raw = 'Tel: <a href="tel:+39{telefono_tecnico_clean}">{telefono_tecnico}</a> Mail: {email_tecnico}'
    resolved = mail_manager.resolve_signature_html(raw, prov='pavia')
    assert '0382 999888' in resolved
    assert '0382999888' in resolved
    assert 'tecnico_pv1@algecopower.it' in resolved


def test_router_admin_csrf_check_accepts_both_header_variants():
    """Verifica che router._admin_csrf_check accetti sia X-CSRF-Token che X-CSRFToken."""
    app = Flask(__name__)

    token = 'test_token_abc123'
    # Test con X-CSRF-Token
    with app.test_request_context('/admin', method='POST', headers={'X-CSRF-Token': token, 'Cookie': f'csrf_token={token}'}):
        res = router._admin_csrf_check()
        assert res is None

    # Test con X-CSRFToken
    with app.test_request_context('/admin', method='POST', headers={'X-CSRFToken': token, 'Cookie': f'csrf_token={token}'}):
        res = router._admin_csrf_check()
        assert res is None

    # Test senza header
    with app.test_request_context('/admin', method='POST', headers={'Cookie': f'csrf_token={token}'}):
        res = router._admin_csrf_check()
        assert res is not None
        assert res[1] == 403


def test_server_csrf_logic_accepts_both_headers():
    """Verifica la logica di comparazione CSRF utilizzata in server.py."""
    session_token = secrets.token_urlsafe(32)

    def check_csrf(header_token_name, header_token_val):
        headers = {header_token_name: header_token_val}
        header_token = headers.get('X-CSRF-Token') or headers.get('X-CSRFToken') or ''
        return bool(header_token and session_token and secrets.compare_digest(header_token, session_token))

    assert check_csrf('X-CSRF-Token', session_token) is True
    assert check_csrf('X-CSRFToken', session_token) is True
    assert check_csrf('X-Invalid-Header', session_token) is False
    assert check_csrf('X-CSRF-Token', 'wrong_token') is False


def test_distinte_resolve_template_server_resolves_phone():
    """Verifica che _resolve_template_server risolva telefono_tecnico."""
    from routes import distinte

    tpl = "Tel: {telefono_tecnico} (clean: {telefono_tecnico_clean}) Mail: {email_tecnico}"
    resolved = distinte._resolve_template_server(
        tpl,
        fields={},
        email_tecnico='tecnico_pv1@algecopower.it',
        telefono_tecnico='391 390 1303'
    )
    assert '391 390 1303' in resolved
    assert '3913901303' in resolved
    assert 'tecnico_pv1@algecopower.it' in resolved


def test_dynamic_province_addition_and_removal(tmp_path, monkeypatch):
    """Verifica che aggiungendo o rimuovendo province da provinces.json, get_template_settings si adegui."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'settings': {
            'province': {
                'TORINO': {'nome': 'Torino', 'email': 'to@test.it', 'telefono': '011 025 2702'},
                'PAVIA': {'nome': 'Pavia', 'email': 'pv@test.it', 'telefono': '391 390 1303'},
                'VECCHIA_PROV': {'nome': 'Vecchia', 'email': 'old@test.it', 'telefono': '000'}
            },
            'firma_html': 'Firma: {telefono_tecnico} - {email_tecnico}'
        }
    }, indent=4)

    # provinces.json ha solo TORINO e una NUOVA_PROV
    prov_json = str(tmp_path / 'data' / 'provinces.json')
    json_write(prov_json, {
        'TORINO': {'nome': 'Torino'},
        'NUOVA_PROV': {'nome': 'Nuova Provincia'}
    }, indent=4)

    st = mail_manager.get_template_settings('NUOVA_PROV')
    assert 'NUOVA_PROV' in st['province']
    assert 'TORINO' in st['province']
    assert 'VECCHIA_PROV' not in st['province']
    assert st['province']['NUOVA_PROV']['nome'] == 'Nuova Provincia'
    assert st['province']['NUOVA_PROV']['email'] == 'tecnico_nuova_prov@algecopower.it'

    # Risoluzione firma per NUOVA_PROV
    sig = mail_manager.resolve_signature_html(st['firma_html'], prov='NUOVA_PROV')
    assert 'tecnico_nuova_prov@algecopower.it' in sig
    assert '011 025 2702' in sig

    # Risoluzione firma per TORINO
    sig_to = mail_manager.resolve_signature_html(st['firma_html'], prov='TORINO')
    assert 'to@test.it' in sig_to
    assert '011 025 2702' in sig_to


def test_html_to_plain_text_preserves_spacing():
    """Verifica che html_to_plain_text non incolli le parole e preservi i ritorni a capo."""
    html_input = (
        "Gentile {nome},<br>"
        "come da precedente comunicazione.<br><br>"
        "<p>La sostituzione, OBBLIGATORIA e GRATUITA, è richiesta.</p>"
        "<p>SI PREGA DI RISPONDERE &amp; CONFERMARE.</p>"
        "Per chiarimenti scrivici a <a href=\"https://www.algecopower.it\">Algeco Power</a>&nbsp;oppure chiama."
    )
    plain = mail_manager.html_to_plain_text(html_input)

    # Verifica che le righe non siano incollate insieme
    assert "Gentile {nome},\ncome da precedente comunicazione." in plain
    assert "comunicazione.\n\nLa sostituzione" in plain
    assert "richiesta.\n\nSI PREGA DI RISPONDERE & CONFERMARE." in plain
    assert "Algeco Power (https://www.algecopower.it) oppure chiama." in plain
    assert "<br>" not in plain
    assert "<p>" not in plain
    assert "&nbsp;" not in plain


def test_save_whatsapp_template_normalizes_html_tags(tmp_path, monkeypatch):
    """Verifica che salvando un template WhatsApp con tag HTML <br>, vengano normalizzati in \n."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'templates': {
            'wa_conferma': {
                'label': 'Messaggio Preavviso',
                'type': 'whatsapp',
                'body': 'Testo base'
            }
        }
    }, indent=4)

    payload = {
        'id': 'sys_wa_conferma',
        'title': 'Messaggio Preavviso Modificato',
        'type': 'template',
        'category': 'whatsapp',
        'body': 'Gentile {nome},<br>testo con a capo.<br><br>Nuovo paragrafo.'
    }

    res = mail_manager.save_template_or_snippet(payload, prov='torino')
    assert res['success'] is True

    # Verifica nel file provinciale salvato
    saved_json = str(tmp_path / 'data' / 'torino' / 'distinte_templates.json')
    from moduli.safe_json import json_read
    data = json_read(saved_json)
    body_saved = data['templates']['wa_conferma']['body']

    assert "<br>" not in body_saved
    assert "Gentile {nome},\ntesto con a capo.\n\nNuovo paragrafo." == body_saved


def test_all_templates_all_provinces_copy_and_newlines():
    """Verifica che tutti i template di tutte le province si convertano in plain text con spazi e a capo corretti."""
    import json
    from routes.distinte import _resolve_template_server

    base_file = os.path.join(mail_manager._ROOT_DIR, 'data', 'distinte_templates.json')
    with open(base_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    provinces = ['TORINO', 'PAVIA', 'LECCO']
    for prov in provinces:
        st = data.get('settings', {}).get('province', {}).get(prov, {})
        email_tec = st.get('email', f'tecnico_{prov.lower()}@algecopower.it')
        tel_tec = st.get('telefono', '011 025 2702')

        for tid, tmpl in data.get('templates', {}).items():
            fields = {
                'nome': 'Mario Rossi',
                'comune': 'Moncalieri',
                'data': '15/09/2026',
                'pod': 'IT001E12345678',
                'indirizzo': 'Via Roma 1',
                'matricola': '12345',
                'eneltel': '98765',
                'nome_societa': 'Acme Srl',
                'distacco': 'no',
                'lista_date': '- Lunedì 15\n- Martedì 16',
                'nome_operatore': 'Operatore Test',
                'telefono_operatore': '333 111222'
            }
            resolved = _resolve_template_server(
                tmpl['body'],
                fields,
                conditionals=tmpl.get('conditionals'),
                email_tecnico=email_tec,
                telefono_tecnico=tel_tec
            )
            plain = mail_manager.html_to_plain_text(resolved)

            # Nessun residuo di tag HTML
            assert '<br>' not in plain, f"Provincia {prov}, template {tid} contiene ancora <br>"
            assert '<p>' not in plain, f"Provincia {prov}, template {tid} contiene ancora <p>"
            assert '<a' not in plain, f"Provincia {prov}, template {tid} contiene ancora <a"

            # Spazi e a capo preservati
            assert '\n' in plain or len(plain.split()) <= 10, f"Provincia {prov}, template {tid} privo di ritorni a capo"
            assert 'Rossi,come' not in plain, f"Provincia {prov}, template {tid} ha parole incollate!"
            assert 'Moncalieri.La' not in plain, f"Provincia {prov}, template {tid} ha frasi incollate!"


def test_save_template_preserves_attributes_and_updates_correctly(tmp_path, monkeypatch):
    """Verifica che salvando una modifica a un template di sistema, gli attributi base siano preservati."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'templates': {
            'email_semplificata': {
                'label': 'Email Semplificata',
                'type': 'email',
                'icon': 'fas fa-envelope',
                'subject': 'Vecchio Oggetto',
                'body': 'Vecchio Corpo',
                'fields': ['nome', 'pod']
            }
        },
        'snippets': {
            'snip_test': {
                'title': 'Snippet Test',
                'body': 'Testo snippet base'
            }
        }
    }, indent=4)

    # Modifica solo subject e body
    payload = {
        'id': 'sys_email_semplificata',
        'title': 'Email Semplificata Modificata',
        'type': 'template',
        'category': 'distinte',
        'subject': 'Nuovo Oggetto Aggiornato',
        'body': 'Nuovo Corpo Aggiornato'
    }
    res = mail_manager.save_template_or_snippet(payload, prov='torino')
    assert res['success'] is True

    # Ricarica
    items = mail_manager.list_templates_and_snippets(prov='torino')
    tpl = next((i for i in items if i['id'] == 'sys_email_semplificata'), None)
    assert tpl is not None
    assert tpl['title'] == 'Email Semplificata Modificata'
    assert tpl['subject'] == 'Nuovo Oggetto Aggiornato'
    assert tpl['body'] == 'Nuovo Corpo Aggiornato'
    assert tpl['icon'] == 'fas fa-envelope'
    assert tpl['type'] == 'template'


def test_distinte_save_and_load_merges_snippets(tmp_path, monkeypatch):
    """Verifica che salvando i template da distinte, snippets e fields base non vengano persi."""
    from routes import distinte as dist_routes
    monkeypatch.setattr(dist_routes, '_ROOT_DIR', str(tmp_path))
    monkeypatch.setattr(dist_routes, 'PROVINCIA_ATTIVA', 'TORINO')

    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'templates': {
            'email_test': {'label': 'Test', 'body': 'Corpo'}
        },
        'snippets': {
            'orari_apertura': {'title': 'Orari Apertura', 'body': 'Orari'}
        },
        'fields': {
            'nome': {'label': 'Nome'}
        }
    }, indent=4)

    # Salvataggio tipico dal modal distinte (invia solo templates, fields, settings)
    save_payload = {
        'templates': {
            'email_test': {'label': 'Test Modificato', 'body': 'Corpo Nuovo'}
        },
        'fields': {
            'nome': {'label': 'Nome'}
        }
    }
    dist_routes._save_templates_data(save_payload)

    # Ricarica: snippets devono essere preservati!
    loaded = dist_routes._load_templates_data()
    assert 'snippets' in loaded
    assert 'orari_apertura' in loaded['snippets']
    assert loaded['templates']['email_test']['label'] == 'Test Modificato'


def test_delete_template_removes_from_base_and_provincial(tmp_path, monkeypatch):
    """Verifica che l'eliminazione di un template lo rimuova sia dalla base che da tutte le province."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)
    os.makedirs(str(tmp_path / 'data' / 'pavia'), exist_ok=True)

    base_file = str(tmp_path / 'data' / 'distinte_templates.json')
    torino_file = str(tmp_path / 'data' / 'torino' / 'distinte_templates.json')
    pavia_file = str(tmp_path / 'data' / 'pavia' / 'distinte_templates.json')

    initial_data = {
        'templates': {
            'tpl_elimina': {'label': 'Da Eliminare', 'body': 'Test body'},
            'tpl_resta': {'label': 'Da Conservare', 'body': 'Altro body'}
        }
    }
    json_write(base_file, initial_data, indent=4)
    json_write(torino_file, initial_data, indent=4)
    json_write(pavia_file, initial_data, indent=4)

    # Elimina con prefisso sys_
    res = mail_manager.delete_template_or_snippet('sys_tpl_elimina', prov='torino')
    assert res is True

    # Verifica ricarica da list_templates_and_snippets
    items_to = mail_manager.list_templates_and_snippets(prov='torino')
    keys_to = [i.get('key') for i in items_to]
    assert 'tpl_elimina' not in keys_to
    assert 'tpl_resta' in keys_to

    items_pv = mail_manager.list_templates_and_snippets(prov='pavia')
    keys_pv = [i.get('key') for i in items_pv]
    assert 'tpl_elimina' not in keys_pv
    assert 'tpl_resta' in keys_pv

    # Verifica su file
    from moduli.safe_json import json_read
    b_data = json_read(base_file)
    assert 'tpl_elimina' not in b_data.get('templates', {})
    assert 'tpl_resta' in b_data.get('templates', {})

    t_data = json_read(torino_file)
    assert 'tpl_elimina' not in t_data.get('templates', {})


def test_delete_snippet_removes_from_base_and_provincial(tmp_path, monkeypatch):
    """Verifica che l'eliminazione di uno snippet lo rimuova sia da base che da provinciale."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)

    base_file = str(tmp_path / 'data' / 'distinte_templates.json')
    torino_file = str(tmp_path / 'data' / 'torino' / 'distinte_templates.json')

    initial_data = {
        'snippets': {
            'snip_del': {'title': 'Snippet Da Eliminare', 'body': 'Corpo del'},
            'snip_ok': {'title': 'Snippet OK', 'body': 'Corpo ok'}
        }
    }
    json_write(base_file, initial_data, indent=4)
    json_write(torino_file, initial_data, indent=4)

    res = mail_manager.delete_template_or_snippet('snip_snip_del', prov='torino')
    assert res is True

    items = mail_manager.list_templates_and_snippets(prov='torino')
    keys = [i.get('key') for i in items]
    assert 'snip_del' not in keys
    assert 'snip_ok' in keys


def test_distinte_save_templates_deletes_removed_templates(tmp_path, monkeypatch):
    """Verifica che salvando dal modal distinte, i template eliminati vengano rimossi anche dalla base."""
    from routes import distinte as dist_routes
    monkeypatch.setattr(dist_routes, '_ROOT_DIR', str(tmp_path))
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    monkeypatch.setattr(dist_routes, 'PROVINCIA_ATTIVA', 'TORINO')

    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)
    real_base = str(tmp_path / 'data' / 'distinte_templates.json')
    json_write(real_base, {
        'templates': {
            'tpl_a': {'label': 'Template A', 'body': 'Corpo A'},
            'tpl_b': {'label': 'Template B', 'body': 'Corpo B'}
        },
        'snippets': {
            'snip_1': {'title': 'Snip 1', 'body': 'Corpo 1'}
        }
    }, indent=4)

    # Salvataggio in cui tpl_b è stato eliminato
    save_payload = {
        'templates': {
            'tpl_a': {'label': 'Template A Modificato', 'body': 'Corpo A Modificato'}
        }
    }
    dist_routes._save_templates_data(save_payload)

    loaded = dist_routes._load_templates_data()
    assert 'tpl_b' not in loaded.get('templates', {})
    assert 'tpl_a' in loaded.get('templates', {})
    assert 'snip_1' in loaded.get('snippets', {})


def test_convert_template_to_snippet(tmp_path, monkeypatch):
    """Verifica che la modifica della tipologia da template a snippet sposti l'elemento e aggiorni l'id."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)

    base_file = str(tmp_path / 'data' / 'distinte_templates.json')
    torino_file = str(tmp_path / 'data' / 'torino' / 'distinte_templates.json')

    initial_data = {
        'templates': {
            'comunicazione_test': {
                'label': 'Comunicazione Test',
                'subject': 'Oggetto Test',
                'body': 'Corpo Testo',
                'fields': ['nome']
            }
        },
        'snippets': {}
    }
    json_write(base_file, initial_data, indent=4)
    json_write(torino_file, initial_data, indent=4)

    # Converti a snippet
    payload = {
        'id': 'sys_comunicazione_test',
        'title': 'Frase Comunicazione Test',
        'type': 'snippet',
        'category': 'frasario',
        'body': 'Nuovo corpo frase rapida'
    }
    res = mail_manager.save_template_or_snippet(payload, prov='torino')
    assert res['success'] is True
    assert res['id'] == 'snip_comunicazione_test'

    # Ricarica
    items = mail_manager.list_templates_and_snippets(prov='torino')
    item = next((i for i in items if i.get('key') == 'comunicazione_test'), None)
    assert item is not None
    assert item['id'] == 'snip_comunicazione_test'
    assert item['type'] == 'snippet'
    assert item['title'] == 'Frase Comunicazione Test'
    assert item['body'] == 'Nuovo corpo frase rapida'

    # Verifica file su disco
    from moduli.safe_json import json_read
    b_data = json_read(base_file)
    assert 'comunicazione_test' not in b_data.get('templates', {})
    assert 'comunicazione_test' in b_data.get('snippets', {})

    t_data = json_read(torino_file)
    assert 'comunicazione_test' not in t_data.get('templates', {})
    assert 'comunicazione_test' in t_data.get('snippets', {})


def test_convert_snippet_to_template(tmp_path, monkeypatch):
    """Verifica che la modifica della tipologia da snippet a template completo sposti l'elemento e aggiorni l'id."""
    monkeypatch.setattr(mail_manager, '_ROOT_DIR', str(tmp_path))
    os.makedirs(str(tmp_path / 'data' / 'torino'), exist_ok=True)

    base_file = str(tmp_path / 'data' / 'distinte_templates.json')
    torino_file = str(tmp_path / 'data' / 'torino' / 'distinte_templates.json')

    initial_data = {
        'templates': {},
        'snippets': {
            'frase_test': {
                'title': 'Frase Test',
                'body': 'Testo breve'
            }
        }
    }
    json_write(base_file, initial_data, indent=4)
    json_write(torino_file, initial_data, indent=4)

    # Converti a template
    payload = {
        'id': 'snip_frase_test',
        'title': 'Template Completo Test',
        'type': 'template',
        'category': 'generale',
        'subject': 'Oggetto {nome}',
        'body': 'Gentile {nome}, corpo template'
    }
    res = mail_manager.save_template_or_snippet(payload, prov='torino')
    assert res['success'] is True
    assert res['id'] == 'sys_frase_test'

    # Ricarica
    items = mail_manager.list_templates_and_snippets(prov='torino')
    item = next((i for i in items if i.get('key') == 'frase_test'), None)
    assert item is not None
    assert item['id'] == 'sys_frase_test'
    assert item['type'] == 'template'
    assert item['subject'] == 'Oggetto {nome}'

    from moduli.safe_json import json_read
    b_data = json_read(base_file)
    assert 'frase_test' in b_data.get('templates', {})
    assert 'frase_test' not in b_data.get('snippets', {})





