// -*- coding: utf-8 -*-
/**
 * BulkEmailPreview - Modulo condiviso per risoluzione template, anteprima e invio email
 * Condiviso tra Gestione Distinte, Gestione Lettere e Gestione Comuni.
 */
(function (global) {
    'use strict';

    const GIORNI_IT = ['LUNEDÌ', 'MARTEDÌ', 'MERCOLEDÌ', 'GIOVEDÌ', 'VENERDÌ', 'SABATO', 'DOMENICA'];
    const MESI_IT = ['GENNAIO', 'FEBBRAIO', 'MARZO', 'APRILE', 'MAGGIO', 'GIUGNO',
                     'LUGLIO', 'AGOSTO', 'SETTEMBRE', 'OTTOBRE', 'NOVEMBRE', 'DICEMBRE'];

    const BulkEmailPreview = {
        /**
         * Formatta una data (DD/MM/YYYY o YYYY-MM-DD) in formato esteso in maiuscolo (es. LUNEDÌ 15 GIUGNO).
         */
        formatDataEstesa: function (dateStr) {
            if (!dateStr) return '';
            let d;
            if (dateStr.includes('/')) {
                const parts = dateStr.split('/').map(Number);
                if (parts.length === 3) {
                    d = new Date(parts[2], parts[1] - 1, parts[0]);
                }
            } else if (dateStr.includes('-')) {
                const parts = dateStr.split('-').map(Number);
                if (parts.length === 3) {
                    d = new Date(parts[0], parts[1] - 1, parts[2]);
                }
            }
            if (!d || isNaN(d.getTime())) return String(dateStr);
            const dayOfWeek = (d.getDay() + 6) % 7; // Lunedì = 0
            return `${GIORNI_IT[dayOfWeek]} ${d.getDate()} ${MESI_IT[d.getMonth()]}`;
        },

        /**
         * Risolve un template sostituendo segnaposto {campo}, condizionali {key} e {email_tecnico}.
         */
        resolveTemplate: function (bodyStr, fields, conditionals, emailTecnico) {
            let result = bodyStr || '';
            if (conditionals) {
                for (const [key, cond] of Object.entries(conditionals)) {
                    const fieldVal = (fields && fields[cond.field]) || '';
                    const replacement = (cond.values && cond.values[fieldVal]) || '';
                    result = result.replace(new RegExp('\\{' + key + '\\}', 'g'), replacement);
                }
            }
            result = result.replace(/\{email_tecnico\}/g, emailTecnico || '');
            const dateFields = ['data', 'nuova_data'];
            for (const [k, v] of Object.entries(fields || {})) {
                const val = dateFields.includes(k) ? this.formatDataEstesa(v) : (v || '');
                result = result.replace(new RegExp('\\{' + k + '\\}', 'g'), val);
            }
            return result;
        },

        /**
         * Converte il testo con ritorni a capo in HTML e aggiunge la firma configurata.
         */
        buildMessageHtml: function (bodyText, firmaHtml, emailTecnico) {
            let html = (bodyText || '').replace(/\n/g, '<br>');
            if (firmaHtml) {
                html += firmaHtml.replace(/\{email_tecnico\}/g, emailTecnico || '');
            }
            return html;
        },

        /**
         * Genera la barra degli strumenti per il Rich Text Editor (RTE).
         */
        buildRteToolbar: function () {
            return `
                <div class="rte-toolbar">
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('bold')" title="Grassetto"><i class="fas fa-bold"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('italic')" title="Corsivo"><i class="fas fa-italic"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('underline')" title="Sottolineato"><i class="fas fa-underline"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('insertUnorderedList')" title="Lista puntata"><i class="fas fa-list-ul"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('insertOrderedList')" title="Lista numerata"><i class="fas fa-list-ol"></i></button>
                    <button type="button" class="rte-btn" onclick="const url=prompt('Inserisci URL (es. https://...):'); if(url) BulkEmailPreview.execRteCmd('createLink', url);" title="Link"><i class="fas fa-link"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('unlink')" title="Rimuovi Link"><i class="fas fa-unlink"></i></button>
                    <button type="button" class="rte-btn" onclick="BulkEmailPreview.execRteCmd('removeFormat')" title="Rimuovi Formattazione"><i class="fas fa-eraser"></i></button>
                </div>
            `;
        },

        /**
         * Esegue un comando di formattazione per il rich text editor contenteditable.
         */
        execRteCmd: function (command, value) {
            document.execCommand(command, false, value || null);
        },

        /**
         * Rendering della lista allegati con eliminazione e aggiunta interattiva.
         */
        renderAllegatiList: function (allegati, previewIdx, tuttiAllegati, onRemoveFn, onAddFn) {
            const removeName = onRemoveFn || 'rimuoviAllegato';
            const addName = onAddFn || 'aggiungiAllegato';
            let html = '<div class="allegati-list">';
            (allegati || []).forEach((nome, j) => {
                html += `
                    <div class="allegato-item">
                        <i class="fas fa-file-pdf" style="color:#ef4444;"></i>
                        <span class="allegato-nome">${this.escapeHtml(nome)}</span>
                        <button type="button" class="allegato-remove" onclick="${removeName}(${previewIdx}, ${j})" title="Rimuovi">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                `;
            });
            const nonPresenti = (tuttiAllegati || []).filter(a => !(allegati || []).includes(a));
            if (nonPresenti.length > 0) {
                html += `
                    <div class="allegato-aggiungi">
                        <select id="addAllegatoSelect${previewIdx}" class="allegato-select">
                            <option value="">+ Aggiungi allegato...</option>
                            ${nonPresenti.map(a => `<option value="${this.escapeHtml(a)}">${this.escapeHtml(a)}</option>`).join('')}
                        </select>
                        <button type="button" class="allegato-add-btn" onclick="${addName}(${previewIdx})">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                `;
            }
            if ((!allegati || allegati.length === 0) && nonPresenti.length === 0) {
                html += '<div class="mini" style="color:var(--text-muted);">Nessun allegato disponibile</div>';
            }
            html += '</div>';
            return html;
        },

        /**
         * Converte formattazione HTML in markdown leggibile per WhatsApp.
         */
        convertHtmlToWhatsApp: function (html) {
            let txt = html || '';
            txt = txt.replace(/<br\s*[\/]?>/gi, '\n');
            txt = txt.replace(/<\/div>\s*<div[^>]*>/gi, '\n');
            txt = txt.replace(/<\/p>\s*<p[^>]*>/gi, '\n\n');
            txt = txt.replace(/<b>(.*?)<\/b>/gi, '*$1*');
            txt = txt.replace(/<strong>(.*?)<\/strong>/gi, '*$1*');
            txt = txt.replace(/<i>(.*?)<\/i>/gi, '_$1_');
            txt = txt.replace(/<em>(.*?)<\/em>/gi, '_$1_');
            txt = txt.replace(/<s>(.*?)<\/s>/gi, '~$1~');
            txt = txt.replace(/<[^>]+>/g, '');
            txt = txt.replace(/&nbsp;/g, ' ');
            return encodeURIComponent(txt.trim());
        },

        /**
         * Escape HTML di base.
         */
        escapeHtml: function (v) {
            return String(v ?? '').replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            }[c]));
        },

        /**
         * Carica i template e genera FIELD_DEFS e TEMPLATES con funzioni di generazione.
         */
        loadCentralizedTemplates: async function (url = '/api/distinte/templates') {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('Errore caricamento template centralizzati');
            const data = await resp.json();
            const templateSettings = data.settings || {};

            const fieldDefs = {};
            for (const [id, def] of Object.entries(data.fields || {})) {
                const fd = { label: def.label, placeholder: def.placeholder };
                if (def.type) fd.type = def.type;
                if (def.options) fd.options = def.options;
                if (def.autoFillRule) {
                    const rule = def.autoFillRule;
                    fd.autoFill = c => {
                        const val = c[rule.field] || '';
                        return val.toLowerCase().includes(rule.contains) ? rule.then : rule.else;
                    };
                } else if (def.autoFillFrom === null) {
                    fd.autoFill = () => '';
                } else if (Array.isArray(def.autoFillFrom)) {
                    const keys = def.autoFillFrom;
                    fd.autoFill = c => keys.map(k => c[k] || '').join(' ').trim();
                } else {
                    const key = def.autoFillFrom;
                    fd.autoFill = c => c[key] || '';
                }
                fieldDefs[id] = fd;
            }

            const templates = {};
            for (const [id, tmpl] of Object.entries(data.templates || {})) {
                templates[id] = {
                    ...tmpl,
                    generate: (c, f, provEmail) => BulkEmailPreview.resolveTemplate(tmpl.body, f, tmpl.conditionals, provEmail),
                    generateSubject: (c, f, provEmail) => tmpl.subject
                        ? BulkEmailPreview.resolveTemplate(tmpl.subject, f, null, provEmail)
                        : `Sostituzione contatore - ${c.nome || ''}`,
                };
            }

            return {
                templates,
                fieldDefs,
                settings: templateSettings,
            };
        }
    };

    global.BulkEmailPreview = BulkEmailPreview;
})(typeof window !== 'undefined' ? window : this);
