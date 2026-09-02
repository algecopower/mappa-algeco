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
         * Pulisce una stringa per usarla come nome file sicuro.
         */
        sanitizeFilename: function (name) {
            if (!name) return 'Azienda';
            return String(name)
                .normalize('NFKD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^\w\s-]/g, '')
                .trim()
                .replace(/[-\s]+/g, '_')
                .slice(0, 50) || 'Azienda';
        },

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
         * Converte frasi dal singolare al plurale per comunicazioni relative a più contatori.
         */
        pluralizeText: function (text) {
            if (!text) return text || '';
            let t = text;
            const reps = [
                [/\bil vostro contatore\b/gi, 'i vostri contatori'],
                [/\bdel vostro contatore\b/gi, 'dei vostri contatori'],
                [/\bal vostro contatore\b/gi, 'ai vostri contatori'],
                [/\bil contatore\b/gi, 'i contatori'],
                [/\bdel contatore\b/gi, 'dei contatori'],
                [/\bal contatore\b/gi, 'ai contatori'],
                [/\bsul contatore\b/gi, 'sui contatori'],
                [/\bdel dispositivo\b/gi, 'dei dispositivi'],
                [/\bil dispositivo\b/gi, 'i dispositivi'],
                [/\bassociato al POD in oggetto\b/gi, 'associati ai POD indicati'],
                [/\bassociato al POD\b/gi, 'associati ai POD'],
                [/\boggetto dell['’]intervento\b/gi, 'oggetto degli interventi'],
                [/\bLa data prevista per l['’]intervento è\b/gi, 'Le date previste per gli interventi sono'],
            ];
            for (const [re, repl] of reps) {
                t = t.replace(re, repl);
            }
            return t;
        },

        /**
         * Raggruppa una lista di elementi (contatori) per azienda / email / comune.
         */
        groupContattiPerAzienda: function (items, getKeyFn) {
            const groups = new Map();
            items.forEach(item => {
                let key;
                if (getKeyFn) {
                    key = getKeyFn(item);
                } else {
                    const nome = (item.nome_societa || item.nome || item.nominativo || '').trim().toUpperCase();
                    const email = (item.email || '').trim().toLowerCase();
                    const com = (item.comune || '').trim().toUpperCase();
                    key = `${nome}__${com}__${email}`;
                }
                if (!groups.has(key)) {
                    groups.set(key, []);
                }
                groups.get(key).push(item);
            });
            return Array.from(groups.values());
        },

        /**
         * Costruisce oggetto, corpo ed allegati per un gruppo di contatori (N=1, N=2, N>2).
         */
        buildPluralContent: function (tmpl, contatoriList, fieldDefs, emailTecnico) {
            const n = (contatoriList && contatoriList.length) || 1;
            const first = contatoriList[0] || {};
            const fields = {};
            if (tmpl.fields && fieldDefs) {
                for (const fId of tmpl.fields) {
                    const fd = fieldDefs[fId];
                    if (fd && fd.autoFill) {
                        fields[fId] = fd.autoFill(first);
                    }
                }
            }

            const nomeAzienda = first.nome_societa || first.nome || first.nominativo || 'Azienda';

            if (n <= 1) {
                const bodyText = tmpl.generate ? tmpl.generate(first, fields, emailTecnico) : this.resolveTemplate(tmpl.body, fields, tmpl.conditionals, emailTecnico);
                const subject = tmpl.generateSubject ? tmpl.generateSubject(first, fields, emailTecnico) : (tmpl.subject ? this.resolveTemplate(tmpl.subject, fields, null, emailTecnico) : `Sostituzione contatore - ${nomeAzienda}`);
                return {
                    subject: subject,
                    bodyText: bodyText,
                    excelFilename: null,
                };
            }

            // N >= 2: Pluralizza il corpo base
            let rawBody = this.resolveTemplate(tmpl.body, fields, tmpl.conditionals, emailTecnico);
            let pluralBody = this.pluralizeText(rawBody);

            if (n === 2) {
                const c1 = contatoriList[0];
                const c2 = contatoriList[1];
                const pod1 = c1.pod || c1.pod_filone || '';
                const pod2 = c2.pod || c2.pod_filone || '';

                const subject = `${nomeAzienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE - POD ${pod1}, ${pod2}`;

                const dettagliContatori = `\n\nRiepilogo contatori oggetto della sostituzione:\n` +
                    `1) POD: ${pod1} — ${c1.indirizzo || c1.via || ''} ${c1.civico || ''} (${c1.comune || ''}) [Tipo: ${c1.tipologia_contatore || c1.tipo_misuratore || 'N/D'}${c1.matricola_filone || c1.matricola || c1.matricola_ante ? ', Matricola: ' + (c1.matricola_filone || c1.matricola || c1.matricola_ante) : ''}]\n` +
                    `2) POD: ${pod2} — ${c2.indirizzo || c2.via || ''} ${c2.civico || ''} (${c2.comune || ''}) [Tipo: ${c2.tipologia_contatore || c2.tipo_misuratore || 'N/D'}${c2.matricola_filone || c2.matricola || c2.matricola_ante ? ', Matricola: ' + (c2.matricola_filone || c2.matricola || c2.matricola_ante) : ''}]`;

                if (pluralBody.includes("POD (presente in fattura):")) {
                    pluralBody = pluralBody.replace(/POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo|$)/s, dettagliContatori.trim() + "\n\n");
                } else {
                    pluralBody += dettagliContatori;
                }

                return {
                    subject: subject,
                    bodyText: pluralBody,
                    excelFilename: null,
                };
            } else {
                // N > 2: Plurale con conteggio e allegato Excel
                const safeNome = this.sanitizeFilename(nomeAzienda);
                const excelFilename = `Elenco_Contatori_${safeNome}.xlsx`;
                const subject = `${nomeAzienda}: SOSTITUZIONE CONTATORI E-DISTRIBUZIONE (${n} contatori)`;

                const notaExcel = `\n\nIn allegato alla presente trasmettiamo il file Excel riepilogativo (${excelFilename}) con l'elenco completo dei ${n} contatori oggetto dell’intervento (comprensivo di POD, tipologia misuratore, indirizzo e civico).`;

                if (pluralBody.includes("POD (presente in fattura):")) {
                    pluralBody = pluralBody.replace(/POD \(presente in fattura\):.*?(?=Precisiamo che|\n\nPrecisiamo|$)/s, notaExcel.trim() + "\n\n");
                } else {
                    pluralBody += notaExcel;
                }

                return {
                    subject: subject,
                    bodyText: pluralBody,
                    excelFilename: excelFilename,
                };
            }
        },

        /**
         * Rileva la provincia attiva esaminando window, badge UI, generazioni, AppState o hostname.
         */
        getDetectedProvince: function () {
            if (typeof window !== 'undefined') {
                if (window.provincia) return String(window.provincia).toUpperCase();
                const badge = document.getElementById('provinciaBadge');
                if (badge && badge.textContent && badge.textContent.trim()) {
                    return badge.textContent.trim().toUpperCase();
                }
                if (window.generazioni && window.generazioni[0] && window.generazioni[0].provincia) {
                    return String(window.generazioni[0].provincia).toUpperCase();
                }
                if (window.AppState && window.AppState.provincia) {
                    return String(window.AppState.provincia).toUpperCase();
                }
                const host = window.location.hostname || '';
                const parts = host.split('.');
                if (parts.length >= 3 && parts[0] !== 'www' && parts[0] !== 'admin') {
                    return parts[0].toUpperCase();
                }
            }
            return 'TORINO';
        },

        /**
         * Ritorna le impostazioni dei template attive con fallback.
         */
        getTemplateSettings: function () {
            if (typeof window !== 'undefined') {
                if (window.templateSettings && window.templateSettings.province) {
                    return window.templateSettings;
                }
                if (BulkEmailPreview.cachedSettings && BulkEmailPreview.cachedSettings.province) {
                    return BulkEmailPreview.cachedSettings;
                }
                if (window.AppState && window.AppState.settings) {
                    return window.AppState.settings;
                }
            }
            return BulkEmailPreview.cachedSettings || {};
        },

        /**
         * Risolve tutti i tag della firma HTML per qualsiasi provincia passata o rilevata dinamicamente.
         */
        resolveSignatureHtml: function (rawSig, emailTecnico, telefonoTecnico, provKey) {
            if (!rawSig) return '';
            const st = this.getTemplateSettings();
            const prov = (provKey || this.getDetectedProvince()).toUpperCase();
            const provSettings = (st.province && st.province[prov]) || {};

            let email = emailTecnico;
            if (!email) {
                email = provSettings.email || st['email_' + prov.toLowerCase()] || st.email_tecnico || `tecnico_${prov.toLowerCase()}@algecopower.it`;
            }

            let tel = telefonoTecnico;
            if (!tel) {
                tel = provSettings.telefono;
                if (!tel && email && st.province) {
                    for (const p of Object.values(st.province)) {
                        if (p && p.email && p.email.toLowerCase() === email.toLowerCase()) {
                            tel = p.telefono;
                            break;
                        }
                    }
                }
                if (!tel) {
                    tel = st.telefono_tecnico || '011 025 2702';
                }
            }

            const telClean = (tel || '').replace(/[^0-9+]/g, '');
            let sig = String(rawSig);
            sig = sig.replace(/\{email_tecnico\}/g, email || '');
            sig = sig.replace(/\{telefono_tecnico\}/g, tel || '');
            sig = sig.replace(/\{telefono_ufficio\}/g, tel || '');
            sig = sig.replace(/\{telefono_tecnico_clean\}/g, telClean);
            return sig;
        },

        /**
         * Risolve un template sostituendo segnaposto {campo}, condizionali {key}, {email_tecnico} e {telefono_tecnico}.
         */
        resolveTemplate: function (bodyStr, fields, conditionals, emailTecnico, telefonoTecnico, provKey) {
            let result = bodyStr || '';
            if (conditionals) {
                for (const [key, cond] of Object.entries(conditionals)) {
                    const fieldVal = (fields && fields[cond.field]) || '';
                    const replacement = (cond.values && cond.values[fieldVal]) || '';
                    result = result.replace(new RegExp('\\{' + key + '\\}', 'g'), replacement);
                }
            }

            const st = this.getTemplateSettings();
            const prov = (provKey || this.getDetectedProvince()).toUpperCase();
            const provSettings = (st.province && st.province[prov]) || {};

            let email = emailTecnico;
            if (!email) {
                email = provSettings.email || st['email_' + prov.toLowerCase()] || st.email_tecnico || `tecnico_${prov.toLowerCase()}@algecopower.it`;
            }

            let tel = telefonoTecnico;
            if (!tel) {
                tel = provSettings.telefono;
                if (!tel && email && st.province) {
                    for (const p of Object.values(st.province)) {
                        if (p && p.email && p.email.toLowerCase() === email.toLowerCase()) {
                            tel = p.telefono;
                            break;
                        }
                    }
                }
                if (!tel) {
                    tel = st.telefono_tecnico || '011 025 2702';
                }
            }

            const telClean = (tel || '').replace(/[^0-9+]/g, '');
            result = result.replace(/\{email_tecnico\}/g, email || '');
            result = result.replace(/\{telefono_tecnico\}/g, tel || '');
            result = result.replace(/\{telefono_ufficio\}/g, tel || '');
            result = result.replace(/\{telefono_tecnico_clean\}/g, telClean);

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
        buildMessageHtml: function (bodyText, firmaHtml, emailTecnico, telefonoTecnico, provKey) {
            let html = (bodyText || '').replace(/\n/g, '<br>');
            if (firmaHtml) {
                html += this.resolveSignatureHtml(firmaHtml, emailTecnico, telefonoTecnico, provKey);
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
         * Scarica o visualizza l'allegato selezionato (PDF statico o Excel dinamico).
         */
        scaricaAllegato: async function (nome, previewIdx) {
            if (!nome) return;
            const isXlsx = String(nome).toLowerCase().endsWith('.xlsx');

            if (isXlsx) {
                let item = null;
                const previews = (typeof window !== 'undefined' && Array.isArray(window.bulkPreviews) && window.bulkPreviews) ||
                                 (typeof bulkPreviews !== 'undefined' && Array.isArray(bulkPreviews) && bulkPreviews) || [];
                if (typeof previewIdx === 'number' && previews[previewIdx]) {
                    item = previews[previewIdx];
                }

                // 1. Caso Lettere / Distinte con elenco contatori
                const contatori = (item && item.contatori && item.contatori.length > 0)
                    ? item.contatori
                    : (item && item.contatto ? [item.contatto] : null);

                if (contatori && contatori.length > 0) {
                    try {
                        const resp = await fetch('/api/allegati/anteprima-excel-contatori', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                contatori: contatori,
                                nome_azienda: item.nome || item.nome_societa || item.nominativo || 'Azienda',
                                filename: nome
                            })
                        });
                        if (resp.ok) {
                            const blob = await resp.blob();
                            const url = window.URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = url;
                            a.download = nome;
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            window.URL.revokeObjectURL(url);
                            return;
                        } else {
                            const errData = await resp.json().catch(() => ({}));
                            console.error('Errore generazione Excel contatori:', errData);
                            if (typeof showToast === 'function') {
                                showToast(errData.error || 'Errore generazione Excel contatori', true);
                            }
                            return;
                        }
                    } catch (e) {
                        console.error('Errore download excel contatori:', e);
                        if (typeof showToast === 'function') {
                            showToast('Errore durante il download dell\'Excel', true);
                        }
                        return;
                    }
                }

                // 2. Caso Comuni con Excel per comune
                if (item && item.comune) {
                    window.open('/api/comuni/download-excel/' + encodeURIComponent(item.comune), '_blank');
                    return;
                }

                // Fallback nome Contatori_<comune>.xlsx
                if (/^Contatori_.+\.xlsx$/i.test(nome)) {
                    const comuneName = nome.replace(/^Contatori_/i, '').replace(/\.xlsx$/i, '');
                    window.open('/api/comuni/download-excel/' + encodeURIComponent(comuneName), '_blank');
                    return;
                }
            }

            // File statico (PDF o altro allegato archiviato)
            window.open('/api/allegati/file/' + encodeURIComponent(nome), '_blank');
        },

        /**
         * Apre il file picker per caricare un file dal PC e aggiungerlo alla card di anteprima.
         */
        triggerUpload: function (previewIdx) {
            let input = document.getElementById('bulkFileUploadInputGlobal');
            if (!input) {
                input = document.createElement('input');
                input.type = 'file';
                input.id = 'bulkFileUploadInputGlobal';
                input.style.display = 'none';
                input.accept = '.pdf,.xlsx,.xls,.docx,.doc,.png,.jpg,.jpeg,.zip';
                document.body.appendChild(input);
            }
            input.onchange = async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                await BulkEmailPreview.uploadFile(file, previewIdx);
                input.value = '';
            };
            input.click();
        },

        /**
         * Esegue l'upload del file al backend e lo aggancia alla card di anteprima.
         */
        uploadFile: async function (file, previewIdx) {
            if (!file) return;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const resp = await fetch('/api/allegati/upload', {
                    method: 'POST',
                    body: formData
                });
                const res = await resp.json();
                if (!res.ok) {
                    if (typeof showToast === 'function') {
                        showToast(res.error || 'Errore caricamento file', true);
                    } else {
                        alert(res.error || 'Errore caricamento file');
                    }
                    return;
                }

                const filename = res.filename;

                // Aggiorna l'elenco globale degli allegati disponibili
                if (typeof allegatiInfo !== 'undefined' && allegatiInfo.allegati_disponibili) {
                    if (!allegatiInfo.allegati_disponibili.includes(filename)) {
                        allegatiInfo.allegati_disponibili.push(filename);
                    }
                }
                if (typeof allegatiDisponibili !== 'undefined' && Array.isArray(allegatiDisponibili)) {
                    if (!allegatiDisponibili.includes(filename)) {
                        allegatiDisponibili.push(filename);
                    }
                }

                // Aggiunge l'allegato alla card di anteprima corrente
                if (typeof window !== 'undefined' && Array.isArray(window.bulkPreviews) && window.bulkPreviews[previewIdx]) {
                    const item = window.bulkPreviews[previewIdx];
                    if (!item.allegati) item.allegati = [];
                    if (!item.allegati.includes(filename)) {
                        item.allegati.push(filename);
                        item.edited = true;
                    }

                    const badge = document.getElementById(`previewBadge${previewIdx}`);
                    if (badge) badge.style.display = '';

                    if (typeof aggiungiAllegato === 'function') {
                        const section = document.getElementById(`previewAllegati${previewIdx}`);
                        const tuttiAllegati = (typeof allegatiInfo !== 'undefined' && allegatiInfo.allegati_disponibili) ||
                                              (typeof allegatiDisponibili !== 'undefined' && allegatiDisponibili) || [];
                        if (section) {
                            section.innerHTML = `<div class="preview-field-label" style="margin-top:10px;"><i class="fas fa-paperclip"></i> Allegati:</div>` +
                                BulkEmailPreview.renderAllegatiList(item.allegati, previewIdx, tuttiAllegati, 'rimuoviAllegato', 'aggiungiAllegato');
                        }
                    } else if (typeof renderPreviewCards === 'function') {
                        renderPreviewCards();
                    } else if (typeof renderBulkPreviews === 'function') {
                        renderBulkPreviews();
                    }

                    if (typeof aggiornaAllegatiBadge === 'function') {
                        aggiornaAllegatiBadge(previewIdx);
                    }

                    if (typeof showToast === 'function') {
                        showToast(`File "${filename}" caricato e inserito!`);
                    }
                }
            } catch (err) {
                console.error("Errore upload allegato:", err);
                if (typeof showToast === 'function') {
                    showToast('Errore di comunicazione durante il caricamento del file.', true);
                }
            }
        },

        /**
         * Applica un allegato a tutte le card di anteprima correnti.
         */
        applicaAllegatoATutti: function (filename) {
            if (!filename || typeof window === 'undefined' || !Array.isArray(window.bulkPreviews)) return;
            if (!confirm(`Vuoi inserire l'allegato "${filename}" in tutte le email in anteprima?`)) return;

            window.bulkPreviews.forEach((item, idx) => {
                if (!item.allegati) item.allegati = [];
                if (!item.allegati.includes(filename)) {
                    item.allegati.push(filename);
                    item.edited = true;
                }
                const badge = document.getElementById(`previewBadge${idx}`);
                if (badge) badge.style.display = '';
                if (typeof aggiornaAllegatiBadge === 'function') {
                    aggiornaAllegatiBadge(idx);
                }
            });

            if (typeof renderPreviewCards === 'function') {
                renderPreviewCards();
            } else if (typeof renderBulkPreviews === 'function') {
                renderBulkPreviews();
            }
            if (typeof showToast === 'function') {
                showToast(`Allegato "${filename}" applicato a tutte le email!`);
            }
        },

        /**
         * Rendering della lista allegati con supporto sia a PDF che Excel dinamici, download e upload manuale.
         */
        renderAllegatiList: function (allegati, previewIdx, tuttiAllegati, onRemoveFn, onAddFn) {
            const removeName = onRemoveFn || 'rimuoviAllegato';
            const addName = onAddFn || 'aggiungiAllegato';
            let html = '<div class="allegati-list">';
            (allegati || []).forEach((nome, j) => {
                const isXlsx = String(nome).toLowerCase().endsWith('.xlsx');
                const isImg = /\.(png|jpe?g|gif|webp)$/i.test(String(nome));
                const iconClass = isXlsx ? 'fas fa-file-excel' : (isImg ? 'fas fa-file-image' : 'fas fa-file-pdf');
                const iconColor = isXlsx ? '#10b981' : (isImg ? '#8b5cf6' : '#ef4444');
                const extraBadge = isXlsx && nome.toLowerCase().includes('elenco_contatori') ? '<span class="badge" style="background:#10b981;font-size:0.65rem;margin-left:6px;">Generato Auto</span>' : '';
                html += `
                    <div class="allegato-item">
                        <i class="${iconClass}" style="color:${iconColor};"></i>
                        <span class="allegato-nome clickable" onclick="BulkEmailPreview.scaricaAllegato('${this.escapeHtml(nome)}', ${previewIdx})" title="Clicca per scaricare o visualizzare">
                            ${this.escapeHtml(nome)}${extraBadge}
                        </span>
                        <button type="button" class="allegato-download" onclick="BulkEmailPreview.scaricaAllegato('${this.escapeHtml(nome)}', ${previewIdx})" title="Scarica / Visualizza allegato">
                            <i class="fas fa-download"></i>
                        </button>
                        <button type="button" class="allegato-apply-all" onclick="BulkEmailPreview.applicaAllegatoATutti('${this.escapeHtml(nome)}')" title="Aggiungi questo allegato a tutte le email">
                            <i class="fas fa-layer-group"></i>
                        </button>
                        <button type="button" class="allegato-remove" onclick="${removeName}(${previewIdx}, ${j})" title="Rimuovi da questa email">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                `;
            });

            const nonPresenti = (tuttiAllegati || []).filter(a => !(allegati || []).includes(a));
            html += '<div class="allegati-actions-row" style="display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap;">';

            if (nonPresenti.length > 0) {
                html += `
                    <div class="allegato-aggiungi" style="margin-top:0;">
                        <select id="addAllegatoSelect${previewIdx}" class="allegato-select">
                            <option value="">+ Aggiungi file da archivio...</option>
                            ${nonPresenti.map(a => `<option value="${this.escapeHtml(a)}">${this.escapeHtml(a)}</option>`).join('')}
                        </select>
                        <button type="button" class="allegato-add-btn" onclick="${addName}(${previewIdx})" title="Aggiungi">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                `;
            }

            html += `
                <button type="button" class="btn-preset allegato-upload-btn" onclick="BulkEmailPreview.triggerUpload(${previewIdx})" title="Carica un nuovo file dal tuo computer" style="display:flex;align-items:center;gap:6px;">
                    <i class="fas fa-cloud-arrow-up"></i> Carica dal PC
                </button>
            `;

            html += '</div>';

            if ((!allegati || allegati.length === 0) && nonPresenti.length === 0) {
                html += '<div class="mini" style="color:var(--text-muted);margin-top:4px;">Nessun allegato presente</div>';
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
            BulkEmailPreview.cachedSettings = templateSettings;
            if (typeof window !== 'undefined') {
                window.templateSettings = templateSettings;
            }

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
                    generate: (c, f, provEmail, provTel, provKey) => BulkEmailPreview.resolveTemplate(tmpl.body, f, tmpl.conditionals, provEmail, provTel, provKey),
                    generateSubject: (c, f, provEmail, provTel, provKey) => tmpl.subject
                        ? BulkEmailPreview.resolveTemplate(tmpl.subject, f, null, provEmail, provTel, provKey)
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
