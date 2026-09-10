// -*- coding: utf-8 -*-
/**
 * message_template_modal.js
 * Componente unificato per la gestione e invio dei Template Messaggi (WhatsApp, SMS, Email).
 * Supporta la modifica in tempo reale delle variabili/campi (es. data, distacco, nome, pod, operatore).
 * Condiviso tra Gestione Lettere e Gestione Distinte.
 */

(function () {
    'use strict';

    function _esc(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function _notify(msg, isError = false) {
        if (typeof window.showToast === 'function') {
            window.showToast(msg, isError);
        } else if (typeof window.toast === 'function') {
            window.toast(msg, isError ? 'error' : 'info');
        } else {
            console.log(`[MessageTemplateModal] ${msg}`);
        }
    }

    function _isCellulare(num) {
        if (!num) return false;
        const clean = String(num).replace(/[^\d]/g, '');
        let n = clean;
        if (n.startsWith('0039')) n = n.slice(4);
        else if (n.startsWith('39') && n.length > 10) n = n.slice(2);
        if (n.startsWith('0') && n.length >= 10 && n.slice(1).startsWith('3')) n = n.slice(1);
        return n.startsWith('3') && n.length >= 9 && n.length <= 11;
    }

    function _formatDataEstesa(dateStr) {
        if (!dateStr) return '';
        const giorni = ['DOMENICA','LUNEDÌ','MARTEDÌ','MERCOLEDÌ','GIOVEDÌ','VENERDÌ','SABATO'];
        const mesi = ['GENNAIO','FEBBRAIO','MARZO','APRILE','MAGGIO','GIUGNO',
                      'LUGLIO','AGOSTO','SETTEMBRE','OTTOBRE','NOVEMBRE','DICEMBRE'];
        const match = String(dateStr).match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
        if (match) {
            const d = new Date(parseInt(match[3], 10), parseInt(match[2], 10) - 1, parseInt(match[1], 10));
            if (!isNaN(d)) return `${giorni[d.getDay()]} ${d.getDate()} ${mesi[d.getMonth()]}`;
        }
        const match2 = String(dateStr).match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (match2) {
            const d = new Date(parseInt(match2[1], 10), parseInt(match2[2], 10) - 1, parseInt(match2[3], 10));
            if (!isNaN(d)) return `${giorni[d.getDay()]} ${d.getDate()} ${mesi[d.getMonth()]}`;
        }
        return dateStr;
    }

    function _htmlToPlainText(html) {
        if (!html) return '';
        let txt = String(html);
        txt = txt.replace(/<a[^>]*href="([^"]*?)"[^>]*>\s*(.*?)\s*<\/a>/gi, (m, href, label) => {
            const cleanHref = (href || '').trim();
            const cleanLabel = (label || '').trim();
            if (!cleanHref || cleanHref === cleanLabel || cleanHref.startsWith('mailto:')) return cleanLabel || cleanHref;
            return `${cleanLabel} (${cleanHref})`;
        });
        txt = txt.replace(/<br\s*[\/]?>/gi, '\n');
        txt = txt.replace(/<\/p>/gi, '\n\n');
        txt = txt.replace(/<\/div>/gi, '\n');
        txt = txt.replace(/<[^>]+>/gi, '');
        txt = txt.replace(/&nbsp;/gi, ' ');
        txt = txt.replace(/&amp;/gi, '&');
        txt = txt.replace(/&lt;/gi, '<');
        txt = txt.replace(/&gt;/gi, '>');
        txt = txt.replace(/&quot;/gi, '"');
        txt = txt.replace(/&#039;/gi, "'");
        return txt.trim();
    }

    function _parseDateToYmd(str) {
        if (!str) return null;
        const s = String(str).trim();
        if (!s) return null;
        if (s.toUpperCase().includes('ANNULLAT')) return 'ANNULLATO';
        const dmy = s.match(/^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$/);
        if (dmy) {
            return `${dmy[3]}-${dmy[2].padStart(2, '0')}-${dmy[1].padStart(2, '0')}`;
        }
        const ymd = s.match(/^(\d{4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,2})$/);
        if (ymd) {
            return `${ymd[1]}-${ymd[2].padStart(2, '0')}-${ymd[3].padStart(2, '0')}`;
        }
        return s;
    }

    function _checkCambioData(dataLettera, dataFilone) {
        if (!dataFilone || !String(dataFilone).trim()) return { cambiato: false };
        const sFilone = String(dataFilone).trim();
        const sLettera = String(dataLettera || '').trim();

        if (sFilone.toUpperCase().includes('ANNULLAT')) {
            return {
                cambiato: true,
                isAnnullato: true,
                dataLettera: sLettera,
                dataAttuale: 'ANNULLATO'
            };
        }

        if (!sLettera) return { cambiato: false };

        const normLettera = _parseDateToYmd(sLettera);
        const normFilone = _parseDateToYmd(sFilone);

        if (normFilone && normLettera && normFilone !== normLettera) {
            return {
                cambiato: true,
                isAnnullato: false,
                dataLettera: sLettera,
                dataAttuale: sFilone
            };
        }
        return { cambiato: false };
    }

    const _DEFAULT_FIELD_DEFS = {
        comune: { label: 'Comune', placeholder: 'Comune' },
        data: { label: 'Data Intervento', placeholder: 'DD/MM/YYYY' },
        distacco: {
            label: 'Distacco fornitura?',
            type: 'select',
            options: [
                { l: 'No - Senza distacco', v: 'no' },
                { l: 'Sì - Con breve distacco', v: 'si' }
            ]
        },
        eneltel: { label: 'Eneltel', placeholder: '000000000' },
        indirizzo: { label: 'Indirizzo', placeholder: 'Via ... N.' },
        lista_date: { label: 'Lista Date Proposte', type: 'textarea', placeholder: 'Data 1\nData 2...' },
        matricola: { label: 'Matricola', placeholder: 'Codice / Matricola' },
        nome: { label: 'Nome Cliente', placeholder: 'Nome cliente' },
        nome_operatore: { label: 'Nome Operatore', placeholder: 'Es. Mario Rossi' },
        nome_referente: { label: 'Referente', placeholder: 'Nome referente' },
        nome_societa: { label: 'Nome Società', placeholder: 'Nome società/condominio' },
        nuova_data: { label: 'Nuova Data Proposta', placeholder: 'DD/MM/YYYY' },
        pod: { label: 'POD', placeholder: 'IT001E...' },
        telefono_operatore: { label: 'Telefono Operatore', placeholder: 'Es. 333 1234567' },
        telefono_tecnico: { label: 'Telefono Tecnico', placeholder: '011 025 2702' },
        email_tecnico: { label: 'Email Tecnico', placeholder: 'info@algecopower.it' }
    };

    let _templates = null;
    let _fieldDefs = null;
    let _settings = null;
    let _activeContact = null;
    let _activeOptions = null;
    let _selectedTmplId = null;

    function _injectStyles() {
        if (document.getElementById('messageTemplateModalStyles')) return;
        const style = document.createElement('style');
        style.id = 'messageTemplateModalStyles';
        style.textContent = `
            .mtm-backdrop {
                position: fixed !important;
                inset: 0 !important;
                background: rgba(10, 15, 29, 0.75) !important;
                display: none;
                align-items: center !important;
                justify-content: center !important;
                z-index: 100050 !important;
                padding: 16px !important;
                box-sizing: border-box !important;
            }
            .mtm-backdrop.active {
                display: flex !important;
            }
            .mtm-modal {
                background: var(--surface-raised, #182234) !important;
                color: var(--text-primary, #e2e8f0) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 12px !important;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.45) !important;
                width: 100% !important;
                max-width: 760px !important;
                max-height: 92vh !important;
                display: flex !important;
                flex-direction: column !important;
                overflow: hidden !important;
                animation: mtmFadeIn 0.18s ease-out !important;
            }
            @keyframes mtmFadeIn {
                from { opacity: 0; transform: scale(0.96); }
                to { opacity: 1; transform: scale(1); }
            }
            .mtm-header {
                padding: 14px 20px !important;
                display: flex !important;
                align-items: center !important;
                justify-content: space-between !important;
                border-bottom: 1px solid var(--border, #334155) !important;
                background: rgba(var(--accent-rgb, 59, 130, 246), 0.05) !important;
            }
            .mtm-header h3 {
                margin: 0 !important;
                font-size: 1.15rem !important;
                font-weight: 700 !important;
                display: flex !important;
                align-items: center !important;
                gap: 9px !important;
                color: var(--text-primary, #f8fafc) !important;
            }
            .mtm-close {
                background: transparent !important;
                border: none !important;
                color: var(--text-muted, #94a3b8) !important;
                font-size: 1.4rem !important;
                cursor: pointer !important;
                padding: 4px 8px !important;
                border-radius: 6px !important;
                line-height: 1 !important;
                transition: color 0.15s, background 0.15s !important;
            }
            .mtm-close:hover {
                color: #ef4444 !important;
                background: rgba(239, 68, 68, 0.12) !important;
            }
            .mtm-body {
                padding: 16px 20px !important;
                overflow-y: auto !important;
                display: flex !important;
                flex-direction: column !important;
                gap: 13px !important;
            }
            .mtm-contact-bar {
                font-size: 0.88rem !important;
                color: var(--text-muted, #94a3b8) !important;
                background: var(--surface, #1e293b) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 8px !important;
                padding: 8px 12px !important;
                display: flex !important;
                flex-wrap: wrap !important;
                gap: 8px 14px !important;
                align-items: center !important;
            }
            .mtm-contact-bar strong {
                color: var(--text-primary, #f8fafc) !important;
            }
            .mtm-contact-bar .mtm-tel-highlight {
                color: var(--accent, #3b82f6) !important;
                font-family: monospace !important;
                font-size: 0.95rem !important;
                font-weight: 700 !important;
            }
            .mtm-tabs-wrap {
                display: flex !important;
                flex-wrap: wrap !important;
                gap: 6px !important;
            }
            .mtm-tab-btn {
                background: var(--surface, #1e293b) !important;
                color: var(--text-muted, #94a3b8) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 7px !important;
                padding: 6px 11px !important;
                font-size: 0.82rem !important;
                font-weight: 600 !important;
                cursor: pointer !important;
                display: inline-flex !important;
                align-items: center !important;
                gap: 6px !important;
                transition: all 0.15s ease !important;
            }
            .mtm-tab-btn:hover {
                background: var(--surface-hover, #283548) !important;
                color: var(--text-primary, #f8fafc) !important;
                border-color: var(--border-hover, #475569) !important;
            }
            .mtm-tab-btn.active {
                background: var(--accent, #3b82f6) !important;
                color: #ffffff !important;
                border-color: var(--accent, #3b82f6) !important;
                box-shadow: 0 2px 8px rgba(var(--accent-rgb, 59, 130, 246), 0.35) !important;
            }
            .mtm-fields-grid {
                display: grid !important;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)) !important;
                gap: 10px !important;
                background: var(--surface, #1e293b) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 8px !important;
                padding: 12px 14px !important;
            }
            .mtm-field-group {
                display: flex !important;
                flex-direction: column !important;
                gap: 4px !important;
            }
            .mtm-field-group.full-width {
                grid-column: 1 / -1 !important;
            }
            .mtm-field-group label {
                font-size: 0.72rem !important;
                text-transform: uppercase !important;
                letter-spacing: 0.04em !important;
                color: var(--text-muted, #94a3b8) !important;
                font-weight: 700 !important;
            }
            .mtm-field-input {
                background: var(--bg-card, #0f172a) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 6px !important;
                color: var(--text-primary, #f8fafc) !important;
                padding: 7px 10px !important;
                font-size: 0.85rem !important;
                font-family: inherit !important;
                box-sizing: border-box !important;
                width: 100% !important;
                transition: border-color 0.15s, box-shadow 0.15s !important;
            }
            .mtm-field-input:focus {
                outline: none !important;
                border-color: var(--accent, #3b82f6) !important;
                box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25) !important;
            }
            .mtm-info-box {
                background: rgba(59, 130, 246, 0.08) !important;
                border: 1px solid rgba(59, 130, 246, 0.25) !important;
                border-radius: 7px !important;
                padding: 8px 12px !important;
                font-size: 0.82rem !important;
                color: var(--text-muted, #cbd5e1) !important;
                display: flex !important;
                align-items: center !important;
                gap: 8px !important;
            }
            .mtm-info-box i {
                color: var(--accent, #3b82f6) !important;
            }
            .mtm-subject-box {
                background: rgba(59, 130, 246, 0.08) !important;
                border: 1px solid rgba(59, 130, 246, 0.3) !important;
                border-radius: 8px !important;
                padding: 8px 12px !important;
                font-size: 0.85rem !important;
                display: flex !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 10px !important;
            }
            .mtm-subject-box strong {
                color: var(--accent, #3b82f6) !important;
            }
            .mtm-preview-box {
                background: var(--bg-card, #0f172a) !important;
                color: var(--text-primary, #f8fafc) !important;
                border: 1px solid var(--border, #334155) !important;
                border-radius: 8px !important;
                padding: 14px !important;
                font-size: 0.88rem !important;
                line-height: 1.55 !important;
                white-space: pre-wrap !important;
                min-height: 140px !important;
                max-height: 250px !important;
                overflow-y: auto !important;
                font-family: inherit !important;
            }
            .mtm-preview-box.email-preview {
                font-family: Arial, Helvetica, sans-serif !important;
                font-size: 0.88rem !important;
                line-height: 1.6 !important;
            }
            .mtm-preview-box.email-preview a {
                color: var(--accent, #3b82f6) !important;
                text-decoration: underline !important;
            }
            .mtm-footer {
                padding: 14px 20px !important;
                border-top: 1px solid var(--border, #334155) !important;
                background: rgba(0, 0, 0, 0.15) !important;
                display: flex !important;
                align-items: center !important;
                justify-content: space-between !important;
                gap: 10px !important;
                flex-wrap: wrap !important;
            }
            .mtm-btn {
                padding: 7px 14px !important;
                font-size: 0.84rem !important;
                font-weight: 600 !important;
                border-radius: 6px !important;
                cursor: pointer !important;
                display: inline-flex !important;
                align-items: center !important;
                gap: 6px !important;
                border: 1px solid transparent !important;
                text-decoration: none !important;
                transition: background 0.15s, transform 0.05s !important;
            }
            .mtm-btn:active {
                transform: scale(0.98);
            }
            .mtm-btn-soft {
                background: var(--surface, #1e293b) !important;
                color: var(--text-primary, #e2e8f0) !important;
                border-color: var(--border, #334155) !important;
            }
            .mtm-btn-soft:hover {
                background: var(--surface-hover, #283548) !important;
            }
            .mtm-btn-sms {
                background: linear-gradient(135deg, #10b981, #059669) !important;
                color: #ffffff !important;
            }
            .mtm-btn-sms:hover {
                background: linear-gradient(135deg, #059669, #047857) !important;
            }
            .mtm-btn-wa {
                background: #25d366 !important;
                color: #ffffff !important;
            }
            .mtm-btn-wa:hover {
                background: #1eb855 !important;
            }
            .mtm-btn-mail {
                background: var(--accent, #3b82f6) !important;
                color: #ffffff !important;
            }
            .mtm-btn-mail:hover {
                background: var(--accent-hover, #2563eb) !important;
            }
        `;
        document.head.appendChild(style);
    }

    function _injectModalHtml() {
        if (document.getElementById('messageTemplateModal')) return;
        const div = document.createElement('div');
        div.className = 'mtm-backdrop';
        div.id = 'messageTemplateModal';
        div.innerHTML = `
            <div class="mtm-modal" onclick="event.stopPropagation()">
                <div class="mtm-header">
                    <h3 id="mtmModalTitle">
                        <span id="mtmModalTitleText">Template Messaggio</span>
                    </h3>
                    <button type="button" class="mtm-close" onclick="MessageTemplateModal.close()">&times;</button>
                </div>
                <div class="mtm-body">
                    <div class="mtm-contact-bar" id="mtmContactInfo"></div>

                    <div>
                        <label style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-muted,#94a3b8);font-weight:700;display:block;margin-bottom:6px;">Seleziona Template:</label>
                        <div class="mtm-tabs-wrap" id="mtmTemplateTabs"></div>
                    </div>

                    <div id="mtmFieldsWrap" style="display:none;">
                        <label style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-muted,#94a3b8);font-weight:700;display:block;margin-bottom:6px;">Variabili Template (Modificabili):</label>
                        <div class="mtm-fields-grid" id="mtmFieldsGrid"></div>
                    </div>

                    <div class="mtm-info-box" id="mtmInfoBox" style="display:none;">
                        <i class="fas fa-info-circle"></i>
                        <span id="mtmInfoText"></span>
                    </div>

                    <div class="mtm-subject-box" id="mtmSubjectBox" style="display:none;">
                        <div>
                            <strong>Oggetto:</strong> <span id="mtmSubjectText"></span>
                        </div>
                        <button type="button" class="mtm-btn mtm-btn-soft" style="padding:3px 8px;font-size:0.75rem;" onclick="MessageTemplateModal.copiaOggetto()">
                            <i class="fas fa-copy"></i> Copia
                        </button>
                    </div>

                    <div>
                        <label style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;color:var(--text-muted,#94a3b8);font-weight:700;display:block;margin-bottom:4px;">Testo Risolto:</label>
                        <div class="mtm-preview-box" id="mtmMessagePreview"></div>
                    </div>
                </div>
                <div class="mtm-footer">
                    <button type="button" class="mtm-btn mtm-btn-soft" onclick="MessageTemplateModal.close()">Chiudi</button>
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                        <button type="button" class="mtm-btn mtm-btn-soft" onclick="MessageTemplateModal.copiaTesto()">
                            <i class="fas fa-copy"></i> Copia Testo
                        </button>
                        <button type="button" class="mtm-btn mtm-btn-sms" id="mtmSendSmsBtn" onclick="MessageTemplateModal.inviaSms()">
                            <i class="fas fa-sms"></i> Invia SMS
                        </button>
                        <a class="mtm-btn mtm-btn-wa" id="mtmOpenWaBtn" target="_blank" style="display:none;" onclick="MessageTemplateModal.handleWaClick()">
                            <i class="fab fa-whatsapp"></i> Apri WhatsApp
                        </a>
                        <a class="mtm-btn mtm-btn-mail" id="mtmOpenMailBtn" target="_blank" style="display:none;" onclick="MessageTemplateModal.handleMailClick()">
                            <i class="fas fa-envelope"></i> Apri Mail
                        </a>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(div);

        div.addEventListener('click', (e) => {
            if (e.target === div) MessageTemplateModal.close();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && div.classList.contains('active')) {
                MessageTemplateModal.close();
            }
        });
    }

    async function _ensureTemplatesLoaded() {
        if (_templates && Object.keys(_templates).length > 0) return _templates;
        if (window.TEMPLATES && Object.keys(window.TEMPLATES).length > 0) {
            _templates = window.TEMPLATES;
            _fieldDefs = window.FIELD_DEFS || {};
            _settings = window.templateSettings || {};
            return _templates;
        }
        try {
            const res = await fetch('/api/distinte/templates');
            const data = await res.json();
            _templates = data.templates || {};
            _fieldDefs = data.fields || {};
            _settings = data.settings || {};
        } catch (e) {
            console.error('[MessageTemplateModal] Errore caricamento templates:', e);
            _templates = {};
            _fieldDefs = {};
            _settings = {};
        }
        return _templates;
    }

    function _computeInitialFieldValue(fieldId, c) {
        if (!c) return '';
        const def = (_fieldDefs && _fieldDefs[fieldId]) || _DEFAULT_FIELD_DEFS[fieldId] || {};

        if (def.autoFillRule) {
            const rule = def.autoFillRule;
            const srcVal = String(c[rule.field] || c.tipologia_contatore || c.tipo_misuratore || '').toLowerCase();
            return (rule.contains && srcVal.includes(rule.contains)) ? rule.then : rule.else;
        }

        if (fieldId === 'distacco') {
            const tipo = String(c.tipologia_contatore || c.tipo_misuratore || '').toLowerCase();
            return tipo.includes('semidirett') ? 'no' : 'si';
        }
        if (fieldId === 'comune') {
            return c.comune || '';
        }
        if (fieldId === 'data' || fieldId === 'nuova_data') {
            return c.data_pian_filone || c.data_intervento || c.data || '';
        }
        if (fieldId === 'nome' || fieldId === 'nome_societa') {
            return c.nome || c.nominativo || '';
        }
        if (fieldId === 'pod') {
            return c.pod || c.pod_filone || '';
        }
        if (fieldId === 'indirizzo') {
            const via = c.indirizzo || c.via || '';
            const civ = c.civico || '';
            return `${via} ${civ}`.trim();
        }
        if (fieldId === 'eneltel') {
            return c.eneltel || c.eneltel_filone || '';
        }
        if (fieldId === 'matricola') {
            return c.matricola || c.matricola_contatore || c.matricola_filone || '';
        }
        if (fieldId === 'nome_operatore') {
            return c.operatore || c.matricola_operatore || '';
        }
        if (fieldId === 'telefono_operatore') {
            return c.telefono_operatore || '';
        }
        if (fieldId === 'lista_date') {
            const d = c.data_pian_filone || c.data_intervento || c.data || '';
            return d ? `- ${d}` : '';
        }
        if (fieldId === 'email_tecnico') {
            const pk = (window.provincia || 'TORINO').toUpperCase();
            const provSettings = (_settings && _settings.province && _settings.province[pk]) || {};
            return provSettings.email || (_settings && _settings.email_tecnico) || 'info@algecopower.it';
        }
        if (fieldId === 'telefono_tecnico' || fieldId === 'telefono_ufficio') {
            const pk = (window.provincia || 'TORINO').toUpperCase();
            const provSettings = (_settings && _settings.province && _settings.province[pk]) || {};
            return provSettings.telefono || (_settings && _settings.telefono_tecnico) || '011 025 2702';
        }

        if (def.autoFillFrom) {
            if (Array.isArray(def.autoFillFrom)) {
                return def.autoFillFrom.map(k => c[k] || '').filter(Boolean).join(' ').trim();
            }
            return c[def.autoFillFrom] || c[fieldId] || '';
        }

        return c[fieldId] || '';
    }

    function _buildTemplateFields(tmpl, c) {
        const wrap = document.getElementById('mtmFieldsWrap');
        const grid = document.getElementById('mtmFieldsGrid');
        if (!wrap || !grid) return;

        const fieldsList = (tmpl && tmpl.fields) || [];
        if (!fieldsList.length) {
            wrap.style.display = 'none';
            grid.innerHTML = '';
            return;
        }

        let html = '';
        for (const fieldId of fieldsList) {
            const def = (_fieldDefs && _fieldDefs[fieldId]) || _DEFAULT_FIELD_DEFS[fieldId] || { label: fieldId };
            const label = def.label || fieldId;
            const val = _computeInitialFieldValue(fieldId, c);
            const isFullWidth = def.type === 'textarea' || fieldId === 'lista_date';

            if (def.type === 'select') {
                const options = def.options || [
                    { l: 'No - Senza distacco', v: 'no' },
                    { l: 'Sì - Con breve distacco', v: 'si' }
                ];
                const optHtml = options.map(o => `<option value="${_esc(o.v)}" ${String(o.v) === String(val) ? 'selected' : ''}>${_esc(o.l)}</option>`).join('');
                html += `
                    <div class="mtm-field-group">
                        <label>${_esc(label)}</label>
                        <select class="mtm-field-input" data-field="${_esc(fieldId)}" onchange="MessageTemplateModal.updatePreview()">
                            ${optHtml}
                        </select>
                    </div>
                `;
            } else if (def.type === 'textarea') {
                html += `
                    <div class="mtm-field-group full-width">
                        <label>${_esc(label)}</label>
                        <textarea class="mtm-field-input" data-field="${_esc(fieldId)}" rows="3" placeholder="${_esc(def.placeholder || '')}" oninput="MessageTemplateModal.updatePreview()">${_esc(val)}</textarea>
                    </div>
                `;
            } else {
                let extraHint = '';
                if (fieldId === 'data') {
                    const cambio = _checkCambioData(c.data_lettera || c.data_intervento, c.data_pian_filone);
                    if (cambio.cambiato) {
                        if (cambio.isAnnullato) {
                            extraHint = `<div style="font-size:0.72rem;color:#ef4444;margin-top:3px;font-weight:600;"><i class="fas fa-ban"></i> Intervento ANNULLATO a DB (data lettera: <del style="color:#94a3b8;">${_esc(cambio.dataLettera)}</del>)</div>`;
                        } else {
                            extraHint = `<div style="font-size:0.72rem;color:#f59e0b;margin-top:3px;font-weight:600;"><i class="fas fa-history"></i> Data originaria lettera: <del style="color:#94a3b8;">${_esc(cambio.dataLettera)}</del></div>`;
                        }
                    }
                }
                html += `
                    <div class="mtm-field-group">
                        <label>${_esc(label)}</label>
                        <input type="text" class="mtm-field-input" data-field="${_esc(fieldId)}" value="${_esc(val)}" placeholder="${_esc(def.placeholder || '')}" oninput="MessageTemplateModal.updatePreview()">
                        ${extraHint}
                    </div>
                `;
            }
        }

        grid.innerHTML = html;
        wrap.style.display = 'block';
    }

    function _getFieldValues() {
        const values = {};
        document.querySelectorAll('#mtmFieldsGrid .mtm-field-input').forEach(el => {
            const field = el.dataset.field;
            if (field) values[field] = el.value;
        });
        return values;
    }

    function _resolveTemplateString(templateStr, fieldValues, conditionals, c, telTecnico, emailTecnico, isSms) {
        if (!templateStr) return '';
        let result = templateStr;

        if (conditionals) {
            for (const [key, cond] of Object.entries(conditionals)) {
                const fVal = fieldValues[cond.field] || (c ? _computeInitialFieldValue(cond.field, c) : '');
                const rep = (cond.values && cond.values[fVal]) || '';
                result = result.replace(new RegExp('\\{' + key + '\\}', 'g'), rep);
            }
        }

        const distaccoVal = fieldValues['distacco'] || (c && String(c.tipologia_contatore || c.tipo_misuratore || '').toLowerCase().includes('semidirett') ? 'no' : 'si');
        const fallbackDistacco = distaccoVal === 'no'
            ? 'Precisiamo inoltre che durante l’intervento NON è previsto il distacco della fornitura elettrica (in allegato la specifica tecnica di riferimento).'
            : 'Facciamo presente che durante l’intervento è previsto un breve distacco della fornitura elettrica.';
        result = result.replace(/{frase_distacco}/g, fallbackDistacco);
        result = result.replace(/{distacco_text}/g, fallbackDistacco);

        const telUff = fieldValues['telefono_tecnico'] || fieldValues['telefono_ufficio'] || telTecnico || '011 025 2702';
        const mailUff = fieldValues['email_tecnico'] || emailTecnico || 'info@algecopower.it';
        result = result.replace(/{telefono_tecnico}/g, telUff);
        result = result.replace(/{telefono_tecnico_clean}/g, String(telUff).replace(/[^\d]/g, ''));
        result = result.replace(/{email_tecnico}/g, mailUff);
        result = result.replace(/{telefono_ufficio}/g, telUff);

        const dateFields = ['data', 'nuova_data'];
        for (const [k, v] of Object.entries(fieldValues)) {
            let val = v || '';
            if (dateFields.includes(k) && !isSms) {
                val = _formatDataEstesa(val);
            }
            result = result.replace(new RegExp('\\{' + k + '\\}', 'g'), val);
        }

        if (c) {
            const nome = c.nome || c.nominativo || 'Gentile Cliente';
            const rawData = c.data_pian_filone || c.data_intervento || c.data || '';
            const dataVal = isSms ? rawData : _formatDataEstesa(rawData);
            const comune = c.comune || '';
            const indirizzo = `${c.indirizzo || c.via || ''} ${c.civico || ''}`.trim();
            const pod = c.pod || c.pod_filone || '';
            const eneltel = c.eneltel || c.eneltel_filone || '';
            const matricola = c.matricola || c.matricola_contatore || c.matricola_filone || '';
            const operatore = c.operatore || c.matricola_operatore || '';
            const telOperatore = c.telefono_operatore || '';

            result = result
                .replace(/{nome_societa}/g, nome)
                .replace(/{nome}/g, nome)
                .replace(/{data}/g, dataVal)
                .replace(/{comune}/g, comune)
                .replace(/{indirizzo}/g, indirizzo)
                .replace(/{pod}/g, pod)
                .replace(/{eneltel}/g, eneltel)
                .replace(/{matricola}/g, matricola)
                .replace(/{nome_operatore}/g, operatore)
                .replace(/{telefono_operatore}/g, telOperatore)
                .replace(/{lista_date}/g, rawData ? `- ${rawData}` : '- prossime giornate lavorative');
        }

        return result;
    }

    const MessageTemplateModal = {
        async open(options = {}) {
            _injectStyles();
            _injectModalHtml();

            const c = options.contact || {};
            _activeContact = { ...c };
            _activeOptions = options;

            const targetTel = options.telOverride || c.telefono || c.telefono_filone || '';
            const targetTipo = options.tipoOverride || c.tipo || (_isCellulare(targetTel) ? 'cellulare' : (targetTel ? 'fisso' : 'senza_telefono'));

            _activeContact.telefono_target = targetTel;
            _activeContact.tipo_target = targetTipo;

            const infoEl = document.getElementById('mtmContactInfo');
            if (infoEl) {
                const nome = c.nome || c.nominativo || 'Contatto';
                const pod = c.pod || 'N/D';
                const com = c.comune || '';
                const ind = `${c.indirizzo || c.via || ''} ${c.civico || ''}`.trim();
                const telStr = targetTel ? `<span class="mtm-tel-highlight"><i class="fas fa-phone"></i> ${_esc(targetTel)}</span>` : '<span style="color:#ef4444;"><i class="fas fa-phone-slash"></i> Nessun telefono</span>';

                const cambioData = _checkCambioData(c.data_lettera || c.data_intervento, c.data_pian_filone);
                let cambioBanner = '';
                if (cambioData.cambiato) {
                    if (cambioData.isAnnullato) {
                        cambioBanner = `
                            <div style="grid-column: 1 / -1; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); color: #fca5a5; padding: 5px 9px; border-radius: 6px; font-size: 0.76rem; font-weight: 700; display: flex; align-items: center; gap: 7px;">
                                <i class="fas fa-ban" style="color:#ef4444;"></i>
                                <span>Attenzione ufficio: intervento segnato come ANNULLATO a DB (data originaria lettera: ${_esc(cambioData.dataLettera)})</span>
                            </div>
                        `;
                    } else {
                        cambioBanner = `
                            <div style="grid-column: 1 / -1; background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.35); color: #fbbf24; padding: 5px 9px; border-radius: 6px; font-size: 0.76rem; font-weight: 600; display: flex; align-items: center; gap: 7px;">
                                <i class="fas fa-history" style="color:#f59e0b;"></i>
                                <span>Data intervento modificata a DB: Attuale <b>${_esc(cambioData.dataAttuale)}</b> (Lettera: <del style="color:#94a3b8;">${_esc(cambioData.dataLettera)}</del>)</span>
                            </div>
                        `;
                    }
                }

                infoEl.innerHTML = `
                    <div><strong>${_esc(nome)}</strong></div>
                    <div>${telStr}</div>
                    <div>POD: <b>${_esc(pod)}</b></div>
                    ${com ? `<div><i class="fas fa-city"></i> ${_esc(com)}</div>` : ''}
                    ${ind ? `<div><i class="fas fa-map-marker-alt"></i> ${_esc(ind)}</div>` : ''}
                    ${cambioBanner}
                `;
            }

            await _ensureTemplatesLoaded();

            const tabsWrap = document.getElementById('mtmTemplateTabs');
            if (tabsWrap) {
                tabsWrap.innerHTML = '';
                let firstId = '';
                for (const [id, tmpl] of Object.entries(_templates || {})) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'mtm-tab-btn';
                    btn.id = `mtmTabBtn_${id}`;
                    const icon = tmpl.type === 'sms' ? '<i class="fas fa-sms" style="color:#10b981;"></i> ' :
                                 tmpl.type === 'whatsapp' ? '<i class="fab fa-whatsapp" style="color:#25d366;"></i> ' :
                                 '<i class="fas fa-envelope" style="color:var(--accent,#3b82f6);"></i> ';
                    btn.innerHTML = `${icon}${_esc(tmpl.label || id)}`;
                    btn.onclick = () => MessageTemplateModal.selectTemplate(id);
                    tabsWrap.appendChild(btn);
                    if (!firstId) firstId = id;
                }

                const defaultId = _selectedTmplId && _templates[_selectedTmplId]
                    ? _selectedTmplId
                    : (targetTipo === 'fisso' && _templates['email_semplificata'] ? 'email_semplificata' : (firstId || 'sms_preavviso'));
                MessageTemplateModal.selectTemplate(defaultId);
            }

            const backdrop = document.getElementById('messageTemplateModal');
            if (backdrop) backdrop.classList.add('active');
        },

        selectTemplate(tmplId) {
            _selectedTmplId = tmplId;
            document.querySelectorAll('#mtmTemplateTabs .mtm-tab-btn').forEach(b => b.classList.remove('active'));
            const activeBtn = document.getElementById(`mtmTabBtn_${tmplId}`);
            if (activeBtn) activeBtn.classList.add('active');

            const tmpl = (_templates || {})[tmplId];
            if (!tmpl || !_activeContact) return;

            const titleText = document.getElementById('mtmModalTitleText');
            if (titleText) {
                const icon = tmpl.type === 'sms' ? '<i class="fas fa-sms" style="color:#10b981;"></i> ' :
                             tmpl.type === 'whatsapp' ? '<i class="fab fa-whatsapp" style="color:#25d366;"></i> ' :
                             '<i class="fas fa-envelope" style="color:var(--accent,#3b82f6);"></i> ';
                titleText.innerHTML = `${icon}<span>${_esc(tmpl.label || 'Template Messaggio')}</span>`;
            }

            _buildTemplateFields(tmpl, _activeContact);
            MessageTemplateModal.updatePreview();
        },

        updatePreview() {
            const tmpl = (_templates || {})[_selectedTmplId];
            if (!tmpl || !_activeContact) return;

            const fieldValues = _getFieldValues();

            const infoBox = document.getElementById('mtmInfoBox');
            const infoText = document.getElementById('mtmInfoText');
            if (infoBox && infoText) {
                if (tmpl.info) {
                    infoText.textContent = tmpl.info;
                    infoBox.style.display = 'flex';
                } else {
                    infoBox.style.display = 'none';
                }
            }

            const pk = (window.provincia || 'TORINO').toUpperCase();
            const provSettings = (_settings && _settings.province && _settings.province[pk]) || {};
            const telTecnico = provSettings.telefono || (_settings && _settings.telefono_tecnico) || '011 025 2702';
            const emailTecnico = provSettings.email || (_settings && _settings.email_tecnico) || 'info@algecopower.it';

            let body = tmpl.body || '';
            const isSms = tmpl.type === 'sms';
            const isEmail = tmpl.type === 'email';

            const resolvedBody = _resolveTemplateString(body, fieldValues, tmpl.conditionals, _activeContact, telTecnico, emailTecnico, isSms);

            const previewEl = document.getElementById('mtmMessagePreview');
            if (previewEl) {
                if (isEmail) {
                    const htmlToRender = resolvedBody.includes('<p>') || resolvedBody.includes('<br>') ? resolvedBody : resolvedBody.replace(/\n/g, '<br>');
                    previewEl.innerHTML = htmlToRender;
                    previewEl.classList.add('email-preview');
                } else {
                    previewEl.textContent = _htmlToPlainText(resolvedBody);
                    previewEl.classList.remove('email-preview');
                }
            }

            const subjBox = document.getElementById('mtmSubjectBox');
            const subjText = document.getElementById('mtmSubjectText');
            let resolvedSubject = '';
            if (isEmail && tmpl.subject) {
                resolvedSubject = _resolveTemplateString(tmpl.subject, fieldValues, tmpl.conditionals, _activeContact, telTecnico, emailTecnico, false);
                if (subjText) subjText.textContent = resolvedSubject;
                if (subjBox) subjBox.style.display = 'flex';
            } else {
                if (subjBox) subjBox.style.display = 'none';
            }

            const plainTextForSending = isEmail ? _htmlToPlainText(resolvedBody) : (previewEl ? previewEl.textContent : '');

            const telTarget = _activeContact.telefono_target || '';
            const isCell = _isCellulare(telTarget);
            const waBtn = document.getElementById('mtmOpenWaBtn');
            const smsBtn = document.getElementById('mtmSendSmsBtn');
            const mailBtn = document.getElementById('mtmOpenMailBtn');

            if (waBtn) {
                if (isCell) {
                    const cleanTel = String(telTarget).replace(/[^\d]/g, '');
                    const telInt = cleanTel.startsWith('39') ? cleanTel : `39${cleanTel}`;
                    waBtn.href = `https://wa.me/${telInt}?text=${encodeURIComponent(plainTextForSending)}`;
                    waBtn.style.display = 'inline-flex';
                } else {
                    waBtn.style.display = 'none';
                }
            }

            if (smsBtn) {
                smsBtn.style.display = (tmpl.type === 'sms' || tmpl.type === 'whatsapp' || isCell) ? 'inline-flex' : 'none';
            }

            if (mailBtn) {
                if (isEmail) {
                    const subj = resolvedSubject || 'Sostituzione contatore';
                    mailBtn.href = `mailto:?subject=${encodeURIComponent(subj)}`;
                    mailBtn.style.display = 'inline-flex';
                } else {
                    mailBtn.style.display = 'none';
                }
            }
        },

        async copiaTesto() {
            const tmpl = (_templates || {})[_selectedTmplId];
            const isEmail = tmpl && tmpl.type === 'email';
            const previewEl = document.getElementById('mtmMessagePreview');
            const plainText = previewEl ? (previewEl.innerText || previewEl.textContent || '') : '';
            const htmlContent = previewEl ? previewEl.innerHTML : '';

            if (isEmail && navigator.clipboard && window.ClipboardItem) {
                try {
                    const blob = new Blob([htmlContent], { type: 'text/html' });
                    const plainBlob = new Blob([plainText], { type: 'text/plain' });
                    await navigator.clipboard.write([
                        new ClipboardItem({
                            'text/html': blob,
                            'text/plain': plainBlob
                        })
                    ]);
                    _notify('Testo email copiato negli appunti (formattato)!');
                    return;
                } catch (e) {}
            }

            if (navigator.clipboard) {
                navigator.clipboard.writeText(plainText)
                    .then(() => _notify('Testo messaggio copiato negli appunti!'))
                    .catch(() => _notify('Errore nella copia del testo', true));
            }
        },

        copiaOggetto() {
            const subj = document.getElementById('mtmSubjectText')?.textContent || '';
            if (!subj) return;
            navigator.clipboard.writeText(subj)
                .then(() => _notify('Oggetto copiato negli appunti!'))
                .catch(() => _notify('Errore nella copia dell\'oggetto', true));
        },

        async inviaSms() {
            if (!_activeContact) return;
            const tel = _activeContact.telefono_target;
            if (!tel) {
                _notify('Nessun numero di telefono per l\'invio SMS', true);
                return;
            }
            const previewEl = document.getElementById('mtmMessagePreview');
            const testo = previewEl ? (previewEl.innerText || previewEl.textContent || '') : '';
            if (!testo.trim()) {
                _notify('Testo SMS vuoto', true);
                return;
            }

            const btn = document.getElementById('mtmSendSmsBtn');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Invio...';
            }

            const endpoint = (_activeOptions && _activeOptions.smsEndpoint) || '/api/lettere/invia-singolo-sms';

            try {
                const res = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        pod: _activeContact.pod,
                        telefono: tel,
                        testo: testo,
                        nome: _activeContact.nome || _activeContact.nominativo,
                        data_pian_filone: _activeContact.data_pian_filone || _activeContact.data_intervento || _activeContact.data
                    })
                });
                const data = await res.json();
                if (data.ok) {
                    _notify(`SMS inviato con successo a ${tel}`);
                    MessageTemplateModal.close();
                    if (_activeOptions && typeof _activeOptions.onSmsSent === 'function') {
                        _activeOptions.onSmsSent(_activeContact.pod, data);
                    }
                } else {
                    _notify(data.error || data.errore || 'Errore invio SMS', true);
                }
            } catch (e) {
                _notify(e.message || 'Errore di connessione SMS', true);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-sms"></i> Invia SMS';
                }
            }
        },

        handleWaClick() {
            if (_activeContact && _activeOptions && typeof _activeOptions.onWaOpened === 'function') {
                _activeOptions.onWaOpened(_activeContact.pod);
            }
            setTimeout(() => MessageTemplateModal.close(), 300);
        },

        async handleMailClick() {
            const previewEl = document.getElementById('mtmMessagePreview');
            const plainText = previewEl ? (previewEl.innerText || previewEl.textContent || '') : '';
            const htmlContent = previewEl ? previewEl.innerHTML : '';
            if (htmlContent) {
                try {
                    const blob = new Blob([htmlContent], { type: 'text/html' });
                    const plainBlob = new Blob([plainText], { type: 'text/plain' });
                    await navigator.clipboard.write([
                        new ClipboardItem({
                            'text/html': blob,
                            'text/plain': plainBlob
                        })
                    ]);
                    _notify('Corpo mail copiato! Incolla con Ctrl+V nel messaggio.');
                } catch (e) {
                    if (navigator.clipboard) {
                        await navigator.clipboard.writeText(plainText);
                        _notify('Corpo mail copiato! Incolla con Ctrl+V nel messaggio.');
                    }
                }
            }
            if (_activeContact && _activeOptions && typeof _activeOptions.onMailOpened === 'function') {
                _activeOptions.onMailOpened(_activeContact.pod);
            }
            setTimeout(() => MessageTemplateModal.close(), 300);
        },

        async reloadTemplates() {
            _templates = null;
            _fieldDefs = null;
            return await _ensureTemplatesLoaded();
        },

        close() {
            const backdrop = document.getElementById('messageTemplateModal');
            if (backdrop) backdrop.classList.remove('active');
            _activeContact = null;
            _activeOptions = null;
        }
    };

    window.MessageTemplateModal = MessageTemplateModal;
})();
