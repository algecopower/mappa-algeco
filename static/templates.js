/**
 * static/templates.js — Gestione interattiva della pagina Modelli & Template Centralizzati
 * Supporta Editor Visuale WYSIWYG completo, Codice Sorgente HTML, e sincronizzazione bidirezionale.
 */

const AppState = {
    provincia: 'TORINO',
    items: [],
    settings: {},
    availableFields: [],
    selectedId: null,
    currentFilter: 'all',
    editorMode: 'visual', // 'visual' | 'html'
    sigMode: 'visual'     // 'visual' | 'html'
};

function htmlToPlainText(html) {
    if (typeof window.BulkEmailPreview !== 'undefined' && window.BulkEmailPreview.htmlToPlainText) {
        return window.BulkEmailPreview.htmlToPlainText(html);
    }
    if (!html) return '';
    let txt = String(html);
    txt = txt.replace(/<a[^>]*href="([^"]*?)"[^>]*>\s*(.*?)\s*<\/a>/gi, (m, href, label) => {
        const cleanHref = (href || '').trim();
        const cleanLabel = (label || '').trim();
        if (!cleanLabel || cleanLabel === cleanHref) return cleanHref;
        return `${cleanLabel} (${cleanHref})`;
    });
    txt = txt.replace(/<br\s*[\/]?>/gi, '\n');
    txt = txt.replace(/<\/div>\s*<div[^>]*>/gi, '\n');
    txt = txt.replace(/<\/div>/gi, '\n');
    txt = txt.replace(/<\/p>\s*<p[^>]*>/gi, '\n\n');
    txt = txt.replace(/<\/p>/gi, '\n\n');
    txt = txt.replace(/<li[^>]*>/gi, '• ');
    txt = txt.replace(/<\/li>/gi, '\n');
    txt = txt.replace(/<strong[^>]*>(.*?)<\/strong>/gi, '*$1*');
    txt = txt.replace(/<b[^>]*>(.*?)<\/b>/gi, '*$1*');
    txt = txt.replace(/<em[^>]*>(.*?)<\/em>/gi, '_$1_');
    txt = txt.replace(/<i[^>]*>(.*?)<\/i>/gi, '_$1_');
    txt = txt.replace(/<s[^>]*>(.*?)<\/s>/gi, '~$1~');
    txt = txt.replace(/<strike[^>]*>(.*?)<\/strike>/gi, '~$1~');
    txt = txt.replace(/<del[^>]*>(.*?)<\/del>/gi, '~$1~');
    txt = txt.replace(/<[^>]+>/g, '');
    txt = txt.replace(/&nbsp;/g, ' ')
             .replace(/&amp;/g, '&')
             .replace(/&lt;/g, '<')
             .replace(/&gt;/g, '>')
             .replace(/&quot;/g, '"')
             .replace(/&#39;/g, "'");
    txt = txt.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    txt = txt.replace(/\n{3,}/g, '\n\n');
    return txt.trim();
}

document.addEventListener('DOMContentLoaded', () => {
    lucide.createIcons();
    initPage();
});

async function initPage() {
    await loadAllData();
    
    // Seleziona il primo elemento o quello specificato dall'hash
    const hashId = window.location.hash.replace('#', '');
    if (hashId && AppState.items.some(i => i.id === hashId || i.key === hashId)) {
        selectItem(hashId);
    } else if (AppState.items.length > 0) {
        selectItem(AppState.items[0].id);
    }
}

async function loadAllData() {
    try {
        const res = await fetch('/api/templates/all');
        const data = await res.json();
        if (data.success) {
            AppState.provincia = data.provincia || 'TORINO';
            AppState.items = data.items || [];
            AppState.settings = data.settings || {};
            AppState.availableFields = data.available_fields || [];
            
            renderFilterCounts();
            renderPlaceholdersPills();
            renderTemplatesList();
            populateSettingsForm();
        }
    } catch (e) {
        showToast('Errore caricamento dati: ' + e.message, true);
    }
}

// ============================================================================
// FILTRI & LISTA
// ============================================================================

function renderFilterCounts() {
    const total = AppState.items.length;
    const email = AppState.items.filter(i => i.type === 'template' && (i.category === 'distinte' || i.category === 'email' || !i.category)).length;
    const enti = AppState.items.filter(i => i.key === 'email_comune_avvio' || i.key === 'email_polizia_municipale' || i.category === 'comuni').length;
    const wa = AppState.items.filter(i => i.category === 'whatsapp' || i.key?.startsWith('wa_')).length;
    const snippets = AppState.items.filter(i => i.type === 'snippet').length;
    const provs = Object.keys(AppState.settings?.province || {}).length;

    document.getElementById('countAll').textContent = total;
    document.getElementById('countEmail').textContent = email;
    document.getElementById('countEnti').textContent = enti;
    document.getElementById('countWa').textContent = wa;
    document.getElementById('countSnippets').textContent = snippets;
    const countProvEl = document.getElementById('countProv');
    if (countProvEl) countProvEl.textContent = provs;
}

function setFilter(filter) {
    AppState.currentFilter = filter;
    document.querySelectorAll('.filter-chip').forEach(c => {
        c.classList.toggle('active', c.dataset.filter === filter);
    });

    if (filter === 'settings') {
        document.getElementById('editorTemplateWrap').style.display = 'none';
        document.getElementById('editorSettingsWrap').style.display = 'flex';
        populateSettingsForm();
        return;
    }

    document.getElementById('editorTemplateWrap').style.display = 'flex';
    document.getElementById('editorSettingsWrap').style.display = 'none';
    renderTemplatesList();
}

function filterTemplatesList() {
    renderTemplatesList();
}

function renderTemplatesList() {
    const container = document.getElementById('templatesListContainer');
    const query = (document.getElementById('templateSearchInput')?.value || '').toLowerCase().trim();
    
    let filtered = AppState.items;
    const f = AppState.currentFilter;
    
    if (f === 'email') {
        filtered = filtered.filter(i => i.type === 'template' && i.category !== 'whatsapp' && !i.key?.startsWith('wa_') && i.key !== 'email_comune_avvio' && i.key !== 'email_polizia_municipale');
    } else if (f === 'enti') {
        filtered = filtered.filter(i => i.key === 'email_comune_avvio' || i.key === 'email_polizia_municipale' || i.category === 'comuni');
    } else if (f === 'whatsapp') {
        filtered = filtered.filter(i => i.category === 'whatsapp' || i.key?.startsWith('wa_'));
    } else if (f === 'snippet') {
        filtered = filtered.filter(i => i.type === 'snippet');
    }

    if (query) {
        filtered = filtered.filter(i => 
            (i.title || '').toLowerCase().includes(query) ||
            (i.subject || '').toLowerCase().includes(query) ||
            (i.body || '').toLowerCase().includes(query)
        );
    }

    if (filtered.length === 0) {
        container.innerHTML = `<div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">Nessun modello trovato</div>`;
        return;
    }

    container.innerHTML = filtered.map(item => {
        const isActive = String(item.id) === String(AppState.selectedId) || String(item.key) === String(AppState.selectedId);
        const iconName = item.type === 'snippet' ? 'message-square-quote' : (item.category === 'whatsapp' ? 'message-square' : (item.category === 'comuni' || item.key?.includes('comune') ? 'building-2' : 'mail'));
        const typeBadge = item.type === 'snippet' ? 'Frase Rapida' : 'Template';

        return `
            <div class="template-card-item ${isActive ? 'active' : ''}" onclick="selectItem('${item.id || item.key}')">
                <div class="template-card-top">
                    <span class="template-card-title">
                        <i data-lucide="${iconName}" style="width:14px; height:14px;"></i>
                        ${escapeHtml(item.title)}
                    </span>
                    <span class="template-card-badge">${typeBadge}</span>
                </div>
                <div class="template-card-preview">
                    ${escapeHtml(item.subject || item.body.substring(0, 70))}
                </div>
            </div>
        `;
    }).join('');

    lucide.createIcons();
}

function selectItem(id) {
    const item = AppState.items.find(i => String(i.id) === String(id) || String(i.key) === String(id));
    if (!item) return;

    AppState.selectedId = item.id || item.key;
    
    // Switch to editor view if in settings
    if (AppState.currentFilter === 'settings') {
        setFilter('all');
    }

    document.getElementById('currentId').value = item.id || item.key;
    document.getElementById('templateTitle').value = item.title || '';
    document.getElementById('templateType').value = item.type || 'template';
    document.getElementById('templateSubject').value = item.subject || '';

    // Popola sia Visual che HTML
    const isWaOrSnippet = item.category === 'whatsapp' || String(item.key).startsWith('wa_') || item.type === 'snippet';
    const rawBody = item.body || '';
    const formattedHtml = rawBody.includes('<p>') || rawBody.includes('<br>') || rawBody.includes('<table>') ? rawBody : rawBody.replace(/\n/g, '<br>');
    
    document.getElementById('visualEditor').innerHTML = formattedHtml;
    document.getElementById('htmlEditor').value = isWaOrSnippet ? htmlToPlainText(rawBody) : rawBody;

    // Badges & Actions
    document.getElementById('editorBadgeType').textContent = item.type === 'snippet' ? '💬 Frase Rapida / Snippet' : '🪄 Template Completo';
    document.getElementById('editorBadgeSystem').style.display = item.is_system ? 'inline-flex' : 'none';
    document.getElementById('deleteTemplateBtn').style.display = 'inline-flex';

    onTypeChange();
    renderTemplatesList();
}

function onTypeChange() {
    const isTpl = document.getElementById('templateType').value === 'template';
    document.getElementById('subjectGroup').style.display = isTpl ? 'flex' : 'none';
}

// ============================================================================
// SWITCH MODALITÀ: VISUALE (WYSIWYG) <-> CODICE HTML
// ============================================================================

function switchEditorMode(mode) {
    AppState.editorMode = mode;
    
    const visualBtn = document.getElementById('modeVisualBtn');
    const htmlBtn = document.getElementById('modeHtmlBtn');
    const visualWrap = document.getElementById('visualEditorWrap');
    const htmlWrap = document.getElementById('htmlEditorWrap');
    const wysiwygToolbar = document.getElementById('wysiwygToolbar');

    const visualEditor = document.getElementById('visualEditor');
    const htmlEditor = document.getElementById('htmlEditor');

    const currentItem = AppState.items.find(i => String(i.id) === String(AppState.selectedId) || String(i.key) === String(AppState.selectedId));
    const isWaOrSnippet = (currentItem && (currentItem.category === 'whatsapp' || String(currentItem.key).startsWith('wa_') || currentItem.type === 'snippet')) || (document.getElementById('templateType')?.value === 'snippet');

    if (mode === 'visual') {
        // Passa da HTML a Visuale
        visualBtn.classList.add('active');
        htmlBtn.classList.remove('active');
        
        const val = htmlEditor.value;
        visualEditor.innerHTML = val.includes('<p>') || val.includes('<br>') || val.includes('<table>') ? val : val.replace(/\n/g, '<br>');
        visualWrap.style.display = 'flex';
        htmlWrap.style.display = 'none';
        wysiwygToolbar.style.display = 'flex';
    } else {
        // Passa da Visuale a HTML/Testo
        htmlBtn.classList.add('active');
        visualBtn.classList.remove('active');
        
        htmlEditor.value = isWaOrSnippet ? htmlToPlainText(visualEditor.innerHTML) : visualEditor.innerHTML;
        visualWrap.style.display = 'none';
        htmlWrap.style.display = 'flex';
        wysiwygToolbar.style.display = 'none';
    }
}

// ============================================================================
// WYSIWYG FORMATTING ACTIONS
// ============================================================================

function execEditorCmd(cmd, val = null) {
    const editor = document.getElementById('visualEditor');
    editor.focus();
    document.execCommand(cmd, false, val);
}

function insertLinkDialog() {
    const url = prompt("Inserisci l'indirizzo web (URL):", "https://");
    if (url) {
        execEditorCmd('createLink', url);
    }
}

function insertHighlightTag() {
    const selection = window.getSelection();
    if (!selection.rangeCount) return;
    const selectedText = selection.toString();
    const html = `<span style="background-color: #fef08a; color: #854d0e; padding: 2px 5px; border-radius: 3px; font-weight: bold;">${selectedText || 'Testo evidenziato'}</span>`;
    document.execCommand('insertHTML', false, html);
}

// ============================================================================
// INSERIMENTO SEGNAPOSTO RAPIDI
// ============================================================================

function renderPlaceholdersPills() {
    const container = document.getElementById('placeholdersList');
    container.innerHTML = AppState.availableFields.map(f => `
        <button type="button" class="placeholder-pill" onclick="insertPlaceholder('${f.key}')" title="${escapeHtml(f.label)} (Es: ${escapeHtml(f.sample)})">
            + {${f.key}}
        </button>
    `).join('');
}

function insertPlaceholder(key) {
    const token = `{${key}}`;
    const subjEl = document.getElementById('templateSubject');
    
    // Se il focus è nell'oggetto
    if (document.activeElement === subjEl) {
        insertAtCursorInput(subjEl, token);
        return;
    }

    if (AppState.editorMode === 'visual') {
        const visualEditor = document.getElementById('visualEditor');
        visualEditor.focus();
        document.execCommand('insertText', false, token);
    } else {
        const htmlEditor = document.getElementById('htmlEditor');
        insertAtCursorInput(htmlEditor, token);
    }
}

function insertAtCursorInput(input, text) {
    input.focus();
    const start = input.selectionStart || 0;
    const end = input.selectionEnd || 0;
    const val = input.value;
    input.value = val.substring(0, start) + text + val.substring(end);
    input.selectionStart = input.selectionEnd = start + text.length;
}

// ============================================================================
// SALVATAGGIO & AZIONI MODELLO
// ============================================================================

async function saveTemplate() {
    const id = document.getElementById('currentId').value;
    const currentItem = AppState.items.find(i => String(i.id) === String(id) || String(i.key) === String(id));
    const title = document.getElementById('templateTitle').value.trim();
    const type = document.getElementById('templateType').value;
    const subject = document.getElementById('templateSubject').value.trim();
    const isWaOrSnippet = (currentItem && (currentItem.category === 'whatsapp' || String(currentItem.key).startsWith('wa_') || currentItem.type === 'snippet')) || type === 'snippet';
    
    // Recupera il corpo sincronizzato
    let body = '';
    if (AppState.editorMode === 'visual') {
        body = document.getElementById('visualEditor').innerHTML;
    } else {
        body = document.getElementById('htmlEditor').value;
    }

    // Se è un template WhatsApp o uno snippet, converti sempre in plain text con a capo \n
    if (isWaOrSnippet) {
        body = htmlToPlainText(body);
    }

    if (!title || !body.trim()) {
        showToast('Titolo e testo del modello sono obbligatori.', true);
        return;
    }

    const saveBtn = document.getElementById('saveBtn');
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i data-lucide="loader-2" class="spin"></i> Salvataggio...';
    lucide.createIcons();

    try {
        const payload = {
            id: id || undefined,
            title: title,
            type: type,
            category: currentItem ? currentItem.category : (type === 'snippet' ? 'frasario' : 'generale'),
            subject: subject,
            body: body
        };

        const res = await fetch('/api/templates/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        if (data.success) {
            showToast('Modello salvato e sincronizzato in tutto il gestionale!');
            await loadAllData();
            selectItem(data.id || id);
        } else {
            showToast(data.error || 'Errore salvataggio', true);
        }
    } catch (e) {
        showToast('Errore di connessione: ' + e.message, true);
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<i data-lucide="save"></i> Salva Modello Centralizzato';
        lucide.createIcons();
    }
}

function createNewTemplate() {
    AppState.selectedId = null;
    document.getElementById('currentId').value = '';
    document.getElementById('templateTitle').value = 'Nuovo Modello Personalizzato';
    document.getElementById('templateType').value = 'template';
    document.getElementById('templateSubject').value = 'Comunicazione {nome} - POD {pod}';
    
    const initialText = '<p>Gentile <strong>{nome}</strong>,</p><p>vi contattiamo in merito all\'utenza sita in {indirizzo} a {comune}.</p><p>Restiamo a disposizione per qualsiasi chiarimento.</p><p>Cordiali saluti.</p>';
    document.getElementById('visualEditor').innerHTML = initialText;
    document.getElementById('htmlEditor').value = initialText;

    document.getElementById('editorBadgeType').textContent = '🪄 Nuovo Template';
    document.getElementById('editorBadgeSystem').style.display = 'none';
    document.getElementById('deleteTemplateBtn').style.display = 'none';

    onTypeChange();
    renderTemplatesList();
}

function duplicateCurrentTemplate() {
    const title = document.getElementById('templateTitle').value;
    const type = document.getElementById('templateType').value;
    const subject = document.getElementById('templateSubject').value;
    const body = AppState.editorMode === 'visual' ? document.getElementById('visualEditor').innerHTML : document.getElementById('htmlEditor').value;

    AppState.selectedId = null;
    document.getElementById('currentId').value = '';
    document.getElementById('templateTitle').value = `${title} (Copia)`;
    document.getElementById('templateType').value = type;
    document.getElementById('templateSubject').value = subject;
    document.getElementById('visualEditor').innerHTML = body;
    document.getElementById('htmlEditor').value = body;

    document.getElementById('editorBadgeType').textContent = '🪄 Template Duplicato';
    document.getElementById('editorBadgeSystem').style.display = 'none';
    document.getElementById('deleteTemplateBtn').style.display = 'none';

    showToast('Modello duplicato. Clicca su Salva per memorizzarlo.');
    renderTemplatesList();
}

async function deleteCurrentTemplate() {
    const id = document.getElementById('currentId').value;
    if (!id) return;

    if (!confirm('Sei sicuro di voler eliminare questo modello? L\'eliminazione è immediata su tutto il gestionale.')) return;

    try {
        const res = await fetch(`/api/templates/delete/${id}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': getCsrfToken() }
        });
        const data = await res.json();
        if (data.success) {
            showToast('Modello eliminato con successo.');
            await loadAllData();
            if (AppState.items.length > 0) {
                selectItem(AppState.items[0].id || AppState.items[0].key);
            }
        } else {
            showToast(data.error || 'Errore eliminazione', true);
        }
    } catch (e) {
        showToast('Errore: ' + e.message, true);
    }
}

// ============================================================================
// SETTINGS FIRMA & RECAPITI DINAMICI PER PROVINCIA
// ============================================================================

function populateSettingsForm() {
    renderProvincesCards();
    const s = AppState.settings || {};
    document.getElementById('set_firma_html').value = s.firma_html || '';
    renderSignaturePreview();
}

function renderProvincesCards() {
    const container = document.getElementById('provincesListContainer');
    if (!container) return;

    const provMap = AppState.settings?.province || {
        'TORINO': { nome: 'Torino', email: 'tecnico_to2@algecopower.it', telefono: '011 025 2702' },
        'PAVIA': { nome: 'Pavia', email: 'tecnico_pv1@algecopower.it', telefono: '011 025 2702' },
        'LECCO': { nome: 'Lecco', email: 'tecnico_lc@algecopower.it', telefono: '011 025 2702' }
    };

    const keys = Object.keys(provMap);
    container.innerHTML = keys.map(pk => {
        const p = provMap[pk] || {};
        return `
            <div class="province-manage-card" data-prov-key="${pk}">
                <div class="province-card-top">
                    <span class="province-card-badge">
                        <i data-lucide="map-pin" style="width:14px; height:14px;"></i>
                        ${escapeHtml(p.nome || pk)} (${escapeHtml(pk)})
                    </span>
                    <span style="font-size:0.7rem; color:var(--text-muted); font-weight:600;">Provincia di Sistema</span>
                </div>
                <div class="province-card-fields">
                    <div class="province-field-row">
                        <label>Nome Visualizzato</label>
                        <input type="text" class="prov-input-nome" value="${escapeHtml(p.nome || pk)}" placeholder="Es. Torino">
                    </div>
                    <div class="province-field-row">
                        <label>Email Ufficio Tecnico</label>
                        <input type="email" class="prov-input-email" value="${escapeHtml(p.email || '')}" placeholder="tecnico_${pk.toLowerCase()}@algecopower.it" oninput="renderSignaturePreview()">
                    </div>
                    <div class="province-field-row">
                        <label>Numero di Telefono Ufficio</label>
                        <input type="text" class="prov-input-tel" value="${escapeHtml(p.telefono || '011 025 2702')}" placeholder="011 025 2702" oninput="renderSignaturePreview()">
                    </div>
                </div>
            </div>
        `;
    }).join('');

    lucide.createIcons();
    const countProvEl = document.getElementById('countProv');
    if (countProvEl) countProvEl.textContent = keys.length;
}

function switchSignatureMode(mode) {
    AppState.sigMode = mode;
    const isVisual = mode === 'visual';
    document.getElementById('modeSigVisualBtn').classList.toggle('active', isVisual);
    document.getElementById('modeSigHtmlBtn').classList.toggle('active', !isVisual);
    document.getElementById('sigVisualContainer').style.display = isVisual ? 'block' : 'none';
    document.getElementById('sigHtmlContainer').style.display = isVisual ? 'none' : 'block';

    if (isVisual) {
        renderSignaturePreview();
    }
}

function renderSignaturePreview() {
    const raw = document.getElementById('set_firma_html')?.value || '';
    const activeProv = AppState.provincia.toUpperCase();
    const provData = AppState.settings?.province?.[activeProv] || {};

    const activeCard = document.querySelector(`.province-manage-card[data-prov-key="${activeProv}"]`);
    const cardEmail = activeCard?.querySelector('.prov-input-email')?.value?.trim();
    const cardTel = activeCard?.querySelector('.prov-input-tel')?.value?.trim();

    const emailTec = cardEmail || provData.email || AppState.settings[`email_${AppState.provincia.toLowerCase()}`] || `tecnico_${AppState.provincia.toLowerCase()}@algecopower.it`;
    const telTec = cardTel || provData.telefono || '011 025 2702';
    const telClean = telTec.replace(/[^0-9+]/g, '');

    let resolved = raw.replace(/{email_tecnico}/g, emailTec);
    resolved = resolved.replace(/{telefono_tecnico}/g, telTec);
    resolved = resolved.replace(/{telefono_ufficio}/g, telTec);
    resolved = resolved.replace(/{telefono_tecnico_clean}/g, telClean);

    const box = document.getElementById('sigVisualPreview');
    if (box) box.innerHTML = resolved || '<em>Nessuna firma impostata</em>';
}

async function saveSettings() {
    // Raccogli i dati da tutte le card delle province
    const provinceMap = {};
    const cards = document.querySelectorAll('.province-manage-card');
    cards.forEach(card => {
        const pk = card.dataset.provKey;
        const nome = card.querySelector('.prov-input-nome')?.value.trim() || pk;
        const email = card.querySelector('.prov-input-email')?.value.trim() || '';
        const telefono = card.querySelector('.prov-input-tel')?.value.trim() || '011 025 2702';
        provinceMap[pk] = { nome, email, telefono };
    });

    const payload = {
        province: provinceMap,
        firma_html: document.getElementById('set_firma_html').value.trim()
    };

    try {
        const res = await fetch('/api/templates/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            showToast('Impostazioni firma, recapiti e province salvate con successo!');
            AppState.settings = data.settings || payload;
            renderProvincesCards();
            renderSignaturePreview();
        } else {
            showToast(data.error || 'Errore salvataggio', true);
        }
    } catch (e) {
        showToast('Errore: ' + e.message, true);
    }
}

// ============================================================================
// UTILITIES
// ============================================================================

function showToast(msg, isError = false) {
    const t = document.getElementById('toastNotification');
    t.textContent = msg;
    t.style.borderColor = isError ? 'var(--danger)' : 'var(--accent)';
    t.classList.add('show');
    setTimeout(() => {
        t.classList.remove('show');
    }, 3000);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function getCsrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
}
