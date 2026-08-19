let allComuni = [];
let filteredComuni = [];
let researchQueue = [];
let currentResearchIndex = 0;

function showToast(msg, isError = false) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.background = isError ? 'var(--danger)' : 'var(--success)';
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
}

function loadComuni() {
    fetch('/api/comuni/list')
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                allComuni = res.data;
                populateLclFilter();
                applyFilters();
                updateStats();
            } else {
                showToast(res.error || 'Errore caricamento', true);
            }
        });
}

function populateLclFilter() {
    const sel = document.getElementById('filterLcl');
    if (!sel) return;
    let lcls = new Set();
    allComuni.forEach(c => {
        if(c.lcl) {
            c.lcl.split(',').forEach(l => lcls.add(l.trim()));
        }
    });
    lcls = Array.from(lcls).filter(x => x).sort();
    let currentVal = sel.value;
    sel.innerHTML = '<option value="">Tutti gli LCL</option>';
    lcls.forEach(l => {
        sel.innerHTML += `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`;
    });
    sel.value = currentVal;
}

function isZtlText(text) {
    if (!text) return false;
    const t = String(text).toUpperCase().trim();
    if (t === 'NO' || t === 'NO.') return false;
    if (/NO\s+ZTL|NON\s+HA\s+ZTL/i.test(t)) return false;
    return true;
}

function updateStats() {
    let totContatori = 0;
    let conEmail = 0;
    let conPec = 0;
    let senzaRecapiti = 0;
    let conZtl = 0;

    allComuni.forEach(c => {
        totContatori += (parseInt(c.num_contatori, 10) || 0);
        let parsedUT = parseRawContacts(c.uff_tecnico + ' ' + (c.protocollo || ''));
        let eComune = c.email_comune || parsedUT.email;
        let tComune = c.telefono_comune || parsedUT.tel;
        let pEmail = c.email_polizia || '';
        let pTel = c.telefono_polizia || '';

        if (eComune || pEmail) conEmail++;
        if (c.pec_comune) conPec++;
        if (!eComune && !tComune && !pEmail && !pTel) senzaRecapiti++;
        if (isZtlText(c.ztl)) conZtl++;
    });

    document.getElementById('statsBox').innerHTML = `
        <div class="kpi"><span>${allComuni.length}</span><small>Comuni totali</small></div>
        <div class="kpi"><span>${totContatori}</span><small>Contatori</small></div>
        <div class="kpi"><span>${conEmail}</span><small>Con email</small></div>
        <div class="kpi"><span class="${senzaRecapiti > 0 ? 'kpi-warning' : ''}">${senzaRecapiti}</span><small>Senza recapiti</small></div>
        <div class="kpi"><span>${conZtl}</span><small>ZTL attive</small></div>
    `;
}

// rimosso toggleIncompleteFilter

function applyFilters() {
    const q = document.getElementById('searchInput').value.toLowerCase();
    const fNoEmail = document.getElementById('f_no_email')?.checked;
    const fNoContatti = document.getElementById('f_no_contatti')?.checked;
    const fZtlSi = document.getElementById('f_ztl_si')?.checked;
    const fZtlNo = document.getElementById('f_ztl_no')?.checked;
    
    const filterLcl = document.getElementById('filterLcl') ? document.getElementById('filterLcl').value : '';

    filteredComuni = allComuni.filter(c => {
        let parsedUT = parseRawContacts(c.uff_tecnico + ' ' + (c.protocollo || ''));
        let eComune = c.email_comune || parsedUT.email;
        let tComune = c.telefono_comune || parsedUT.tel;
        
        let parsedPol = parseRawContacts(c.polizia);
        let ePolizia = c.email_polizia || parsedPol.email;
        let tPolizia = c.telefono_polizia || parsedPol.tel;

        const hasEmail = eComune || ePolizia;
        const hasTel = tComune || tPolizia;
        const isZtl = isZtlText(c.ztl);
        
        if (fNoEmail && hasEmail) return false;
        if (fNoContatti && (hasEmail || hasTel)) return false;
        if (fZtlSi && !isZtl) return false;
        if (fZtlNo && isZtl) return false;

        if (filterLcl && (!c.lcl || !c.lcl.includes(filterLcl))) return false;
        
        if (q) {
            const txt = [c.comune, c.email_comune, c.email_polizia, c.email_uff_tecnico, c.note].join(' ').toLowerCase();
            if (!txt.includes(q)) return false;
        }
        return true;
    });

    renderTable();
    document.getElementById('resultInfo').textContent = `${filteredComuni.length} comuni trovati`;
}

function escapeHtml(s) {
    if (!s) return '';
    return String(s)
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

function parseRawContacts(raw) {
    if (!raw) return { email: '', tel: '', rest: '' };
    let emails = [];
    let rEmail = /([a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+\.[a-zA-Z0-9_-]+)/g;
    let m;
    let clean = raw;
    while ((m = rEmail.exec(raw)) !== null) {
        if(!emails.includes(m[1].toLowerCase())) {
            emails.push(m[1].toLowerCase());
        }
        clean = clean.replace(m[0], '');
    }
    
    let tels = [];
    let rawPhones = clean;
    let rPhone = /(?:\+39\s*)?((?:0\d{1,3}|3\d{2})[\s\-\.]?\d{2,4}[\s\-\.]?\d{2,4}[\s\-\.]?\d{0,4})/g;
    while ((m = rPhone.exec(rawPhones)) !== null) {
        let cl = m[1].replace(/[^\d]/g, '');
        if (cl.length >= 6) {
            tels.push(m[0].trim());
        }
        clean = clean.replace(m[0], '');
    }
    
    let rest = clean.replace(/Rif Excel:|Protocollo:|PEC:|Email:|E-mail:|Mail:|Tel:|Telefono:|Cell:|Cellulare:|Fax:/gi, '')
                    .replace(/[\n\r]+/g, ' ')
                    .replace(/^[-\s]+|[-\s]+$/g, '') // remove leading/trailing dashes and spaces
                    .replace(/\s{2,}/g, ' ')
                    .trim();
    if (rest === '-' || rest === '.') rest = '';
    
    return { email: emails.join(', '), tel: tels.join(', '), rest: rest };
}

function renderTable() {
    const tbody = document.getElementById('comuniBody');
    if (filteredComuni.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">Nessun comune trovato.</td></tr>';
        return;
    }

    let html = '';
    filteredComuni.forEach(c => {
        let escComune = escapeHtml(c.comune);
        
        let parsedUT = parseRawContacts(c.uff_tecnico + ' ' + (c.protocollo || ''));
        let eComune = c.email_comune || parsedUT.email;
        let tComune = c.telefono_comune || parsedUT.tel;
        
        let parsedPol = parseRawContacts(c.polizia);
        let ePolizia = c.email_polizia || parsedPol.email;
        let tPolizia = c.telefono_polizia || parsedPol.tel;

        // Status badges
        let statusBadges = '';
        if (isZtlText(c.ztl)) {
            statusBadges += '<span class="comune-badge badge-ztl">ZTL</span>';
        }
        if (eComune) {
            statusBadges += '<span class="comune-badge badge-email"><i class="fas fa-envelope"></i></span>';
        }
        if (ePolizia) {
            statusBadges += '<span class="comune-badge badge-polizia"><i class="fas fa-shield-halved"></i></span>';
        }
        if (!eComune && !tComune && !ePolizia && !tPolizia) {
            statusBadges += '<span class="comune-badge badge-missing"><i class="fas fa-exclamation-triangle"></i> Recapiti</span>';
        }

        html += `
        <tr data-comune="${escComune}">
            <td><input type="checkbox" class="comune-cb" value="${escComune}" onchange="updateBulkBtn()"></td>
            <td style="vertical-align: top; padding-right: 20px;">
                <div style="display:flex; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:10px;">
                    <div style="flex-grow:1;">
                        <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
                            <div class="comune-title" style="margin:0; font-size:16px;">${escComune}</div>
                            <div style="display:flex; gap:6px;">
                                <button class="btn-soft" style="font-size:11px; padding:2px 6px;" onclick="openResearchFor('${escComune.replace(/'/g, "\\'")}')" title="Cerca su Google"><i class="fab fa-google"></i> Cerca</button>
                                <button class="btn-soft" style="font-size:11px; padding:2px 6px; background:var(--bg-secondary); color:var(--success); border:1px solid var(--border);" onclick="saveRow('${escComune.replace(/'/g, "\\'")}')" title="Salva Modifiche"><i class="fas fa-save"></i> Salva</button>
                            </div>
                        </div>
                        <div style="font-size:11px; color:#aaa; display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                            <span><i class="fas fa-list-ol"></i> LCL: ${escapeHtml(c.lcl) || 'N/D'}</span>
                            <span>|</span>
                            <span><i class="fas fa-bolt"></i> Contatori: <strong>${c.num_contatori || 0}</strong></span>
                            <span>|</span>
                            ${statusBadges}
                        </div>
                    </div>
                </div>
                
                <div style="display:flex; gap:20px; align-items:center; margin-bottom:12px; padding:8px; background:rgba(0,0,0,0.1); border-radius:6px;">
                    <div style="display:flex; gap:8px; align-items:center;">
                        <label style="font-size:12px; color:#ddd; font-weight:600; white-space:nowrap;">ZTL:</label>
                        <label class="switch-small" style="margin:0;">
                            <input type="checkbox" id="ztl_${escComune}" ${isZtlText(c.ztl) ? 'checked' : ''} onchange="saveRow('${escComune.replace(/'/g, "\\'")}')">
                            <span class="slider round"></span>
                        </label>
                        <span style="font-size:12px; font-weight:bold; width:20px;" id="ztl_lbl_${escComune}">${isZtlText(c.ztl) ? 'SÌ' : 'NO'}</span>
                    </div>
                    
                    <div style="display:flex; gap:8px; align-items:center; flex-grow:1;">
                        <label style="font-size:12px; color:#ddd; font-weight:600; white-space:nowrap;">Zona:</label>
                        <input type="text" class="edit-input" id="zone_${escComune}" value="${escapeHtml(c.tipologie_zone || '')}" onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="padding:4px 8px; font-size:12px; margin:0; width:100%;">
                    </div>
                </div>

                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:15px; margin-bottom:5px; align-items: stretch;">
                    <div style="display:flex; flex-direction:column;">
                        <label style="font-size:11px; color:#aaa; font-weight:600; display:block; margin-bottom:4px;">Indicazioni ZTL</label>
                        <textarea class="edit-input note-input" id="iztl_${escComune}" placeholder="Accesso, orari, varchi ZTL..." onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="min-height:70px; width:100%; resize:vertical; font-size:12px; line-height:1.4; flex-grow:1;">${escapeHtml(c.indicazioni_ztl)}</textarea>
                    </div>
                    <div style="display:flex; flex-direction:column;">
                        <label style="font-size:11px; color:#aaa; font-weight:600; display:block; margin-bottom:4px;">Note Operative</label>
                        <textarea class="edit-input" id="nt_${escComune}" placeholder="Note generali..." style="min-height:70px; width:100%; resize:vertical; font-size:12px; line-height:1.4; flex-grow:1;" onblur="saveRow('${escComune.replace(/'/g, "\\'")}')"> ${escapeHtml(c.note)}</textarea>
                    </div>
                </div>
            </td>
            <td style="vertical-align: middle;">
                <label style="font-size:11px; color:#aaa; font-weight:600;">Email / PEC (Uff. Tecnico)</label>
                <textarea class="edit-input contatti-area" id="email_comune_${escComune}" rows="3" placeholder="email@..." onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="margin-bottom:10px; resize:vertical; min-height:60px;">${escapeHtml(eComune)}</textarea>
                
                <label style="font-size:11px; color:#aaa; font-weight:600;">Telefono (Uff. Tecnico)</label>
                <textarea class="edit-input contatti-area" id="tel_comune_${escComune}" rows="2" placeholder="011..." onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="resize:vertical; min-height:40px;">${escapeHtml(tComune)}</textarea>
            </td>
            <td style="vertical-align: middle;">
                <label style="font-size:11px; color:#aaa; font-weight:600;">Email / PEC (Polizia)</label>
                <textarea class="edit-input contatti-area" id="email_polizia_${escComune}" rows="3" placeholder="email@..." onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="margin-bottom:10px; resize:vertical; min-height:60px;">${escapeHtml(ePolizia)}</textarea>
                
                <label style="font-size:11px; color:#aaa; font-weight:600;">Telefono (Polizia)</label>
                <textarea class="edit-input contatti-area" id="tel_polizia_${escComune}" rows="2" placeholder="011..." onblur="saveRow('${escComune.replace(/'/g, "\\'")}')" style="resize:vertical; min-height:40px;">${escapeHtml(tPolizia)}</textarea>
            </td>
        </tr>`;
    });
    tbody.innerHTML = html;
    updateBulkBtn();
}

function extractContatti(text) {
    let email = '';
    let tel = '';
    if (!text) return { email, tel };
    let lines = text.split('\n').map(l => l.trim()).filter(l => l);
    for (let line of lines) {
        if(line.toLowerCase().startsWith('email:')) {
            let val = line.substring(6).trim();
            if(val) email += (email ? ', ' : '') + val;
        } else if(line.toLowerCase().startsWith('tel:')) {
            let val = line.substring(4).trim();
            if(val) tel += (tel ? ', ' : '') + val;
        } else if (line.includes('@')) {
            email += (email ? ', ' : '') + line;
        } else {
            tel += (tel ? ', ' : '') + line;
        }
    }
    return { email, tel };
}

function saveRow(comune) {
    const ztlCb = document.getElementById('ztl_' + comune);
    if(ztlCb) {
        document.getElementById('ztl_lbl_' + comune).textContent = ztlCb.checked ? 'SÌ' : 'NO';
    }

    const data = {
        comune: comune,
        email_comune: document.getElementById('email_comune_' + comune).value.trim(),
        telefono_comune: document.getElementById('tel_comune_' + comune).value.trim(),
        email_polizia: document.getElementById('email_polizia_' + comune).value.trim(),
        telefono_polizia: document.getElementById('tel_polizia_' + comune).value.trim(),
        note: document.getElementById('nt_' + comune).value,
        indicazioni_ztl: document.getElementById('iztl_' + comune) ? document.getElementById('iztl_' + comune).value : '',
        ztl: ztlCb && ztlCb.checked ? 'SI' : 'NO',
        tipologie_zone: document.getElementById('zone_' + comune).value
    };

    fetch('/api/comuni/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken()},
        body: JSON.stringify(data)
    }).then(r => r.json()).then(res => {
        if (res.success) {
            showToast('Salvato ' + comune);
            const idx = allComuni.findIndex(x => x.comune === comune);
            if(idx > -1) Object.assign(allComuni[idx], data);
            updateStats();
        } else {
            showToast(res.error, true);
        }
    });
}

// ============================================================================
// BULK EMAIL
// ============================================================================
function toggleSelectAll(checked) {
    document.querySelectorAll('.comune-cb').forEach(cb => cb.checked = checked);
    updateBulkBtn();
}

function updateBulkBtn() {
    const sel = document.querySelectorAll('.comune-cb:checked').length;
    const btn = document.getElementById('bulkOpenBtn');
    btn.disabled = sel === 0;
    btn.innerHTML = `<i class="fas fa-paper-plane"></i> Invio bulk (${sel})`;
}

let bulkPreviews = [];
let isSending = false;

function openBulkModal() {
    const checked = Array.from(document.querySelectorAll('.comune-cb:checked')).map(cb => cb.value);
    if(checked.length === 0) return;
    
    let html = '';
    checked.forEach(c => {
        html += `<div class="bulk-item"><i class="fas fa-building"></i><span class="bulk-name">${c}</span></div>`;
    });
    document.getElementById('bulkList').innerHTML = html;
    document.getElementById('bulkList').style.display = 'block';
    document.getElementById('bulkPreviewSection').style.display = 'none';
    document.getElementById('bulkPreviewBtn').style.display = 'block';
    document.getElementById('bulkSendBtn').style.display = 'none';
    document.getElementById('bulkProgress').style.display = 'none';
    document.getElementById('bulkLog').innerHTML = '';
    document.getElementById('bulkErrorsSummary').style.display = 'none';
    
    document.getElementById('bulkModal').classList.add('active');
    bulkPreviews = [];
    isSending = false;
    caricaAllegatiDisponibiliComuni();
}

function closeBulkModal() {
    if (isSending) {
        if (!confirm('Invio in corso. Sei sicuro di voler chiudere? Le email già inviate non verranno annullate.')) {
            return;
        }
        isSending = false;
    }
    document.getElementById('bulkModal').classList.remove('active');
}

let comuniPresetFiltro = 'tutti';

function applyComuniPreset(preset) {
    comuniPresetFiltro = preset || 'tutti';
    document.querySelectorAll('#comuniBulkPresets .btn-preset').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`comuniPreset_${preset}`);
    if (btn) btn.classList.add('active');
    
    if (bulkPreviews.length > 0) {
        generateBulkPreview();
    }
}

function generateBulkPreview() {
    const checked = Array.from(document.querySelectorAll('.comune-cb:checked')).map(cb => cb.value);
    const template = document.getElementById('templateSelect').value;
    
    if(checked.length === 0) return;
    
    const btn = document.getElementById('bulkPreviewBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generazione...';
    
    fetch('/api/comuni/bulk-preview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            comuni_ids: checked,
            template: template,
            filtro_tipo: comuniPresetFiltro,
            escludi_pec: (comuniPresetFiltro === 'solo_mail' || comuniPresetFiltro === 'no_pec')
        })
    }).then(r => r.json()).then(res => {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-eye"></i> Ricarica Anteprima';
        
        if (res.success) {
            bulkPreviews = res.previews || [];

            // Show errors summary if any
            const errSummary = document.getElementById('bulkErrorsSummary');
            if (res.errors && res.errors.length > 0) {
                let errHtml = '<div class="errors-title"><i class="fas fa-exclamation-triangle"></i> Comuni esclusi:</div>';
                res.errors.forEach(e => {
                    errHtml += `<div class="error-item"><i class="fas fa-times-circle"></i> ${escapeHtml(e)}</div>`;
                });
                errSummary.innerHTML = errHtml;
                errSummary.style.display = 'block';
            } else {
                errSummary.style.display = 'none';
            }

            if(bulkPreviews.length === 0) {
                showToast('Nessuna anteprima generata. Controlla i contatti dei comuni.', true);
                return;
            }
            renderBulkPreviews();
            document.getElementById('bulkList').style.display = 'none';
            document.getElementById('bulkPreviewSection').style.display = 'block';
            document.getElementById('bulkSendBtn').style.display = 'inline-flex';
        } else {
            showToast(res.error, true);
        }
    }).catch(err => {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Errore di connessione (riprova)';
        showToast('Errore di comunicazione con il server.', true);
        console.error(err);
    });
}

function buildEmailHtml(bodyText) {
    // Semplice converter se necessario, ma i nostri bodyHtml sono già html validi
    return bodyText;
}

let allegatiDisponibiliComuni = [];

async function caricaAllegatiDisponibiliComuni() {
    try {
        const resp = await fetch('/api/distinte/allegati');
        const data = await resp.json();
        if (data.ok) {
            allegatiDisponibiliComuni = data.disponibili || [];
        }
    } catch (e) {
        console.error("Errore caricamento allegati comuni:", e);
    }
}

function rimuoviAllegatoComune(previewIdx, allegatoIdx) {
    bulkPreviews[previewIdx].allegati.splice(allegatoIdx, 1);
    segnaModificata(previewIdx);
    const section = document.getElementById(`previewAllegati${previewIdx}`);
    if (section) {
        section.innerHTML = `<div class="preview-field-label" style="margin-top:10px;"><i class="fas fa-paperclip"></i> Allegati:</div>` +
            BulkEmailPreview.renderAllegatiList(bulkPreviews[previewIdx].allegati, previewIdx, allegatiDisponibiliComuni, 'rimuoviAllegatoComune', 'aggiungiAllegatoComune');
    }
    const badge = document.querySelector(`#previewCard${previewIdx} .preview-allegati-badge`);
    const count = (bulkPreviews[previewIdx].allegati || []).length;
    if (badge) {
        badge.innerHTML = `<i class="fas fa-paperclip"></i> ${count}`;
        badge.style.display = count > 0 ? '' : 'none';
    }
}

function aggiungiAllegatoComune(previewIdx) {
    const select = document.getElementById(`addAllegatoSelect${previewIdx}`);
    const nome = select ? select.value : '';
    if (!nome) return;
    if (!bulkPreviews[previewIdx].allegati.includes(nome)) {
        bulkPreviews[previewIdx].allegati.push(nome);
        segnaModificata(previewIdx);
    }
    const section = document.getElementById(`previewAllegati${previewIdx}`);
    if (section) {
        section.innerHTML = `<div class="preview-field-label" style="margin-top:10px;"><i class="fas fa-paperclip"></i> Allegati:</div>` +
            BulkEmailPreview.renderAllegatiList(bulkPreviews[previewIdx].allegati, previewIdx, allegatiDisponibiliComuni, 'rimuoviAllegatoComune', 'aggiungiAllegatoComune');
    }
    const badge = document.querySelector(`#previewCard${previewIdx} .preview-allegati-badge`);
    const count = (bulkPreviews[previewIdx].allegati || []).length;
    if (badge) {
        badge.innerHTML = `<i class="fas fa-paperclip"></i> ${count}`;
        badge.style.display = count > 0 ? '' : 'none';
    }
}

function renderBulkPreviews() {
    window.bulkPreviews = bulkPreviews;
    const section = document.getElementById('bulkPreviewSection');
    let html = `<div class="preview-header-bar">
        <span><i class="fas fa-envelope"></i> ${bulkPreviews.length} email pronte per l'invio</span>
    </div>`;
    
    bulkPreviews.forEach((p, i) => {
        const allegatiHtml = BulkEmailPreview.renderAllegatiList(
            p.allegati || [],
            i,
            allegatiDisponibiliComuni,
            'rimuoviAllegatoComune',
            'aggiungiAllegatoComune'
        );
        html += `
        <div class="preview-card ${i === 0 ? 'expanded' : ''}" id="previewCard${i}">
            <div class="preview-card-header" onclick="togglePreviewCard(${i})">
                <i class="fas fa-chevron-right preview-toggle"></i>
                <span class="preview-dest">${escapeHtml(p.comune)} — ${escapeHtml(p.email)}</span>
                ${p.allegati.length > 0 ? `<span class="preview-allegati-badge"><i class="fas fa-paperclip"></i> ${p.allegati.length}</span>` : ''}
                <span class="preview-card-badge" id="previewBadge${i}" style="display:none;">modificata</span>
                <span class="preview-status-badge" id="previewStatus${i}"></span>
                <button class="btn-remove-preview" onclick="removeFromPreview(${i}, event)" title="Rimuovi da invio">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="preview-card-body">
                <div class="preview-field-label">Oggetto:</div>
                <input type="text" class="preview-subject-input edit-input" id="previewSubject${i}"
                       value="${escapeHtml(p.subject)}" style="width:100%; margin-bottom:10px;"
                       oninput="segnaModificata(${i})">
                <div style="display:flex;align-items:center;justify-content:space-between; margin-bottom:5px;">
                    <div class="preview-field-label" style="margin-bottom:0;">Anteprima email:</div>
                    <button class="btn-soft" id="previewEditBtn${i}" onclick="toggleEditMode(${i}, event)" style="padding: 4px 8px; font-size: 11px;">
                        <i class="fas fa-pen"></i> Modifica testo
                    </button>
                </div>
                <div class="preview-body-rendered" id="previewRendered${i}" style="padding:10px; border-radius:4px; font-size:13px; max-height:200px; overflow-y:auto; border:1px solid var(--border);">${buildEmailHtml(p.bodyHtml)}</div>
                <textarea class="edit-input" id="previewBody${i}" style="display:none; width:100%; min-height:150px; font-family:monospace; font-size:12px; margin-bottom:10px;"
                          oninput="onPreviewBodyEdit(${i})">${escapeHtml(p.bodyHtml)}</textarea>
                <div class="preview-allegati-section" id="previewAllegati${i}">
                    <div class="preview-field-label" style="margin-top:10px;">
                        <i class="fas fa-paperclip"></i> Allegati:
                    </div>
                    ${allegatiHtml}
                </div>
            </div>
        </div>`;
    });
    section.innerHTML = html;
}

function removeFromPreview(idx, event) {
    event.stopPropagation();
    if (bulkPreviews.length <= 1) {
        showToast('Almeno una email deve rimanere nella lista.', true);
        return;
    }
    const comuneRemoved = bulkPreviews[idx].comune;
    bulkPreviews.splice(idx, 1);
    renderBulkPreviews();
    showToast(`${comuneRemoved} rimosso dalla lista invio`);
}

function togglePreviewCard(idx) {
    const card = document.getElementById(`previewCard${idx}`);
    card.classList.toggle('expanded');
}

function toggleEditMode(idx, event) {
    event.stopPropagation();
    const rendered = document.getElementById(`previewRendered${idx}`);
    const editor = document.getElementById(`previewBody${idx}`);
    const btn = document.getElementById(`previewEditBtn${idx}`);
    const isEditing = editor.style.display === 'block';
    
    if (isEditing) {
        rendered.innerHTML = buildEmailHtml(editor.value);
        rendered.style.display = 'block';
        editor.style.display = 'none';
        btn.innerHTML = '<i class="fas fa-pen"></i> Modifica testo';
        btn.classList.remove('active');
    } else {
        rendered.style.display = 'none';
        editor.style.display = 'block';
        btn.innerHTML = '<i class="fas fa-eye"></i> Vedi Anteprima';
        btn.classList.add('active');
    }
}

function onPreviewBodyEdit(idx) {
    segnaModificata(idx);
}

function segnaModificata(idx) {
    const badge = document.getElementById(`previewBadge${idx}`);
    badge.style.display = '';
    badge.classList.add('edited');
}

// ============================================================================
// INVIO SEQUENZIALE CON PROGRESS BAR
// ============================================================================
function sendBulk() {
    if(bulkPreviews.length === 0) return;

    // Conferma prima dell'invio
    const nEmails = bulkPreviews.length;
    const destinatari = bulkPreviews.map(p => p.comune).join(', ');
    const msg = `Stai per inviare ${nEmails} email ai seguenti comuni:\n\n${destinatari}\n\nConfermi l'invio?`;
    if (!confirm(msg)) return;

    // Raccogli i dati aggiornati
    const emailsData = bulkPreviews.map((p, i) => {
        const subjectEl = document.getElementById(`previewSubject${i}`);
        const bodyEl = document.getElementById(`previewBody${i}`);
        return {
            comune: p.comune,
            email: p.email,
            subject: subjectEl ? subjectEl.value : p.subject,
            body_override: bodyEl ? bodyEl.value : p.bodyHtml,
            allegati: p.allegati
        };
    });

    isSending = true;
    document.getElementById('bulkSendBtn').disabled = true;
    document.getElementById('bulkPreviewBtn').style.display = 'none';
    document.getElementById('bulkProgress').style.display = 'block';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = `Preparazione invio di ${emailsData.length} email...`;
    document.getElementById('bulkLog').innerHTML = '';

    // Invio sequenziale con pausa di 3 secondi
    sendSequentially(emailsData, 0, 0, 0);
}

function sendSequentially(emailsData, index, nSent, nErrors) {
    if (!isSending || index >= emailsData.length) {
        // Completato
        isSending = false;
        const pct = 100;
        document.getElementById('progressFill').style.width = pct + '%';
        document.getElementById('progressText').textContent =
            `Completato: ${nSent} inviate, ${nErrors} errori su ${emailsData.length} totali.`;
        
        if (nErrors === 0) {
            document.getElementById('bulkLog').innerHTML += 
                `<div class="log-ok"><i class="fas fa-check-circle"></i> Tutte le ${nSent} email sono state inviate con successo!</div>`;
        }
        showToast(`Invio completato: ${nSent}/${emailsData.length}`);
        return;
    }

    const emailData = emailsData[index];
    const pct = Math.round((index / emailsData.length) * 100);
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressText').textContent =
        `Invio ${index + 1} di ${emailsData.length}: ${emailData.comune}...`;

    // Aggiorna badge status sulla card
    const statusBadge = document.getElementById(`previewStatus${index}`);
    if (statusBadge) {
        statusBadge.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
        statusBadge.className = 'preview-status-badge sending';
    }

    fetch('/api/comuni/send-single', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken()},
        body: JSON.stringify(emailData)
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            nSent++;
            document.getElementById('bulkLog').innerHTML +=
                `<div class="log-ok"><i class="fas fa-check"></i> ${escapeHtml(emailData.comune)} → ${escapeHtml(emailData.email)}</div>`;
            if (statusBadge) {
                statusBadge.innerHTML = '<i class="fas fa-check"></i>';
                statusBadge.className = 'preview-status-badge sent-ok';
            }
        } else {
            nErrors++;
            const errMsg = res.error || 'Errore sconosciuto';
            document.getElementById('bulkLog').innerHTML +=
                `<div class="log-err"><i class="fas fa-times"></i> ${escapeHtml(emailData.comune)}: ${escapeHtml(errMsg)}</div>`;
            if (statusBadge) {
                statusBadge.innerHTML = '<i class="fas fa-times"></i>';
                statusBadge.className = 'preview-status-badge sent-err';
            }
        }

        // Pausa di 3 secondi prima del prossimo invio (evita rate-limiting SMTP)
        if (index + 1 < emailsData.length) {
            setTimeout(() => sendSequentially(emailsData, index + 1, nSent, nErrors), 3000);
        } else {
            sendSequentially(emailsData, index + 1, nSent, nErrors);
        }
    })
    .catch(err => {
        nErrors++;
        document.getElementById('bulkLog').innerHTML +=
            `<div class="log-err"><i class="fas fa-times"></i> ${escapeHtml(emailData.comune)}: Errore di connessione</div>`;
        if (statusBadge) {
            statusBadge.innerHTML = '<i class="fas fa-times"></i>';
            statusBadge.className = 'preview-status-badge sent-err';
        }
        // Riprova dopo pausa
        if (index + 1 < emailsData.length) {
            setTimeout(() => sendSequentially(emailsData, index + 1, nSent, nErrors), 3000);
        } else {
            sendSequentially(emailsData, index + 1, nSent, nErrors);
        }
    });
}


// ============================================================================
// RESEARCH QUEUE
// ============================================================================
function startContactResearchQueue() {
    researchQueue = allComuni.filter(c => !c.email_comune && !c.email_uff_tecnico && !c.email_polizia);
    if(researchQueue.length === 0) {
        showToast('Nessun comune senza contatti trovato.', true);
        return;
    }
    currentResearchIndex = 0;
    openResearchModal();
}

function openResearchFor(comuneId) {
    const c = allComuni.find(x => x.comune === comuneId);
    if(!c) return;
    researchQueue = [c];
    currentResearchIndex = 0;
    openResearchModal();
}

function openResearchModal() {
    const c = researchQueue[currentResearchIndex];
    if(!c) return;

    document.getElementById('researchModal').classList.add('active');
    document.getElementById('researchComuneName').textContent = c.comune;
    document.getElementById('researchProgress').textContent = `Comune ${currentResearchIndex + 1} di ${researchQueue.length}`;

    document.getElementById('researchEmailComune').value = c.email_comune || '';
    document.getElementById('researchTelComune').value = c.telefono_comune || '';
    document.getElementById('researchEmailPolizia').value = c.email_polizia || '';
    document.getElementById('researchTelPolizia').value = c.telefono_polizia || '';

    const qComune = encodeURIComponent(`comune di ${c.comune}`);
    const qPolizia = encodeURIComponent(`comune di ${c.comune} polizia municipale telefono email`);
    const qTecnico = encodeURIComponent(`comune di ${c.comune} ufficio tecnico telefono email`);

    document.getElementById('researchGroups').innerHTML = `
        <div class="research-group">
            <h3>Generico</h3>
            <div class="research-links">
                <a href="https://www.google.com/search?q=${qComune}" target="_blank" class="research-link"><i class="fab fa-google"></i> Comune Generico <i class="fas fa-external-link-alt"></i></a>
            </div>
        </div>
        <div class="research-group">
            <h3>Ufficio Tecnico</h3>
            <div class="research-links">
                <a href="https://www.google.com/search?q=${qTecnico}" target="_blank" class="research-link"><i class="fab fa-google"></i> Ufficio Tecnico <i class="fas fa-external-link-alt"></i></a>
            </div>
        </div>
        <div class="research-group">
            <h3>Polizia Municipale</h3>
            <div class="research-links">
                <a href="https://www.google.com/search?q=${qPolizia}" target="_blank" class="research-link"><i class="fab fa-google"></i> Polizia Municipale <i class="fas fa-external-link-alt"></i></a>
            </div>
        </div>
    `;

    if(document.getElementById('researchAutoOpen').checked) {
        window.open(`https://www.google.com/search?q=${qPolizia}`, '_blank');
        window.open(`https://www.google.com/search?q=${qTecnico}`, '_blank');
    }
}

function closeContactResearch() {
    document.getElementById('researchModal').classList.remove('active');
}

function navigateResearch(dir) {
    const nextIdx = currentResearchIndex + dir;
    if(nextIdx >= 0 && nextIdx < researchQueue.length) {
        currentResearchIndex = nextIdx;
        openResearchModal();
    } else {
        closeContactResearch();
        showToast('Coda completata');
    }
}

function saveResearchContact(andNext) {
    const c = researchQueue[currentResearchIndex];
    if(!c) return;

    const data = {
        comune: c.comune,
        email_comune: document.getElementById('researchEmailComune').value,
        telefono_comune: document.getElementById('researchTelComune').value,
        email_polizia: document.getElementById('researchEmailPolizia').value,
        telefono_polizia: document.getElementById('researchTelPolizia').value
    };

    fetch('/api/comuni/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken()},
        body: JSON.stringify(data)
    }).then(r => r.json()).then(res => {
        if(res.success) {
            showToast('Salvato');
            const idx = allComuni.findIndex(x => x.comune === c.comune);
            if(idx > -1) Object.assign(allComuni[idx], data);
            
            // Aggiorna anche gli input della tabella (textarea)
            const elCC = document.getElementById('contatti_comune_' + c.comune);
            if(elCC) {
                let txt = '';
                if(data.email_comune || data.telefono_comune) {
                    txt = `Email: ${data.email_comune || ''}\nTel: ${data.telefono_comune || ''}`;
                }
                elCC.value = txt;
            }
            const elCP = document.getElementById('contatti_polizia_' + c.comune);
            if(elCP) {
                let txt = '';
                if(data.email_polizia || data.telefono_polizia) {
                    txt = `Email: ${data.email_polizia || ''}\nTel: ${data.telefono_polizia || ''}`;
                }
                elCP.value = txt;
            }

            updateStats();
            
            if(andNext) {
                navigateResearch(1);
            }
        } else {
            showToast(res.error, true);
        }
    });
}

document.getElementById('searchInput').addEventListener('keyup', (e) => {
    if(e.key === 'Enter') applyFilters();
});

document.addEventListener('DOMContentLoaded', () => {
    loadComuni();
    let btn = document.getElementById("scrollTopBtn");
    if(btn) {
        btn.addEventListener('click', () => {
            window.scrollTo({top: 0, behavior: 'smooth'});
        });
    }

    const fileInput = document.getElementById('importComuniFile');
    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);
            
            showToast('Importazione in corso...', false);

            fetch('/api/comuni/import-excel', {
                method: 'POST',
                body: formData,
                headers: {
                    'X-CSRFToken': window.csrf_token || ''
                }
            })
            .then(r => r.json())
            .then(res => {
                fileInput.value = ''; // Reset
                if (res.success) {
                    showToast(`Importati con successo ${res.count} comuni.`, false);
                    loadComuni(); // reload table
                } else {
                    showToast(res.error || 'Errore importazione', true);
                }
            })
            .catch(err => {
                fileInput.value = '';
                showToast('Errore di rete durante l\'importazione', true);
            });
        });
    }
});

// Scroll to top functionality
window.onscroll = function() {
    let btn = document.getElementById("scrollTopBtn");
    if (!btn) return;
    if (document.body.scrollTop > 20 || document.documentElement.scrollTop > 20) {
        btn.style.display = "block";
    } else {
        btn.style.display = "none";
    }
};
