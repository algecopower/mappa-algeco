# -*- coding: utf-8 -*-
"""
Componenti UI (CSS / HTML modal / JS) per la utility 'Verifica Indirizzi'
nel pannello Gestionale.

Pattern speculare a `gestionale.py`: queste funzioni ritornano stringhe
che vengono iniettate in `mappa_html.py`.
"""


def genera_css_verifica_indirizzi() -> str:
    return '''
        /* ========================================
           VERIFICA INDIRIZZI - Modal e tabella risultati
           ======================================== */
        .vi-modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(3px);
            z-index: 10000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .vi-modal.visible { display: flex; animation: modalSlideUp 0.2s ease; }

        .vi-content {
            background: var(--bg-primary);
            color: var(--text-primary);
            border-radius: 12px;
            width: min(1200px, 100%);
            max-height: 90vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 20px 60px rgba(0,0,0,0.6);
            overflow: hidden;
            border: 1px solid var(--border);
        }

        .vi-header {
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 16px 20px;
            background: linear-gradient(135deg, #2980b9 0%, #1f6391 100%);
            color: #fff;
        }
        .vi-header-title { font-size: 1.1em; font-weight: 700; flex: 1; }
        .vi-header-close {
            background: rgba(255,255,255,0.18);
            border: none;
            color: #fff;
            width: 32px;
            height: 32px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1.2em;
        }
        .vi-header-close:hover { background: rgba(255,255,255,0.32); }

        .vi-toolbar {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            padding: 14px 20px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            align-items: center;
        }
        .vi-toolbar label {
            font-size: 0.8em;
            font-weight: 700;
            color: var(--text-primary);
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }
        .vi-toolbar select, .vi-toolbar input[type="text"] {
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.9em;
            min-width: 200px;
        }
        .vi-toolbar select:focus, .vi-toolbar input:focus {
            outline: 2px solid var(--accent);
            outline-offset: 1px;
        }
        .vi-toolbar input[type="checkbox"] {
            width: 16px;
            height: 16px;
            accent-color: var(--accent);
        }
        .vi-btn-verifica {
            padding: 9px 18px;
            background: linear-gradient(135deg, #27ae60 0%, #1e8449 100%);
            color: #fff;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 700;
            font-size: 0.9em;
        }
        .vi-btn-verifica:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(39,174,96,0.3); }
        .vi-btn-verifica:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

        .vi-summary {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding: 10px 20px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            font-size: 0.85em;
            color: var(--text-primary);
        }
        .vi-summary-chip {
            padding: 5px 11px;
            border-radius: 12px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            color: var(--text-primary);
        }

        .vi-body {
            flex: 1;
            overflow-y: auto;
            padding: 0;
            background: var(--bg-primary);
        }
        .vi-empty {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        .vi-empty-icon { font-size: 3em; margin-bottom: 10px; }

        .vi-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88em;
            color: var(--text-primary);
        }
        .vi-table thead th {
            position: sticky;
            top: 0;
            background: var(--bg-secondary);
            color: var(--text-primary);
            text-align: left;
            padding: 10px 12px;
            font-weight: 700;
            border-bottom: 2px solid var(--border);
            z-index: 5;
            font-size: 0.78em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .vi-table tbody td {
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
            color: var(--text-primary);
        }
        .vi-table tbody tr:hover { background: rgba(52, 152, 219, 0.10); }

        .vi-badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 10px;
            font-size: 0.75em;
            font-weight: 700;
            white-space: nowrap;
        }
        .vi-badge.ok { background: #d4edda; color: #155724; }
        .vi-badge.civico_mancante { background: #fff3cd; color: #856404; }
        .vi-badge.civico_sospetto { background: #ffe0b2; color: #c25800; }
        .vi-badge.civico_inesistente { background: #f8d7da; color: #721c24; }
        .vi-badge.via_non_trovata { background: #f8d7da; color: #721c24; }
        .vi-badge.comune_non_riconosciuto { background: #e2e3e5; color: #383d41; }
        .vi-badge.fuori_regione { background: #d1ecf1; color: #0c5460; }
        .vi-badge.db_assente { background: #e2e3e5; color: #383d41; }

        .vi-suggerimenti {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            margin-top: 4px;
        }
        .vi-suggerimento {
            padding: 2px 8px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 0.78em;
            color: var(--text-primary);
            cursor: default;
        }
        .vi-suggerimento.civico { font-weight: 700; }
        .vi-suggerimento.via { font-style: italic; }

        .vi-eneltel { font-family: monospace; font-size: 0.85em; color: var(--text-secondary); }
        .vi-cliente { font-weight: 700; color: var(--text-primary); }
        .vi-nota { font-size: 0.78em; color: var(--text-secondary); margin-top: 3px; }

        .vi-loader {
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 40px;
            gap: 10px;
            color: var(--text-primary);
        }
        .vi-spinner {
            width: 20px;
            height: 20px;
            border: 3px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: vi-spin 0.8s linear infinite;
        }
        @keyframes vi-spin { to { transform: rotate(360deg); } }

        .vi-warning {
            padding: 16px 20px;
            background: #fff3cd;
            color: #856404;
            border-bottom: 1px solid #f5c6cb;
            font-size: 0.9em;
        }

        /* Override del tema light per i chip della summary che si confondono */
        [data-theme="light"] .vi-summary-chip,
        [data-theme="light"] .vi-suggerimento {
            background: #fff;
        }
    '''


def genera_html_verifica_indirizzi() -> str:
    return '''
        <!-- MODAL VERIFICA INDIRIZZI -->
        <div id="verificaIndirizziModal" class="vi-modal">
            <div class="vi-content">
                <div class="vi-header">
                    <span class="vi-header-title">🔍 Verifica Indirizzi (anagrafe nazionale)</span>
                    <button class="vi-header-close" onclick="chiudiModalVerificaIndirizzi()">✕</button>
                </div>

                <div id="viDbWarning" class="vi-warning" style="display:none;"></div>

                <div class="vi-toolbar">
                    <label>LCL</label>
                    <select id="viFiltroLCL"><option value="">— Tutti —</option></select>
                    <label>Comune</label>
                    <select id="viFiltroComune"><option value="">— Tutti —</option></select>
                    <label style="display:flex;align-items:center;gap:6px;">
                        <input type="checkbox" id="viSoloProblematici" checked>
                        Solo problematici
                    </label>
                    <button class="vi-btn-verifica" id="viBtnVerifica" onclick="eseguiVerificaIndirizzi()">
                        🔍 Verifica
                    </button>
                </div>

                <div id="viSummary" class="vi-summary" style="display:none;"></div>

                <div class="vi-body" id="viBody">
                    <div class="vi-empty">
                        <div class="vi-empty-icon">📋</div>
                        <div>Seleziona LCL o Comune e clicca "Verifica" per controllare gli indirizzi.</div>
                        <div style="margin-top:8px;font-size:0.85em;">Senza filtri verranno controllati TUTTI i contatori (può richiedere alcuni secondi).</div>
                    </div>
                </div>
            </div>
        </div>
    '''


def genera_js_verifica_indirizzi() -> str:
    # Doppia graffa: questo JS viene iniettato in una f-string Python.
    return '''
        // ============================================================
        // VERIFICA INDIRIZZI
        // ============================================================
        let _viInitDone = false;
        let _viComuniCompleti = [];        // tutti i comuni
        let _viLclToComuni = {};          // mapping LCL -> [comuni]

        async function apriModalVerificaIndirizzi() {
            const modal = document.getElementById('verificaIndirizziModal');
            modal.classList.add('visible');
            if (!_viInitDone) {
                await caricaStatoVerificaIndirizzi();
                _viInitDone = true;
            }
        }

        function chiudiModalVerificaIndirizzi() {
            document.getElementById('verificaIndirizziModal').classList.remove('visible');
        }

        function _viPopolaComuni(lista) {
            const selComune = document.getElementById('viFiltroComune');
            const valoreCorrente = selComune.value;
            selComune.innerHTML = '<option value="">— Tutti i comuni —</option>';
            lista.forEach(c => {
                const o = document.createElement('option');
                o.value = c;
                o.textContent = c;
                selComune.appendChild(o);
            });
            // Mantieni la selezione corrente solo se ancora valida
            if (valoreCorrente && lista.includes(valoreCorrente)) {
                selComune.value = valoreCorrente;
            }
        }

        function _viOnLCLChange() {
            const lcl = document.getElementById('viFiltroLCL').value;
            if (!lcl) {
                _viPopolaComuni(_viComuniCompleti);
            } else {
                _viPopolaComuni(_viLclToComuni[lcl] || []);
            }
        }

        async function caricaStatoVerificaIndirizzi() {
            try {
                const resp = await fetch('/api/verifica-indirizzi/stato');
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const data = await resp.json();

                const warningBox = document.getElementById('viDbWarning');
                if (!data.db || !data.db.disponibile) {
                    warningBox.style.display = 'block';
                    warningBox.innerHTML = '⚠️ Database indirizzi non disponibile. Esegui sul server:<br>'
                        + '<code style="background:rgba(0,0,0,0.08);padding:2px 6px;border-radius:4px;">'
                        + 'python tools/build_indir_db.py</code>';
                    document.getElementById('viBtnVerifica').disabled = true;
                } else {
                    warningBox.style.display = 'none';
                    document.getElementById('viBtnVerifica').disabled = false;
                }

                _viComuniCompleti = data.comuni || [];
                _viLclToComuni = data.lcl_to_comuni || {};

                const selLCL = document.getElementById('viFiltroLCL');
                selLCL.innerHTML = '<option value="">— Tutti gli LCL —</option>';
                (data.lcl || []).forEach(lcl => {
                    const o = document.createElement('option');
                    o.value = lcl;
                    o.textContent = lcl;
                    selLCL.appendChild(o);
                });
                selLCL.onchange = _viOnLCLChange;

                _viPopolaComuni(_viComuniCompleti);
            } catch (err) {
                console.error('[VerificaIndirizzi] caricaStato:', err);
                const warningBox = document.getElementById('viDbWarning');
                warningBox.style.display = 'block';
                warningBox.textContent = '❌ Errore caricamento stato: ' + err.message;
            }
        }

        async function eseguiVerificaIndirizzi() {
            const btn = document.getElementById('viBtnVerifica');
            const body = document.getElementById('viBody');
            const summary = document.getElementById('viSummary');
            btn.disabled = true;

            const payload = {
                lcl: document.getElementById('viFiltroLCL').value,
                comune: document.getElementById('viFiltroComune').value,
                solo_problematici: document.getElementById('viSoloProblematici').checked,
            };

            body.innerHTML = '<div class="vi-loader"><div class="vi-spinner"></div>Verifica in corso...</div>';
            summary.style.display = 'none';

            try {
                const resp = await fetch('/api/verifica-indirizzi', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.error || 'HTTP ' + resp.status);
                }
                const data = await resp.json();
                renderRisultatiVerifica(data);
            } catch (err) {
                console.error('[VerificaIndirizzi] esegui:', err);
                body.innerHTML = '<div class="vi-empty"><div class="vi-empty-icon">❌</div>'
                    + 'Errore: ' + escapeHtml(err.message) + '</div>';
            } finally {
                btn.disabled = false;
            }
        }

        function _viBadgeLabel(status) {
            const map = {
                'ok': '✅ OK',
                'civico_mancante': '⚠️ Civico mancante',
                'civico_sospetto': '⚠️ Civico sospetto',
                'civico_inesistente': '❌ Civico non esiste',
                'via_non_trovata': '❌ Via non trovata',
                'comune_non_riconosciuto': '❓ Comune sconosciuto',
                'fuori_regione': 'ℹ️ Fuori Piemonte',
                'db_assente': '⚙️ DB assente',
            };
            return map[status] || status;
        }

        function renderRisultatiVerifica(data) {
            const body = document.getElementById('viBody');
            const summary = document.getElementById('viSummary');

            // Sommario
            const stati = data.stati || {};
            const chips = [
                `<span class="vi-summary-chip"><b>${data.totale_filtrati}</b> contatori filtrati</span>`,
                `<span class="vi-summary-chip"><b>${data.totale_problematici}</b> con problemi</span>`,
            ];
            for (const [k, v] of Object.entries(stati)) {
                if (v > 0) chips.push(`<span class="vi-summary-chip">${_viBadgeLabel(k)}: <b>${v}</b></span>`);
            }
            summary.innerHTML = chips.join('');
            summary.style.display = 'flex';

            const risultati = data.risultati || [];
            if (risultati.length === 0) {
                body.innerHTML = '<div class="vi-empty"><div class="vi-empty-icon">✅</div>'
                    + 'Nessun problema rilevato sui contatori filtrati.</div>';
                return;
            }

            const rows = risultati.map(r => {
                const v = r.verifica || {};
                const sugCiv = (v.suggerimenti_civici || []).map(s =>
                    `<span class="vi-suggerimento civico">${s.civico}${s.esponente || ''}</span>`
                ).join('');
                const sugVie = (v.suggerimenti_vie || []).map(s =>
                    `<span class="vi-suggerimento via" title="similarità ${s.score}">${escapeHtml(s.odonimo)}</span>`
                ).join('');
                let suggHtml = '';
                if (sugCiv) suggHtml += `<div><span style="font-size:0.78em;color:var(--text-secondary);">Civici vicini:</span> <span class="vi-suggerimenti">${sugCiv}</span></div>`;
                if (sugVie) suggHtml += `<div style="margin-top:4px;"><span style="font-size:0.78em;color:var(--text-secondary);">Vie simili:</span> <span class="vi-suggerimenti">${sugVie}</span></div>`;
                if (v.nota) suggHtml += `<div class="vi-nota">${escapeHtml(v.nota)}</div>`;

                return `
                    <tr>
                        <td>
                            <div class="vi-cliente">${escapeHtml(r.cliente || '—')}</div>
                            <div class="vi-eneltel">${escapeHtml(r.eneltel)}</div>
                            ${r.lcl ? `<div style="font-size:0.78em;color:var(--text-secondary);">LCL: ${escapeHtml(r.lcl)}</div>` : ''}
                        </td>
                        <td>${escapeHtml(r.comune || '—')}</td>
                        <td>
                            <div>${escapeHtml(r.indirizzo || '—')} <b>${escapeHtml(r.civico || '')}</b></div>
                            ${v.odonimo_match ? `<div style="font-size:0.78em;color:var(--text-secondary);">📍 nel registro: ${escapeHtml(v.odonimo_match)}</div>` : ''}
                        </td>
                        <td><span class="vi-badge ${v.status}">${_viBadgeLabel(v.status)}</span></td>
                        <td>${suggHtml || '—'}</td>
                    </tr>`;
            }).join('');

            body.innerHTML = `
                <table class="vi-table">
                    <thead>
                        <tr>
                            <th>Cliente</th>
                            <th>Comune</th>
                            <th>Indirizzo (utente)</th>
                            <th>Esito</th>
                            <th>Suggerimenti</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>`;
        }

        // Helper: escape minimale per evitare XSS in dati utente
        if (typeof escapeHtml === 'undefined') {
            window.escapeHtml = function(s) {
                return String(s == null ? '' : s)
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            };
        }
    '''
