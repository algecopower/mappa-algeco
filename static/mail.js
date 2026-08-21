/**
 * static/mail.js — Frontend Client per il modulo Gestione Mail Algeco.
 */

// Stato Globale dell'Applicazione
const MailState = {
    currentFolder: 'inbox',
    currentCategory: null,
    currentFilter: 'all',
    searchQuery: '',
    currentPage: 1,
    totalPages: 1,
    selectedMailId: null,
    selectedMailData: null,
    checkedMailIds: new Set(),
    categories: [],
    templates: [],
    standardAttachments: [],
    composer: {
        to: [],
        cc: [],
        bcc: [],
        attachments: [],
        draftId: null,
        scheduledAt: null,
        isMinimized: false,
        isMaximized: false
    }
};

let searchDebounceTimer = null;
let draftAutoSaveTimer = null;

// Inizializzazione al caricamento
document.addEventListener('DOMContentLoaded', () => {
    lucide.createIcons();
    initApp();
});

async function initApp() {
    const statusText = document.getElementById('syncStatusText');
    if (statusText) statusText.textContent = 'Connesso · Pronto';
    await loadCategories();
    await loadTemplates();
    await loadStandardAttachments();
    await loadMailStats();
    await loadMessages();

    if (window.location.hash === '#templates' || window.location.hash === '#modelli') {
        openTemplatesModal();
    }
}

// ============================================================================
// STATS & BADGES
// ============================================================================

async function loadMailStats() {
    try {
        const res = await fetch('/api/mail/stats');
        const data = await res.json();
        if (data.success && data.stats) {
            updateBadge('badge-inbox', data.stats.folders.inbox?.unread || 0);
            updateBadge('badge-starred', data.stats.folders.starred?.total || 0);
            updateBadge('badge-scheduled', data.stats.folders.scheduled?.total || 0);
            updateBadge('badge-sent', data.stats.folders.sent?.total || 0);
            updateBadge('badge-drafts', data.stats.folders.drafts?.total || 0);
            updateBadge('badge-trash', data.stats.folders.trash?.total || 0);

            // Aggiorna badge categorie
            MailState.categories.forEach(cat => {
                const cStat = data.stats.categories[cat.id];
                const unread = cStat?.unread || 0;
                const el = document.getElementById(`cat-badge-${cat.id}`);
                if (el) {
                    el.textContent = unread;
                    el.style.display = unread > 0 ? 'inline-block' : 'none';
                }
            });
        }
    } catch (e) {
        console.error('Errore caricamento statistiche mail:', e);
    }
}

function updateBadge(elementId, count) {
    const el = document.getElementById(elementId);
    if (el) {
        el.textContent = count;
        el.style.display = count > 0 ? 'inline-block' : 'none';
    }
}

// ============================================================================
// CATEGORIE & TEMPLATES
// ============================================================================

async function loadCategories() {
    try {
        const res = await fetch('/api/mail/categories');
        const data = await res.json();
        if (data.success && data.categories) {
            MailState.categories = data.categories;
            renderCategoriesNav();
            populateComposerCategorySelect();
        }
    } catch (e) {
        console.error('Errore caricamento categorie:', e);
    }
}

function renderCategoriesNav() {
    const container = document.getElementById('categoriesNavList');
    if (!container) return;

    if (!MailState.categories.length) {
        container.innerHTML = '<div style="font-size:0.75rem; color:var(--text-muted); padding:4px 8px;">Nessuna categoria</div>';
        return;
    }

    let html = '';
    MailState.categories.forEach(cat => {
        const isActive = MailState.currentCategory === cat.id;
        html += `
            <button class="cat-nav-item ${isActive ? 'active' : ''}" onclick="selectCategory('${cat.id}')">
                <span class="cat-dot" style="background-color: ${cat.color}; box-shadow: 0 0 6px ${cat.color}66;"></span>
                <span class="nav-label">${escapeHtml(cat.name)}</span>
                <span class="nav-badge" id="cat-badge-${cat.id}" style="background-color:${cat.color}22; color:${cat.color}; border:1px solid ${cat.color}44; display:none;">0</span>
            </button>
        `;
    });
    container.innerHTML = html;
}

function populateComposerCategorySelect() {
    const sel = document.getElementById('composerCategorySelect');
    if (!sel) return;
    sel.innerHTML = '<option value="">🏷️ Auto-categorizza</option>' +
        MailState.categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
}

async function loadTemplates() {
    try {
        const res = await fetch('/api/mail/templates');
        const data = await res.json();
        if (data.success && data.templates) {
            MailState.templates = data.templates;
        }
    } catch (e) {
        console.error('Errore caricamento template:', e);
    }
}

async function loadStandardAttachments() {
    try {
        const res = await fetch('/api/mail/standard-attachments');
        const data = await res.json();
        if (data.success && data.attachments) {
            MailState.standardAttachments = data.attachments;
        }
    } catch (e) {
        console.error('Errore caricamento allegati standard:', e);
    }
}

// ============================================================================
// CARICAMENTO & RENDERING LISTA EMAIL
// ============================================================================

async function loadMessages() {
    const container = document.getElementById('messagesContainer');
    if (container) {
        container.innerHTML = `
            <div class="mail-loading-state">
                <i data-lucide="loader" class="spin"></i>
                <span>Caricamento messaggi...</span>
            </div>
        `;
        lucide.createIcons();
    }

    const params = new URLSearchParams();
    if (MailState.currentFolder) params.set('folder', MailState.currentFolder);
    if (MailState.currentCategory) params.set('category_id', MailState.currentCategory);
    if (MailState.searchQuery) params.set('q', MailState.searchQuery);
    if (MailState.currentFilter === 'unread') params.set('unread', '1');
    if (MailState.currentFilter === 'starred') params.set('starred', '1');
    if (MailState.currentFilter === 'attachments') params.set('has_attachments', '1');
    params.set('page', MailState.currentPage);

    try {
        const res = await fetch(`/api/mail/messages?${params.toString()}`);
        const data = await res.json();
        if (data.success) {
            MailState.totalPages = data.total_pages;
            renderMessagesList(data.messages, data.total, data.page, data.per_page);
        } else {
            container.innerHTML = `<div class="mail-empty-list"><p>Errore: ${escapeHtml(data.error)}</p></div>`;
        }
    } catch (e) {
        console.error('Errore fetch messaggi:', e);
        if (container) {
            container.innerHTML = `<div class="mail-empty-list"><p>Impossibile caricare i messaggi.</p></div>`;
        }
    }
}

function renderMessagesList(messages, total, page, perPage) {
    const container = document.getElementById('messagesContainer');
    const paginationInfo = document.getElementById('paginationInfo');
    const prevBtn = document.getElementById('prevPageBtn');
    const nextBtn = document.getElementById('nextPageBtn');

    // Aggiorna Paginazione
    if (total === 0) {
        paginationInfo.textContent = '0 di 0';
    } else {
        const start = (page - 1) * perPage + 1;
        const end = Math.min(page * perPage, total);
        paginationInfo.textContent = `${start} - ${end} di ${total}`;
    }
    prevBtn.disabled = page <= 1;
    nextBtn.disabled = page >= MailState.totalPages;

    if (!messages || messages.length === 0) {
        container.innerHTML = `
            <div class="mail-empty-list">
                <i data-lucide="inbox" style="width:40px;height:40px;opacity:0.4;"></i>
                <p>Nessun messaggio trovato in questa vista.</p>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    let html = '';
    messages.forEach(msg => {
        const isSelected = MailState.selectedMailId === msg.id;
        const isChecked = MailState.checkedMailIds.has(msg.id);
        const isUnread = !msg.is_read;
        const isStarred = Boolean(msg.is_starred);
        const cat = MailState.categories.find(c => c.id === msg.category_id);

        let catBadge = '';
        if (cat) {
            catBadge = `<span class="mail-cat-badge" style="background:${cat.badge_bg || cat.color + '22'}; color:${cat.badge_text || cat.color};">${escapeHtml(cat.name)}</span>`;
        }

        let senderName = msg.from_name || msg.from_addr || 'Sconosciuto';
        if (MailState.currentFolder === 'sent') {
            senderName = `A: ${msg.to_addrs.join(', ') || '(Nessun destinatario)'}`;
        }

        html += `
            <div class="mail-item ${isUnread ? 'unread' : ''} ${isSelected ? 'selected' : ''}" 
                 id="mail-item-${msg.id}" 
                 onclick="handleMailItemClick(event, ${msg.id})">
                <div class="mail-item-check" onclick="event.stopPropagation()">
                    <input type="checkbox" value="${msg.id}" ${isChecked ? 'checked' : ''} onchange="toggleMailCheck(${msg.id}, this.checked)">
                </div>
                <div class="mail-item-star ${isStarred ? 'starred' : ''}" onclick="toggleStar(event, ${msg.id})" title="Preferito">
                    <i data-lucide="star" style="width:15px;height:15px;${isStarred ? 'fill:#eab308;' : ''}"></i>
                </div>
                <div class="mail-item-content">
                    <div class="mail-item-top">
                        <span class="mail-item-sender">${escapeHtml(senderName)}</span>
                        <span class="mail-item-date">${escapeHtml(msg.display_date)}</span>
                    </div>
                    <div class="mail-item-subject-row">
                        <span class="mail-item-subject">${escapeHtml(msg.subject)}</span>
                        ${catBadge}
                    </div>
                    <div class="mail-item-snippet">${escapeHtml(msg.snippet || '')}</div>
                    <div class="mail-item-meta">
                        ${msg.linked_pod ? `<span class="badge-linked-pod"><i data-lucide="zap" style="width:10px;height:10px;"></i> POD: ${escapeHtml(msg.linked_pod)}</span>` : ''}
                        ${msg.has_attachments ? `<span class="badge-has-att"><i data-lucide="paperclip" style="width:11px;height:11px;"></i> ${msg.attachments.length}</span>` : ''}
                        ${msg.is_scheduled ? `<span style="color:#f59e0b; font-size:0.68rem;"><i data-lucide="clock" style="width:10px;height:10px;"></i> Programmata: ${escapeHtml(msg.display_date)}</span>` : ''}
                    </div>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
    lucide.createIcons();
}

// ============================================================================
// NAVIGAZIONE CARTELLE & FILTRI
// ============================================================================

function selectFolder(folderName) {
    MailState.currentFolder = folderName;
    MailState.currentCategory = null;
    MailState.currentPage = 1;
    MailState.checkedMailIds.clear();
    updateBatchActionsBar();

    // Aggiorna classi active sidebar
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.folder === folderName);
    });
    document.querySelectorAll('.categories-nav .cat-nav-item').forEach(el => el.classList.remove('active'));

    const titles = {
        'inbox': 'Posta in arrivo',
        'starred': 'Preferiti ⭐',
        'scheduled': 'Programmate ⏳',
        'sent': 'Posta inviata',
        'drafts': 'Bozze',
        'archive': 'Archivio',
        'trash': 'Cestino'
    };
    document.getElementById('currentFolderTitle').textContent = titles[folderName] || folderName;

    loadMessages();
}

function selectCategory(catId) {
    MailState.currentCategory = catId;
    MailState.currentFolder = null;
    MailState.currentPage = 1;
    MailState.checkedMailIds.clear();
    updateBatchActionsBar();

    document.querySelectorAll('.sidebar-nav .nav-item').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.categories-nav .cat-nav-item').forEach(el => {
        const isActive = el.getAttribute('onclick')?.includes(`'${catId}'`);
        el.classList.toggle('active', isActive);
    });

    const cat = MailState.categories.find(c => c.id === catId);
    document.getElementById('currentFolderTitle').textContent = cat ? `🏷️ ${cat.name}` : 'Categoria';

    loadMessages();
}

function setQuickFilter(filter) {
    MailState.currentFilter = filter;
    MailState.currentPage = 1;

    document.querySelectorAll('.filter-pills .pill-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });

    loadMessages();
}

function handleSearchInput(val) {
    clearTimeout(searchDebounceTimer);
    const clearBtn = document.getElementById('searchClearBtn');
    if (clearBtn) clearBtn.style.display = val ? 'flex' : 'none';

    searchDebounceTimer = setTimeout(() => {
        MailState.searchQuery = val.trim();
        MailState.currentPage = 1;
        loadMessages();
    }, 300);
}

function clearSearch() {
    const input = document.getElementById('globalMailSearch');
    if (input) input.value = '';
    document.getElementById('searchClearBtn').style.display = 'none';
    MailState.searchQuery = '';
    MailState.currentPage = 1;
    loadMessages();
}

function changePage(delta) {
    const newPage = MailState.currentPage + delta;
    if (newPage >= 1 && newPage <= MailState.totalPages) {
        MailState.currentPage = newPage;
        loadMessages();
    }
}

// ============================================================================
// VISUALIZZATORE DETTAGLIO EMAIL (READER PANE)
// ============================================================================

async function handleMailItemClick(event, mailId) {
    // Non aprire se cliccato su checkbox o star
    if (event.target.closest('.mail-item-check') || event.target.closest('.mail-item-star')) {
        return;
    }

    MailState.selectedMailId = mailId;

    // Aggiorna stile riga attiva
    document.querySelectorAll('.mail-item').forEach(el => el.classList.remove('selected'));
    const itemEl = document.getElementById(`mail-item-${mailId}`);
    if (itemEl) {
        itemEl.classList.add('selected');
        itemEl.classList.remove('unread');
    }

    const emptyState = document.getElementById('readerEmptyState');
    const contentWrap = document.getElementById('readerContentWrap');
    emptyState.style.display = 'none';
    contentWrap.style.display = 'flex';

    try {
        const res = await fetch(`/api/mail/messages/${mailId}?mark_read=1`);
        const data = await res.json();
        if (data.success && data.message) {
            MailState.selectedMailData = data.message;
            renderMailDetail(data.message);
            loadMailStats();
        }
    } catch (e) {
        console.error('Errore caricamento dettaglio email:', e);
    }
}

function renderMailDetail(msg) {
    // Subject & Categoria
    document.getElementById('readerSubjectTitle').textContent = msg.subject || '(Nessun oggetto)';
    
    // Category Badge Wrap
    const catWrap = document.getElementById('readerCategoryBadgeWrap');
    const currentCat = MailState.categories.find(c => c.id === msg.category_id);
    let catHtml = `
        <select class="btn-soft" style="font-size:0.75rem; padding:3px 8px;" onchange="changeMessageCategory(${msg.id}, this.value)">
            <option value="">🏷️ Nessuna categoria</option>
            ${MailState.categories.map(c => `<option value="${c.id}" ${msg.category_id === c.id ? 'selected' : ''}>🏷️ ${escapeHtml(c.name)}</option>`).join('')}
        </select>
    `;
    catWrap.innerHTML = catHtml;

    // Sender Info
    const senderName = msg.from_name || msg.from_addr;
    document.getElementById('readerSenderName').textContent = senderName;
    document.getElementById('readerSenderEmail').textContent = `<${msg.from_addr}>`;
    document.getElementById('readerDateFull').textContent = msg.display_date_full || msg.display_date;

    // Initials Avatar
    const initials = senderName.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase() || 'AP';
    document.getElementById('readerSenderAvatar').textContent = initials;

    // Recipients
    document.getElementById('readerToRecipients').textContent = msg.to_addrs.join(', ') || '-';
    const ccWrap = document.getElementById('readerCcWrap');
    if (msg.cc_addrs && msg.cc_addrs.length) {
        ccWrap.style.display = 'inline';
        document.getElementById('readerCcRecipients').textContent = msg.cc_addrs.join(', ');
    } else {
        ccWrap.style.display = 'none';
    }

    // Star Button
    const starBtn = document.getElementById('readerStarBtn');
    if (starBtn) {
        starBtn.innerHTML = `<i data-lucide="star" style="${msg.is_starred ? 'fill:#eab308; color:#eab308;' : ''}"></i>`;
    }

    // Linked Intel Card (POD / Comune / Azienda)
    const linkedCard = document.getElementById('readerLinkedCard');
    if (msg.pod_detail || msg.linked_pod) {
        linkedCard.style.display = 'flex';
        document.getElementById('linkedCardIcon').innerHTML = '<i data-lucide="zap"></i>';
        const nom = msg.pod_detail ? (msg.pod_detail.nominativo || '') : '';
        document.getElementById('linkedCardTitle').textContent = `Contatore POD ${msg.linked_pod}${nom ? ' (' + nom + ')' : ''}`;
        if (msg.pod_detail) {
            document.getElementById('linkedCardDesc').textContent = `${msg.pod_detail.indirizzo}, ${msg.pod_detail.comune} · Operatore: ${msg.pod_detail.operatore || 'N.D.'} · Data: ${msg.pod_detail.data_pianificata || 'N.D.'}`;
        } else {
            document.getElementById('linkedCardDesc').textContent = 'POD rilevato nel messaggio. Clicca per aprirlo e localizzarlo sulla mappa.';
        }
        document.getElementById('linkedCardAction').innerHTML = `
            <a href="/?search=${encodeURIComponent(msg.linked_pod)}" target="_blank" class="btn-soft" style="font-size:0.75rem;"><i data-lucide="map-pin"></i> Vedi in Mappa</a>
        `;
    } else if (msg.comune_detail || msg.linked_comune) {
        linkedCard.style.display = 'flex';
        document.getElementById('linkedCardIcon').innerHTML = '<i data-lucide="building-2"></i>';
        document.getElementById('linkedCardTitle').textContent = `Comune di ${msg.linked_comune}`;
        document.getElementById('linkedCardDesc').textContent = msg.comune_detail ? `Polizia: ${msg.comune_detail.telefono_polizia || '-'} · Uff. Tecnico: ${msg.comune_detail.telefono_uff_tecnico || '-'}` : 'Comune rilevato. Clicca per accedere alla scheda dedicata.';
        document.getElementById('linkedCardAction').innerHTML = `
            <a href="/comuni" target="_blank" class="btn-soft" style="font-size:0.75rem;"><i data-lucide="building"></i> Scheda Comune</a>
        `;
    } else {
        linkedCard.style.display = 'none';
    }

    // Body
    const bodyContent = document.getElementById('mailBodyContent');
    const readerBody = document.getElementById('readerBodyContainer');
    const isForcedLight = readerBody && readerBody.classList.contains('force-light-card');

    if (msg.body_html) {
        let renderedHtml = msg.body_html;
        if (renderedHtml.includes('cid:') && msg.attachments && msg.attachments.length) {
            msg.attachments.forEach(att => {
                const attUrl = `/api/mail/attachment/${msg.id}/${encodeURIComponent(att.filename || '')}?inline=1`;
                if (att.content_id) {
                    renderedHtml = renderedHtml.replaceAll(`cid:${att.content_id}`, attUrl);
                }
                if (att.filename) {
                    renderedHtml = renderedHtml.replaceAll(`cid:${att.filename}`, attUrl);
                }
            });
            if (msg.attachments.length === 1) {
                const singleUrl = `/api/mail/attachment/${msg.id}/${encodeURIComponent(msg.attachments[0].filename || '')}?inline=1`;
                renderedHtml = renderedHtml.replace(/src=["']cid:[^"']+["']/gi, `src="${singleUrl}"`);
            }
        }
        bodyContent.innerHTML = renderedHtml;
        if (!isForcedLight) {
            normalizeEmailHtmlForDarkMode(bodyContent);
        }
    } else {
        bodyContent.innerHTML = `<pre style="white-space: pre-wrap; font-family: inherit; margin: 0;">${escapeHtml(msg.body_text || '')}</pre>`;
    }

    // Attachments Box
    const attBox = document.getElementById('readerAttachmentsBox');
    const attGrid = document.getElementById('readerAttachmentsGrid');
    if (msg.attachments && msg.attachments.length) {
        attBox.style.display = 'block';
        document.getElementById('attachmentsCountLabel').textContent = `Allegati (${msg.attachments.length})`;
        
        let attHtml = '';
        msg.attachments.forEach(att => {
            const ext = att.filename.split('.').pop().toLowerCase();
            let icon = 'file-text';
            if (['pdf'].includes(ext)) icon = 'file-text';
            if (['xlsx', 'xls', 'csv'].includes(ext)) icon = 'file-spreadsheet';
            if (['jpg', 'jpeg', 'png', 'gif'].includes(ext)) icon = 'image';
            if (['zip', 'rar', '7z'].includes(ext)) icon = 'archive';

            const sizeFormatted = att.size_bytes ? `${Math.round(att.size_bytes / 1024)} KB` : '';

            attHtml += `
                <div class="att-card">
                    <div class="att-card-icon"><i data-lucide="${icon}"></i></div>
                    <div class="att-card-info">
                        <div class="att-card-name" title="${escapeHtml(att.filename)}">${escapeHtml(att.filename)}</div>
                        <div class="att-card-size">${sizeFormatted}</div>
                    </div>
                    <div class="att-card-actions">
                        <a href="/api/mail/attachment/${msg.id}/${encodeURIComponent(att.filename)}" target="_blank" class="btn-att-dl" title="Scarica">
                            <i data-lucide="download"></i>
                        </a>
                    </div>
                </div>
            `;
        });
        attGrid.innerHTML = attHtml;
    } else {
        attBox.style.display = 'none';
    }

    // Popola Frasario Rapido per quick reply
    populateQuickReplySnippets();
    lucide.createIcons();
}

function isDarkColorString(colorStr) {
    if (!colorStr) return false;
    colorStr = colorStr.trim().toLowerCase();
    if (['#000', '#000000', 'black', '#111', '#111111', '#222', '#222222', '#333', '#333333', '#444', '#444444', '#555', '#555555', '#1f497d', '#002060', '#000033', 'navy', 'darkblue'].includes(colorStr)) {
        return true;
    }
    const rgbMatch = colorStr.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (rgbMatch) {
        const r = parseInt(rgbMatch[1]);
        const g = parseInt(rgbMatch[2]);
        const b = parseInt(rgbMatch[3]);
        return (0.299 * r + 0.587 * g + 0.114 * b) < 135;
    }
    if (colorStr.startsWith('#')) {
        let hex = colorStr.slice(1);
        if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
        if (hex.length === 6) {
            const r = parseInt(hex.substring(0, 2), 16);
            const g = parseInt(hex.substring(2, 4), 16);
            const b = parseInt(hex.substring(4, 6), 16);
            return (0.299 * r + 0.587 * g + 0.114 * b) < 135;
        }
    }
    return false;
}

function normalizeEmailHtmlForDarkMode(container) {
    if (!container) return;
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    const readerBody = document.getElementById('readerBodyContainer');
    const isForcedLight = readerBody && readerBody.classList.contains('force-light-card');
    
    if (!isDark || isForcedLight) return;

    const elements = container.querySelectorAll('*');
    elements.forEach(el => {
        // Tag <font color="...">
        if (el.tagName.toLowerCase() === 'font') {
            const fontColor = el.getAttribute('color');
            if (isDarkColorString(fontColor)) {
                el.removeAttribute('color');
                el.style.color = '#e2e8f0';
            }
        }

        // Inline style.color
        const inlineColor = el.style.color;
        if (inlineColor && isDarkColorString(inlineColor)) {
            const bg = el.style.backgroundColor || el.style.background;
            if (!bg || isDarkColorString(bg)) {
                el.style.color = '#e2e8f0';
            }
        }

        // Tag <a> con colore scuro o blu troppo profondo
        if (el.tagName.toLowerCase() === 'a') {
            const aColor = el.style.color;
            if (!aColor || isDarkColorString(aColor)) {
                el.style.color = '#60a5fa';
            }
        }

        // Separatori <hr>
        if (el.tagName.toLowerCase() === 'hr') {
            el.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        }
    });
}

function toggleMailReaderTheme() {
    const readerBody = document.getElementById('readerBodyContainer');
    const btn = document.getElementById('toggleMailThemeBtn');
    if (!readerBody) return;

    const isLightNow = readerBody.classList.toggle('force-light-card');
    if (btn) {
        btn.innerHTML = `<i data-lucide="${isLightNow ? 'moon' : 'sun'}"></i>`;
        btn.title = isLightNow ? "Torna a modalità lettura scura ad alto contrasto" : "Alterna sfondo chiaro / scuro per questa email";
        lucide.createIcons();
    }

    const bodyContent = document.getElementById('mailBodyContent');
    if (isLightNow) {
        if (MailState.selectedMailData && MailState.selectedMailData.body_html && bodyContent) {
            bodyContent.innerHTML = MailState.selectedMailData.body_html;
        }
        showToast("Modalità lettura: Sfondo Chiaro attivo per questa email.");
    } else {
        if (MailState.selectedMailData && MailState.selectedMailData.body_html && bodyContent) {
            bodyContent.innerHTML = MailState.selectedMailData.body_html;
            normalizeEmailHtmlForDarkMode(bodyContent);
        }
        showToast("Modalità lettura: Sfondo Scuro ad alto contrasto.");
    }
}

async function changeMessageCategory(msgId, catId) {
    try {
        await fetch('/api/mail/set-category', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: msgId, category_id: catId })
        });
        loadMessages();
        loadMailStats();
    } catch (e) {
        console.error('Errore cambio categoria:', e);
    }
}

// ============================================================================
// AZIONI SUL MESSAGGIO CORRENTE
// ============================================================================

async function toggleStar(event, msgId) {
    if (event) event.stopPropagation();
    try {
        await fetch('/api/mail/toggle-starred', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: msgId })
        });
        loadMessages();
        loadMailStats();
    } catch (e) {
        console.error('Errore toggle star:', e);
    }
}

function toggleCurrentMailStar() {
    if (MailState.selectedMailId) {
        toggleStar(null, MailState.selectedMailId);
    }
}

async function archiveCurrentMail() {
    if (!MailState.selectedMailId) return;
    try {
        await fetch('/api/mail/move-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: MailState.selectedMailId, folder: 'archive' })
        });
        resetReader();
        loadMessages();
        loadMailStats();
    } catch (e) {
        console.error('Errore archiviazione:', e);
    }
}

async function deleteCurrentMail() {
    if (!MailState.selectedMailId) return;
    if (!confirm("Sei sicuro di voler eliminare questo messaggio?")) return;
    try {
        await fetch(`/api/mail/messages/${MailState.selectedMailId}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCsrfToken() }
        });
        resetReader();
        loadMessages();
        loadMailStats();
    } catch (e) {
        console.error('Errore eliminazione:', e);
    }
}

function printCurrentMail() {
    if (!MailState.selectedMailId || !MailState.selectedMailData) {
        showToast('Seleziona prima un messaggio email da stampare.', true);
        return;
    }
    window.print();
}

function resetReader() {
    MailState.selectedMailId = null;
    MailState.selectedMailData = null;
    document.getElementById('readerEmptyState').style.display = 'flex';
    document.getElementById('readerContentWrap').style.display = 'none';
}

// ============================================================================
// BATCH ACTIONS (SELEZIONE MULTIPLA)
// ============================================================================

function toggleSelectAllMessages(checked) {
    document.querySelectorAll('.mail-item input[type="checkbox"]').forEach(cb => {
        cb.checked = checked;
        const mid = parseInt(cb.value);
        if (checked) MailState.checkedMailIds.add(mid);
        else MailState.checkedMailIds.delete(mid);
    });
    updateBatchActionsBar();
}

function toggleMailCheck(msgId, checked) {
    if (checked) MailState.checkedMailIds.add(msgId);
    else MailState.checkedMailIds.delete(msgId);
    updateBatchActionsBar();
}

function updateBatchActionsBar() {
    const bar = document.getElementById('batchActionsBar');
    const pills = document.getElementById('filterPillsContainer');
    const countEl = document.getElementById('batchSelectedCount');
    const count = MailState.checkedMailIds.size;

    if (count > 0) {
        if (pills) pills.style.display = 'none';
        if (bar) bar.style.display = 'flex';
        if (countEl) countEl.textContent = count === 1 ? '1 selezionata' : `${count} selezionate`;
        renderBatchCategoryMenu();
    } else {
        if (bar) bar.style.display = 'none';
        if (pills) pills.style.display = 'flex';
        const selectAll = document.getElementById('selectAllCheckbox');
        if (selectAll) selectAll.checked = false;
        closeBatchCatDropdown();
    }
}

function toggleBatchCatDropdown(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const dropdown = document.getElementById('batchCatDropdown');
    if (!dropdown) return;
    const isActive = dropdown.classList.toggle('active');
    if (isActive) {
        renderBatchCategoryMenu();
    }
}

function closeBatchCatDropdown() {
    const dropdown = document.getElementById('batchCatDropdown');
    if (dropdown) dropdown.classList.remove('active');
}

function renderBatchCategoryMenu() {
    const menu = document.getElementById('batchCategoryMenu');
    if (!menu) return;
    menu.innerHTML = MailState.categories.map(c => `
        <div class="dropdown-item" onclick="batchAssignCategory('${c.id}')">
            <span class="cat-dot" style="display:inline-block; vertical-align:middle; margin-right:6px; background:${c.color};"></span>
            ${escapeHtml(c.name)}
        </div>
    `).join('') + `<div class="dropdown-item" onclick="batchAssignCategory(null)" style="color:var(--text-muted); border-top:1px dashed var(--border); margin-top:4px; padding-top:6px;">Rimuovi categoria</div>`;
}

async function executeBatchAction(action, value = null) {
    const ids = Array.from(MailState.checkedMailIds);
    if (!ids.length) return;

    try {
        const res = await fetch('/api/mail/batch-action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ ids: ids, action: action, value: value })
        });
        const data = await res.json();
        if (data.success) {
            MailState.checkedMailIds.clear();
            updateBatchActionsBar();
            await loadMessages();
            await loadMailStats();
            if (action === 'mark_read') showToast(`${ids.length} email segnate come lette.`);
            else if (action === 'mark_unread') showToast(`${ids.length} email segnate come non lette.`);
            else if (action === 'star') showToast("Email aggiunte ai preferiti.");
            else if (action === 'move') showToast(`Email spostate.`);
            else if (action === 'change_category') showToast("Categoria aggiornata.");
            else if (action === 'delete') showToast(`${ids.length} email eliminate.`);
        } else {
            showToast(`Errore azione batch: ${data.error}`, true);
        }
    } catch (e) {
        console.error('Errore batch action:', e);
        showToast('Errore durante l\'azione: ' + e.message, true);
    }
}

function batchMarkRead(isRead) { executeBatchAction(isRead ? 'mark_read' : 'mark_unread'); }
function batchToggleStar() { executeBatchAction('star'); }
function batchMoveToArchive() { executeBatchAction('move', 'archive'); }
function batchAssignCategory(catId) {
    closeBatchCatDropdown();
    executeBatchAction('change_category', catId);
}
function batchDelete() {
    if (confirm(`Eliminare ${MailState.checkedMailIds.size} messaggi selezionati?`)) {
        executeBatchAction('delete');
    }
}

async function toggleCurrentMailReadStatus() {
    if (!MailState.selectedMailId) return;
    try {
        await fetch('/api/mail/mark-read', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: MailState.selectedMailId, is_read: false })
        });
        showToast("Email segnata come non letta.");
        resetReader();
        await loadMessages();
        await loadMailStats();
    } catch (e) {
        showToast("Errore: " + e.message, true);
    }
}

// Chiusura dropdown se si clicca fuori
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('batchCatDropdown');
    if (dropdown && !dropdown.contains(e.target)) {
        dropdown.classList.remove('active');
    }
});

// ============================================================================
// COMPOSER (SCRIVI MAIL, RISPONDI, INOLTRA)
// ============================================================================

function openComposer(presetData = {}) {
    const overlay = document.getElementById('composerOverlay');
    const win = document.getElementById('composerWindow');
    overlay.style.display = 'block';
    win.classList.remove('minimized');

    // Reset Form
    MailState.composer = {
        to: presetData.to || [],
        cc: presetData.cc || [],
        bcc: presetData.bcc || [],
        attachments: presetData.attachments || [],
        draftId: presetData.draftId || null,
        scheduledAt: presetData.scheduledAt || null,
        isMinimized: false,
        isMaximized: false
    };

    document.getElementById('composerDraftId').value = MailState.composer.draftId || '';
    document.getElementById('composerScheduledAt').value = MailState.composer.scheduledAt || '';
    document.getElementById('composerSubjectInput').value = presetData.subject || '';
    document.getElementById('composerEditor').innerHTML = presetData.bodyHtml || '';
    document.getElementById('composerLinkedPod').value = presetData.linkedPod || '';
    document.getElementById('composerTitle').textContent = presetData.title || 'Nuovo Messaggio';
    document.getElementById('sendBtnLabel').textContent = 'Invia Adesso';

    renderRecipientChips('to');
    renderRecipientChips('cc');
    renderRecipientChips('bcc');
    renderComposerAttachmentChips();
    populateComposerTemplatesMenu();
    populateComposerSnippetsMenu();

    // Focus sull'input destinatario se vuoto
    if (!MailState.composer.to.length) {
        document.getElementById('composerToInput').focus();
    } else {
        document.getElementById('composerEditor').focus();
    }

    lucide.createIcons();
}

function replyCurrentMail(replyAll = false) {
    const msg = MailState.selectedMailData;
    if (!msg) return;

    const to = [msg.from_addr];
    const cc = replyAll ? (msg.cc_addrs || []) : [];
    const subject = msg.subject.toLowerCase().startsWith('re:') ? msg.subject : `Re: ${msg.subject}`;
    
    const quoteHtml = `<br><br><blockquote>--- Messaggio Originale da <strong>${escapeHtml(msg.from_name || msg.from_addr)}</strong> il ${escapeHtml(msg.display_date_full)} ---<br>${msg.body_html || escapeHtml(msg.body_text || '')}</blockquote>`;

    openComposer({
        to: to,
        cc: cc,
        subject: subject,
        bodyHtml: quoteHtml,
        linkedPod: msg.linked_pod || '',
        title: `Risposta a: ${msg.from_name || msg.from_addr}`
    });
}

function forwardCurrentMail() {
    const msg = MailState.selectedMailData;
    if (!msg) return;

    const subject = msg.subject.toLowerCase().startsWith('fwd:') ? msg.subject : `Fwd: ${msg.subject}`;
    const quoteHtml = `<br><br><blockquote>--- Messaggio Inoltrato ---<br><strong>Da:</strong> ${escapeHtml(msg.from_name || msg.from_addr)} &lt;${escapeHtml(msg.from_addr)}&gt;<br><strong>Data:</strong> ${escapeHtml(msg.display_date_full)}<br><strong>Oggetto:</strong> ${escapeHtml(msg.subject)}<br><br>${msg.body_html || escapeHtml(msg.body_text || '')}</blockquote>`;

    openComposer({
        to: [],
        subject: subject,
        bodyHtml: quoteHtml,
        attachments: msg.attachments || [],
        linkedPod: msg.linked_pod || '',
        title: `Inoltra: ${msg.subject}`
    });
}

function closeComposer() {
    // Salva bozza automaticamente prima di chiudere se c'è testo
    const subject = document.getElementById('composerSubjectInput').value;
    const body = document.getElementById('composerEditor').innerHTML;
    if ((subject || body) && !MailState.composer.draftId) {
        saveDraftManual(true);
    }

    const overlay = document.getElementById('composerOverlay');
    const win = document.getElementById('composerWindow');
    overlay.style.display = 'none';
    overlay.classList.remove('has-backdrop');
    document.body.classList.remove('composer-maximized');
    if (win) {
        win.classList.remove('maximized');
        win.classList.remove('minimized');
    }
}

function toggleMinimizeComposer() {
    const overlay = document.getElementById('composerOverlay');
    const win = document.getElementById('composerWindow');
    win.classList.remove('maximized');
    overlay.classList.remove('has-backdrop');
    document.body.classList.remove('composer-maximized');
    win.classList.toggle('minimized');
}

function toggleMaximizeComposer() {
    const overlay = document.getElementById('composerOverlay');
    const win = document.getElementById('composerWindow');
    win.classList.remove('minimized');
    const isMax = win.classList.toggle('maximized');
    overlay.classList.toggle('has-backdrop', isMax);
    document.body.classList.toggle('composer-maximized', isMax);
}

function toggleCcFields(type) {
    const row = document.getElementById(type === 'cc' ? 'composerCcRow' : 'composerBccRow');
    if (row) {
        const isHidden = row.style.display === 'none';
        row.style.display = isHidden ? 'flex' : 'none';
        if (isHidden) {
            document.getElementById(type === 'cc' ? 'composerCcInput' : 'composerBccInput').focus();
        }
    }
}

// Chips per Destinatari
function renderRecipientChips(field) {
    const container = document.getElementById(`composer${field.charAt(0).toUpperCase() + field.slice(1)}Chips`);
    if (!container) return;

    const list = MailState.composer[field];
    container.innerHTML = list.map((email, idx) => `
        <span class="rec-chip">
            ${escapeHtml(email)}
            <span class="rec-chip-remove" onclick="removeRecipientChip('${field}', ${idx})">&times;</span>
        </span>
    `).join('');
}

function removeRecipientChip(field, index) {
    MailState.composer[field].splice(index, 1);
    renderRecipientChips(field);
}

function addRecipientChip(field, email) {
    email = email.trim();
    if (email && !MailState.composer[field].includes(email)) {
        MailState.composer[field].push(email);
        renderRecipientChips(field);
    }
    document.getElementById(`composer${field.charAt(0).toUpperCase() + field.slice(1)}Input`).value = '';
    document.getElementById('contactsAutocompleteMenu').style.display = 'none';
}

// Autocompletamento contatti
let contactSearchDebounce = null;
function handleContactSearch(query, field) {
    clearTimeout(contactSearchDebounce);
    const menu = document.getElementById('contactsAutocompleteMenu');

    if (!query || query.trim().length < 1) {
        menu.style.display = 'none';
        return;
    }

    contactSearchDebounce = setTimeout(async () => {
        try {
            const res = await fetch(`/api/mail/contacts-search?q=${encodeURIComponent(query.trim())}`);
            const data = await res.json();
            if (data.success && data.contacts.length) {
                menu.innerHTML = data.contacts.map(c => `
                    <div class="dropdown-item" onclick="addRecipientChip('${field}', '${escapeHtml(c.email)}')">
                        <div class="dropdown-item-title">${escapeHtml(c.name)} <small style="color:var(--text-muted); font-weight:normal;">&lt;${escapeHtml(c.email)}&gt;</small></div>
                        <div class="dropdown-item-desc">${escapeHtml(c.detail || c.type)}</div>
                    </div>
                `).join('');
                menu.style.display = 'block';
            } else {
                menu.style.display = 'none';
            }
        } catch (e) {
            menu.style.display = 'none';
        }
    }, 200);
}

// Enter o virgola inserisce chip
['composerToInput', 'composerCcInput', 'composerBccInput'].forEach(id => {
    const input = document.getElementById(id);
    if (input) {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault();
                const field = id.replace('composer', '').replace('Input', '').toLowerCase();
                addRecipientChip(field, input.value);
            }
        });
    }
});

// ============================================================================
// SPIACCICA TEMPLATE & FRASARIO
// ============================================================================

function toggleTemplateDropdown() {
    const menu = document.getElementById('composerTemplatesMenu');
    const isVisible = menu.style.display === 'block';
    document.querySelectorAll('.templates-dropdown-menu, .snippets-dropdown-menu').forEach(m => m.style.display = 'none');
    menu.style.display = isVisible ? 'none' : 'block';
}

function toggleSnippetDropdown(menuId) {
    const menu = document.getElementById(menuId);
    const isVisible = menu.style.display === 'block';
    document.querySelectorAll('.templates-dropdown-menu, .snippets-dropdown-menu').forEach(m => m.style.display = 'none');
    menu.style.display = isVisible ? 'none' : 'block';
}

function populateComposerTemplatesMenu() {
    const menu = document.getElementById('composerTemplatesMenu');
    if (!menu) return;

    const templates = MailState.templates.filter(t => t.type === 'template');
    if (!templates.length) {
        menu.innerHTML = '<div style="padding:8px; font-size:0.75rem; color:var(--text-muted);">Nessun template disponibile</div>';
        return;
    }

    menu.innerHTML = templates.map(t => `
        <div class="dropdown-item" onclick="insertTemplateIntoComposer('${t.id}')">
            <div class="dropdown-item-title">🪄 ${escapeHtml(t.title)}</div>
            <div class="dropdown-item-desc">${escapeHtml(t.subject || t.body.substring(0, 70))}...</div>
        </div>
    `).join('');
}

function populateComposerSnippetsMenu() {
    const menu = document.getElementById('composerSnippetsMenu');
    if (!menu) return;

    const snippets = MailState.templates.filter(t => t.type === 'snippet');
    if (!snippets.length) {
        menu.innerHTML = '<div style="padding:8px; font-size:0.75rem; color:var(--text-muted);">Nessuna frase rapida disponibile</div>';
        return;
    }

    menu.innerHTML = snippets.map(s => `
        <div class="dropdown-item" onclick="insertSnippetAtCursor('${s.id}')">
            <div class="dropdown-item-title">💬 ${escapeHtml(s.title)}</div>
            <div class="dropdown-item-desc">${escapeHtml(s.body.substring(0, 60))}...</div>
        </div>
    `).join('');
}

function populateQuickReplySnippets() {
    const menu = document.getElementById('quickReplySnippets');
    if (!menu) return;

    const snippets = MailState.templates.filter(t => t.type === 'snippet');
    menu.innerHTML = snippets.map(s => `
        <div class="dropdown-item" onclick="insertQuickReplySnippet('${s.id}')">
            <div class="dropdown-item-title">${escapeHtml(s.title)}</div>
            <div class="dropdown-item-desc">${escapeHtml(s.body.substring(0, 50))}...</div>
        </div>
    `).join('');
}

async function insertTemplateIntoComposer(templateId) {
    const tpl = MailState.templates.find(t => String(t.id) === String(templateId) || String(t.key) === String(templateId));
    if (!tpl) return;

    // Raccogli variabili note per risoluzione automatica
    let podVal = document.getElementById('composerLinkedPod').value.trim();
    if (!podVal && MailState.selectedMailData && MailState.selectedMailData.linked_pod) {
        podVal = MailState.selectedMailData.linked_pod;
        document.getElementById('composerLinkedPod').value = podVal;
    }
    
    const variables = {
        pod: podVal,
        data: new Date().toLocaleDateString('it-IT')
    };

    let subject = tpl.subject || '';
    let body = tpl.body || '';

    // Risolvi placeholders e condizionali via API centralizzata
    try {
        if (subject) {
            const resSubject = await fetch('/api/mail/resolve-template', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                body: JSON.stringify({ text: subject, variables: variables })
            });
            const dSub = await resSubject.json();
            if (dSub.success) subject = dSub.resolved;
        }

        if (body) {
            const resBody = await fetch('/api/mail/resolve-template', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                body: JSON.stringify({ text: body, variables: variables })
            });
            const dBody = await resBody.json();
            if (dBody.success) body = dBody.resolved;
        }
    } catch (e) {
        console.error('Errore risoluzione template:', e);
    }

    if (subject) document.getElementById('composerSubjectInput').value = subject;
    
    // Converti \n in <br> mantenendo eventuali tag HTML presenti
    const formattedBody = body.includes('<p>') || body.includes('<br>') ? body : body.replace(/\n/g, '<br>');
    document.getElementById('composerEditor').innerHTML = formattedBody;
    
    // Auto-allega documento standard se il template lo prevede
    if (tpl.key === 'email_semplificata' || tpl.key === 'email_societa' || tpl.key === 'email_comune_avvio' || tpl.key === 'email_polizia_municipale') {
        const stdDoc = MailState.standardAttachments.find(d => d.filename.toLowerCase().startsWith('attestazione'));
        if (stdDoc && !MailState.composer.attachments.some(a => a.filename === stdDoc.filename)) {
            MailState.composer.attachments.push({
                filename: stdDoc.filename,
                standard_name: stdDoc.standard_name,
                size_formatted: stdDoc.size_formatted
            });
            renderComposerAttachmentChips();
        }
    }

    document.getElementById('composerTemplatesMenu').style.display = 'none';
}

async function checkPodAutoFill(podVal) {
    if (!podVal || podVal.trim().length < 8) return;
    try {
        const res = await fetch('/api/mail/resolve-template', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ text: '{nome} ({indirizzo}, {comune})', variables: { pod: podVal.trim() } })
        });
        const d = await res.json();
        if (d.success && d.resolved && !d.resolved.includes('{nome}')) {
            const ind = document.getElementById('draftStatusIndicator');
            if (ind) ind.textContent = `Contatore collegato: ${d.resolved}`;
        }
    } catch (e) {
        console.error('Errore auto-fill POD:', e);
    }
}

function insertSnippetAtCursor(snippetId) {
    const snip = MailState.templates.find(s => String(s.id) === String(snippetId));
    if (!snip) return;

    const editor = document.getElementById('composerEditor');
    editor.focus();
    document.execCommand('insertHTML', false, snip.body.replace(/\n/g, '<br>'));
    document.getElementById('composerSnippetsMenu').style.display = 'none';
}

function insertQuickReplySnippet(snippetId) {
    const snip = MailState.templates.find(s => String(s.id) === String(snippetId));
    if (!snip) return;

    const input = document.getElementById('quickReplyInput');
    input.value += (input.value ? '\n\n' : '') + snip.body;
    input.focus();
    document.getElementById('quickReplySnippets').style.display = 'none';
}

// Formattazione Rich-Text
function execEditorCmd(cmd, val = null) {
    document.getElementById('composerEditor').focus();
    document.execCommand(cmd, false, val);
}

function insertLinkDialog() {
    const url = prompt("Inserisci l'indirizzo web (URL):", "https://");
    if (url) execEditorCmd('createLink', url);
}

// ============================================================================
// ALLEGATI NEL COMPOSER
// ============================================================================

function triggerFileUpload() {
    document.getElementById('composerFileInput').click();
}

async function handleAttachmentUpload(files) {
    if (!files || !files.length) return;

    for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append('file', files[i]);

        try {
            const res = await fetch('/api/mail/upload-attachment', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrfToken() },
                body: formData
            });
            const data = await res.json();
            if (data.success && data.attachment) {
                MailState.composer.attachments.push(data.attachment);
            }
        } catch (e) {
            console.error('Errore upload allegato:', e);
        }
    }
    renderComposerAttachmentChips();
}

function renderComposerAttachmentChips() {
    const bar = document.getElementById('composerAttachmentsBar');
    const container = document.getElementById('composerAttachedChips');
    if (!bar || !container) return;

    if (!MailState.composer.attachments.length) {
        bar.style.display = 'none';
        return;
    }

    bar.style.display = 'block';
    container.innerHTML = MailState.composer.attachments.map((att, idx) => `
        <span class="attached-chip">
            <i data-lucide="paperclip" style="width:12px;height:12px;"></i>
            ${escapeHtml(att.filename)} <small style="color:var(--text-muted);">(${att.size_formatted || ''})</small>
            <span class="attached-chip-remove" onclick="removeComposerAttachment(${idx})">&times;</span>
        </span>
    `).join('');
    lucide.createIcons();
}

function removeComposerAttachment(index) {
    MailState.composer.attachments.splice(index, 1);
    renderComposerAttachmentChips();
}

function openStandardAttachmentsSelector() {
    const modal = document.getElementById('standardAttachmentsModal');
    const list = document.getElementById('standardDocsList');
    modal.style.display = 'flex';

    list.innerHTML = MailState.standardAttachments.map(doc => `
        <div class="att-card" style="max-width:100%; margin-bottom:8px; cursor:pointer;" onclick="attachStandardDoc('${escapeHtml(doc.standard_name)}')">
            <div class="att-card-icon"><i data-lucide="file-check"></i></div>
            <div class="att-card-info">
                <div class="att-card-name">${escapeHtml(doc.description || doc.filename)}</div>
                <div class="att-card-size">${escapeHtml(doc.size_formatted)}</div>
            </div>
            <button type="button" class="btn-primary-sm" style="font-size:0.75rem;">Allega</button>
        </div>
    `).join('');
    lucide.createIcons();
}

function attachStandardDoc(standardName) {
    const doc = MailState.standardAttachments.find(d => d.standard_name === standardName);
    if (doc && !MailState.composer.attachments.some(a => a.standard_name === standardName)) {
        MailState.composer.attachments.push({
            filename: doc.filename,
            standard_name: doc.standard_name,
            size_formatted: doc.size_formatted
        });
        renderComposerAttachmentChips();
    }
    closeModal('standardAttachmentsModal');
}

// ============================================================================
// INVIO & PROGRAMMAZIONE
// ============================================================================

function toggleSchedulePopover() {
    const pop = document.getElementById('schedulePopover');
    pop.style.display = pop.style.display === 'block' ? 'none' : 'block';
}

function setSchedulePreset(preset) {
    const now = new Date();
    let target = new Date();

    if (preset === 'tomorrow_morning') {
        target.setDate(now.getDate() + 1);
        target.setHours(8, 30, 0, 0);
    } else if (preset === 'tomorrow_afternoon') {
        target.setDate(now.getDate() + 1);
        target.setHours(14, 0, 0, 0);
    } else if (preset === 'next_monday') {
        const day = now.getDay();
        const diff = now.getDate() + (day === 0 ? 1 : 8 - day);
        target.setDate(diff);
        target.setHours(9, 0, 0, 0);
    }

    const isoStr = target.toISOString();
    MailState.composer.scheduledAt = isoStr;
    document.getElementById('composerScheduledAt').value = isoStr;
    document.getElementById('sendBtnLabel').textContent = `Programma (${target.toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric', month: 'short' })} ${target.getHours()}:${String(target.getMinutes()).padStart(2, '0')})`;
    document.getElementById('schedulePopover').style.display = 'none';
}

function confirmCustomSchedule() {
    const val = document.getElementById('customScheduleDate').value;
    if (!val) {
        alert("Seleziona una data e ora valida.");
        return;
    }
    const dt = new Date(val);
    const isoStr = dt.toISOString();
    MailState.composer.scheduledAt = isoStr;
    document.getElementById('composerScheduledAt').value = isoStr;
    document.getElementById('sendBtnLabel').textContent = `Programma (${dt.toLocaleDateString('it-IT')} ${dt.getHours()}:${String(dt.getMinutes()).padStart(2, '0')})`;
    document.getElementById('schedulePopover').style.display = 'none';
}

async function submitMailSend() {
    const to = MailState.composer.to;
    if (!to.length) {
        alert("Inserisci almeno un destinatario nel campo 'A:'");
        document.getElementById('composerToInput').focus();
        return;
    }

    const subject = document.getElementById('composerSubjectInput').value.trim();
    const bodyHtml = document.getElementById('composerEditor').innerHTML;
    const categoryId = document.getElementById('composerCategorySelect').value;
    const linkedPod = document.getElementById('composerLinkedPod').value.trim();
    const addFiloneNote = document.getElementById('composerAddFiloneNote').checked;
    const scheduledAt = MailState.composer.scheduledAt;

    const payload = {
        to: to,
        cc: MailState.composer.cc,
        bcc: MailState.composer.bcc,
        subject: subject,
        body_html: bodyHtml,
        category_id: categoryId,
        linked_pod: linkedPod,
        add_filone_note: addFiloneNote,
        attachments: MailState.composer.attachments,
        scheduled_at: scheduledAt,
        draft_id: MailState.composer.draftId
    };

    const sendBtn = document.querySelector('.btn-send-main');
    sendBtn.disabled = true;
    sendBtn.innerHTML = '<i data-lucide="loader" class="spin"></i> Invio in corso...';
    lucide.createIcons();

    try {
        const res = await fetch('/api/mail/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('composerOverlay').style.display = 'none';
            alert(data.scheduled ? "Email programmata con successo!" : "Email inviata con successo!");
            loadMessages();
            loadMailStats();
        } else {
            alert(`Errore durante l'invio: ${data.error}`);
        }
    } catch (e) {
        alert(`Errore di connessione: ${e.message}`);
    } finally {
        sendBtn.disabled = false;
        sendBtn.innerHTML = '<i data-lucide="send"></i> <span id="sendBtnLabel">Invia Adesso</span>';
        lucide.createIcons();
    }
}

async function saveDraftManual(silent = false) {
    const payload = {
        to: MailState.composer.to,
        cc: MailState.composer.cc,
        bcc: MailState.composer.bcc,
        subject: document.getElementById('composerSubjectInput').value.trim(),
        body_html: document.getElementById('composerEditor').innerHTML,
        category_id: document.getElementById('composerCategorySelect').value,
        linked_pod: document.getElementById('composerLinkedPod').value.trim(),
        attachments: MailState.composer.attachments,
        draft_id: MailState.composer.draftId
    };

    try {
        const res = await fetch('/api/mail/save-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            MailState.composer.draftId = data.id;
            document.getElementById('composerDraftId').value = data.id;
            const ind = document.getElementById('draftStatusIndicator');
            ind.textContent = `Bozza salvata alle ${new Date().toLocaleTimeString()}`;
            if (!silent) alert("Bozza salvata!");
            loadMailStats();
        }
    } catch (e) {
        console.error('Errore salvataggio bozza:', e);
    }
}

function discardComposer() {
    if (confirm("Sei sicuro di voler eliminare questa bozza?")) {
        if (MailState.composer.draftId) {
            fetch(`/api/mail/messages/${MailState.composer.draftId}?permanent=1`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCsrfToken() }
            });
        }
        document.getElementById('composerOverlay').style.display = 'none';
        loadMessages();
        loadMailStats();
    }
}

// Quick Reply
async function sendQuickReply() {
    const msg = MailState.selectedMailData;
    if (!msg) return;

    const bodyText = document.getElementById('quickReplyInput').value.trim();
    if (!bodyText) {
        alert("Scrivi un messaggio di risposta.");
        return;
    }

    const payload = {
        to: [msg.from_addr],
        subject: msg.subject.toLowerCase().startsWith('re:') ? msg.subject : `Re: ${msg.subject}`,
        body_html: bodyText.replace(/\n/g, '<br>'),
        body_text: bodyText,
        category_id: msg.category_id,
        linked_pod: msg.linked_pod,
        add_filone_note: true
    };

    try {
        const res = await fetch('/api/mail/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('quickReplyInput').value = '';
            alert("Risposta inviata con successo!");
            loadMessages();
            loadMailStats();
        } else {
            alert(`Errore: ${data.error}`);
        }
    } catch (e) {
        alert(`Errore invio risposta: ${e.message}`);
    }
}

function openFullReplyComposer() {
    const val = document.getElementById('quickReplyInput').value;
    replyCurrentMail(false);
    if (val) {
        document.getElementById('composerEditor').innerHTML = val.replace(/\n/g, '<br>') + document.getElementById('composerEditor').innerHTML;
    }
}

// ============================================================================
// SINCRONIZZAZIONE IMAP
// ============================================================================

function toggleSyncDropdown(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const m = document.getElementById('syncDropdownMenu');
    if (!m) return;
    m.style.display = m.style.display === 'none' ? 'block' : 'none';
}

async function triggerSyncImap(limit = 200) {
    const dropdown = document.getElementById('syncDropdownMenu');
    if (dropdown) dropdown.style.display = 'none';

    const btn = document.getElementById('syncImapBtn');
    const spinner = document.getElementById('syncSpinner');
    const statusText = document.getElementById('syncStatusText');

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.add('spin');
    const isFull = (limit === 0);
    if (statusText) statusText.textContent = isFull ? 'Sincronizzazione di tutto lo storico IMAP in corso...' : 'Sincronizzazione IMAP in corso...';

    try {
        const res = await fetch(`/api/mail/sync-imap?limit=${limit}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() }
        });
        const data = await res.json();
        if (data.success) {
            const tot = data.total_inbox ? ` (su ${data.total_inbox} totali in casella)` : '';
            const msg = `Sincronizzazione completata: ${data.new_messages} nuove email importate${tot}`;
            if (statusText) statusText.textContent = msg;
            showToast(msg);
            await loadMessages();
            await loadMailStats();
        } else {
            const errMsg = data.error || 'Errore sincronizzazione IMAP';
            if (statusText) statusText.textContent = `Errore IMAP: ${errMsg}`;
            showToast(errMsg, true);
        }
    } catch (e) {
        console.error("Errore fetch IMAP:", e);
        const errMsg = e.message || 'Errore di connessione al server';
        if (statusText) statusText.textContent = `Errore di connessione IMAP (${errMsg})`;
        showToast(`Errore connessione IMAP: ${errMsg}`, true);
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.classList.remove('spin');
    }
}

// ============================================================================
// GESTIONE MODALI CATEGORIE & TEMPLATE
// ============================================================================

function openModal(modalId) {
    document.getElementById(modalId).style.display = 'flex';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
}

function openCategoriesModal() {
    MailState.editingCatId = null;
    openModal('categoriesModal');
    renderCategoriesManageList();
}

function renderCategoriesManageList() {
    const container = document.getElementById('categoriesManageList');
    if (!container) return;

    if (!MailState.categories || MailState.categories.length === 0) {
        container.innerHTML = `<div class="empty-list-hint">Nessuna categoria configurata.</div>`;
        return;
    }

    container.innerHTML = MailState.categories.map(c => {
        const isEditing = MailState.editingCatId === c.id;
        const kwList = Array.isArray(c.keywords) ? c.keywords : [];

        if (isEditing) {
            return `
                <div class="category-edit-card" id="editCard-${c.id}">
                    <div class="edit-card-header">
                        <span class="edit-card-title"><i data-lucide="edit-3"></i> Modifica: <strong>${escapeHtml(c.name)}</strong></span>
                        <span class="badge-cat-type ${c.is_system ? 'is-sys' : 'is-custom'}">${c.is_system ? 'Sistema' : 'Personalizzata'}</span>
                    </div>
                    <div class="edit-card-body">
                        <div class="edit-form-row">
                            <div class="edit-field flex-grow">
                                <label>Nome Categoria</label>
                                <input type="text" id="editCatName-${c.id}" value="${escapeHtml(c.name)}" class="cat-input">
                            </div>
                            <div class="edit-field">
                                <label>Colore</label>
                                <div class="color-picker-wrapper">
                                    <div class="color-swatches-row">
                                        <button type="button" class="color-swatch-btn ${c.color === '#3b82f6' ? 'active' : ''}" style="background:#3b82f6;" onclick="selectEditCatColor('${c.id}', '#3b82f6', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#10b981' ? 'active' : ''}" style="background:#10b981;" onclick="selectEditCatColor('${c.id}', '#10b981', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#f59e0b' ? 'active' : ''}" style="background:#f59e0b;" onclick="selectEditCatColor('${c.id}', '#f59e0b', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#8b5cf6' ? 'active' : ''}" style="background:#8b5cf6;" onclick="selectEditCatColor('${c.id}', '#8b5cf6', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#ef4444' ? 'active' : ''}" style="background:#ef4444;" onclick="selectEditCatColor('${c.id}', '#ef4444', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#06b6d4' ? 'active' : ''}" style="background:#06b6d4;" onclick="selectEditCatColor('${c.id}', '#06b6d4', this)"></button>
                                        <button type="button" class="color-swatch-btn ${c.color === '#ec4899' ? 'active' : ''}" style="background:#ec4899;" onclick="selectEditCatColor('${c.id}', '#ec4899', this)"></button>
                                    </div>
                                    <input type="color" id="editCatColor-${c.id}" value="${c.color || '#3b82f6'}" class="custom-color-input" onchange="syncEditCatColorPicker('${c.id}', this.value)">
                                </div>
                            </div>
                        </div>
                        <div class="edit-field" style="margin-top:10px;">
                            <label>Keywords / Parole Chiave (separate da virgola)</label>
                            <input type="text" id="editCatKeywords-${c.id}" value="${escapeHtml(kwList.join(', '))}" class="cat-input" placeholder="es: appuntamento, cliente, spostamento, urgenza">
                            <span class="field-hint">Tutte le email contenenti queste parole verranno categorizzate automaticamente.</span>
                        </div>
                        <div class="edit-card-actions">
                            <button type="button" class="btn-save-cat-edit" onclick="saveEditedCategory('${c.id}')">
                                <i data-lucide="check"></i> Salva Modifiche
                            </button>
                            <button type="button" class="btn-cancel-cat-edit" onclick="cancelEditCategory()">
                                Annulla
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        const kwPills = kwList.length > 0 
            ? kwList.map(k => `<span class="kw-pill">${escapeHtml(k)}</span>`).join('') 
            : `<span class="no-kw-hint">Nessuna keyword assegnata</span>`;

        return `
            <div class="category-item-card">
                <div class="cat-item-top">
                    <div class="cat-item-info">
                        <span class="cat-dot-lg" style="background:${c.color || '#3b82f6'};"></span>
                        <strong class="cat-item-name">${escapeHtml(c.name)}</strong>
                        <span class="badge-cat-type ${c.is_system ? 'is-sys' : 'is-custom'}">${c.is_system ? 'Sistema' : 'Personalizzata'}</span>
                    </div>
                    <div class="cat-item-actions">
                        <button type="button" class="btn-cat-action" onclick="startEditCategory('${c.id}')" title="Modifica keywords e colore">
                            <i data-lucide="edit-3"></i> <span>Modifica</span>
                        </button>
                        ${!c.is_system ? `
                            <button type="button" class="btn-cat-action danger" onclick="deleteCategory('${c.id}')" title="Elimina questa categoria">
                                <i data-lucide="trash-2"></i>
                            </button>
                        ` : ''}
                    </div>
                </div>
                <div class="cat-item-keywords-box">
                    <div class="cat-kw-label"><i data-lucide="key" style="width:11px; height:11px;"></i> Keywords attive:</div>
                    <div class="cat-kw-pills-list">${kwPills}</div>
                </div>
            </div>
        `;
    }).join('');

    lucide.createIcons();
}

function startEditCategory(catId) {
    MailState.editingCatId = catId;
    renderCategoriesManageList();
}

function cancelEditCategory() {
    MailState.editingCatId = null;
    renderCategoriesManageList();
}

function selectNewCatColor(color, btn) {
    const input = document.getElementById('newCatColor');
    if (input) input.value = color;
    if (btn) {
        document.querySelectorAll('#newCatSwatches .color-swatch-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
}

function syncNewCatColorPicker(val) {
    document.querySelectorAll('#newCatSwatches .color-swatch-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.color.toLowerCase() === val.toLowerCase());
    });
}

function selectEditCatColor(catId, color, btn) {
    const input = document.getElementById(`editCatColor-${catId}`);
    if (input) input.value = color;
    if (btn && btn.parentElement) {
        btn.parentElement.querySelectorAll('.color-swatch-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
}

function syncEditCatColorPicker(catId, val) {
    const card = document.getElementById(`editCard-${catId}`);
    if (card) {
        card.querySelectorAll('.color-swatches-row .color-swatch-btn').forEach(b => {
            b.classList.toggle('active', b.style.background.toLowerCase() === val.toLowerCase());
        });
    }
}

async function saveEditedCategory(catId) {
    const nameInput = document.getElementById(`editCatName-${catId}`);
    const colorInput = document.getElementById(`editCatColor-${catId}`);
    const kwInput = document.getElementById(`editCatKeywords-${catId}`);

    const name = nameInput ? nameInput.value.trim() : '';
    const color = colorInput ? colorInput.value : '#3b82f6';
    const kw = kwInput ? kwInput.value.trim() : '';

    if (!name) {
        showToast("Il nome della categoria è obbligatorio.", true);
        return;
    }

    try {
        const res = await fetch('/api/mail/categories', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: catId, name: name, color: color, keywords: kw })
        });
        const data = await res.json();
        if (data.success) {
            MailState.editingCatId = null;
            await loadCategories();
            renderCategoriesManageList();
            showToast(`Categoria "${name}" e keywords aggiornate con successo!`);
        } else {
            showToast(`Errore: ${data.error}`, true);
        }
    } catch (e) {
        showToast("Errore durante il salvataggio: " + e.message, true);
    }
}

async function saveNewCategory() {
    const nameEl = document.getElementById('newCatName');
    const colorEl = document.getElementById('newCatColor');
    const kwEl = document.getElementById('newCatKeywords');

    const name = nameEl ? nameEl.value.trim() : '';
    const color = colorEl ? colorEl.value : '#3b82f6';
    const kw = kwEl ? kwEl.value.trim() : '';

    if (!name) {
        showToast("Inserisci il nome della nuova categoria.", true);
        return;
    }

    try {
        const res = await fetch('/api/mail/categories', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ name: name, color: color, keywords: kw })
        });
        const data = await res.json();
        if (data.success) {
            if (nameEl) nameEl.value = '';
            if (kwEl) kwEl.value = '';
            await loadCategories();
            renderCategoriesManageList();
            showToast(`Nuova categoria "${name}" creata con successo!`);
        } else {
            showToast(`Errore creazione: ${data.error}`, true);
        }
    } catch (e) {
        showToast("Errore: " + e.message, true);
    }
}

async function deleteCategory(catId) {
    if (!confirm("Sei sicuro di voler eliminare questa categoria personalizzata?")) return;
    try {
        const res = await fetch(`/api/mail/categories/${catId}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCsrfToken() }
        });
        const data = await res.json();
        if (data.success) {
            await loadCategories();
            renderCategoriesManageList();
            showToast("Categoria eliminata.");
        } else {
            showToast(`Errore: ${data.error}`, true);
        }
    } catch (e) {
        showToast("Errore eliminazione: " + e.message, true);
    }
}

function openTemplatesModal() {
    openModal('templatesModal');
    switchTplTab('templates');
}

function switchTplTab(tab) {
    document.querySelectorAll('.tpl-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    const isSettings = tab === 'settings';
    document.getElementById('tplManageSection').style.display = isSettings ? 'none' : 'block';
    document.getElementById('tplSettingsSection').style.display = isSettings ? 'block' : 'none';
    
    if (isSettings) {
        loadGlobalTemplateSettings();
    } else {
        renderTemplatesManageList(tab);
    }
}

function renderTemplatesManageList(type) {
    const container = document.getElementById('templatesManageList');
    const isTpl = type === 'templates';
    const items = MailState.templates.filter(t => t.type === (isTpl ? 'template' : 'snippet'));

    document.getElementById('manageTplType').value = isTpl ? 'template' : 'snippet';
    document.getElementById('manageTplSubjectRow').style.display = isTpl ? 'block' : 'none';

    if (!items.length) {
        container.innerHTML = '<div style="padding:16px; color:var(--text-muted); text-align:center;">Nessun elemento trovato in questa sezione.</div>';
        return;
    }

    container.innerHTML = items.map(t => `
        <div class="tpl-item-manage" onclick="editTemplateInModal('${t.id}')">
            <div class="tpl-item-info">
                <div class="tpl-item-title">${isTpl ? '🪄' : '💬'} ${escapeHtml(t.title)}</div>
                <div class="tpl-item-sub">${escapeHtml(t.subject || t.body.substring(0, 90))}...</div>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
                <button type="button" class="btn-tool-sm" title="Modifica modello" onclick="event.stopPropagation(); editTemplateInModal('${t.id}')">
                    <i data-lucide="edit-3"></i> Modifica
                </button>
                ${!t.is_system ? `<button type="button" class="btn-tool-sm" onclick="event.stopPropagation(); deleteTemplate('${t.id}')" style="color:#ef4444;" title="Elimina"><i data-lucide="trash-2"></i></button>` : '<span style="font-size:0.7rem; padding:2px 6px; border-radius:4px; background:rgba(59,130,246,0.1); color:var(--accent);">Sistema</span>'}
            </div>
        </div>
    `).join('');
    lucide.createIcons();
}

function editTemplateInModal(id) {
    const item = MailState.templates.find(t => String(t.id) === String(id));
    if (!item) return;

    document.getElementById('tplFormHeading').textContent = `Modifica: ${item.title}`;
    document.getElementById('manageTplId').value = item.id;
    document.getElementById('manageTplTitle').value = item.title;
    document.getElementById('manageTplType').value = item.type;
    document.getElementById('manageTplSubject').value = item.subject || '';
    document.getElementById('manageTplBody').value = item.body || '';
    document.getElementById('manageTplSubjectRow').style.display = item.type === 'template' ? 'block' : 'none';
    document.getElementById('addTplBox').scrollIntoView({ behavior: 'smooth' });
}

async function saveTemplateFromModal() {
    const id = document.getElementById('manageTplId').value;
    const title = document.getElementById('manageTplTitle').value.trim();
    const type = document.getElementById('manageTplType').value;
    const subject = document.getElementById('manageTplSubject').value.trim();
    const body = document.getElementById('manageTplBody').value.trim();
    if (!title || !body) {
        alert("Titolo e testo del modello sono obbligatori.");
        return;
    }

    try {
        await fetch('/api/mail/templates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify({ id: id || undefined, title: title, type: type, subject: subject, body: body })
        });
        resetTplForm();
        await loadTemplates();
        renderTemplatesManageList(type === 'template' ? 'templates' : 'snippets');
        populateComposerTemplatesMenu();
        populateComposerSnippetsMenu();
        populateQuickReplySnippets();
    } catch (e) {
        alert("Errore salvataggio template: " + e.message);
    }
}

function resetTplForm() {
    document.getElementById('tplFormHeading').textContent = 'Crea Nuovo Modello';
    document.getElementById('manageTplId').value = '';
    document.getElementById('manageTplTitle').value = '';
    document.getElementById('manageTplSubject').value = '';
    document.getElementById('manageTplBody').value = '';
}

async function deleteTemplate(itemId) {
    if (confirm("Eliminare questo modello personalizzato?")) {
        await fetch(`/api/mail/templates/${itemId}`, {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCsrfToken() }
        });
        await loadTemplates();
        renderTemplatesManageList('templates');
        populateComposerTemplatesMenu();
        populateComposerSnippetsMenu();
        populateQuickReplySnippets();
    }
}

// FIRMA & RECAPITI
async function loadGlobalTemplateSettings() {
    try {
        const res = await fetch('/api/mail/template-settings');
        const data = await res.json();
        if (data.success && data.settings) {
            const s = data.settings;
            document.getElementById('settingEmailTorino').value = s.email_torino || '';
            document.getElementById('settingEmailPavia').value = s.email_pavia || '';
            document.getElementById('settingEmailLecco').value = s.email_lecco || '';
            document.getElementById('settingFirmaHtml').value = s.firma_html || '';
            updateSignaturePreview();
        }
    } catch (e) {
        console.error('Errore caricamento impostazioni template:', e);
    }
}

function updateSignaturePreview() {
    const raw = document.getElementById('settingFirmaHtml')?.value || '';
    const emailTec = document.getElementById('settingEmailTorino')?.value || 'tecnico@algecopower.it';
    const resolved = raw.replace(/{email_tecnico}/g, emailTec);
    const box = document.getElementById('signaturePreviewBox');
    if (box) box.innerHTML = resolved || '<em>Nessuna firma impostata</em>';
}

document.addEventListener('input', (e) => {
    if (e.target && e.target.id === 'settingFirmaHtml') {
        updateSignaturePreview();
    }
});

async function saveGlobalTemplateSettings() {
    const payload = {
        email_torino: document.getElementById('settingEmailTorino').value.trim(),
        email_pavia: document.getElementById('settingEmailPavia').value.trim(),
        email_lecco: document.getElementById('settingEmailLecco').value.trim(),
        firma_html: document.getElementById('settingFirmaHtml').value.trim()
    };
    try {
        const res = await fetch('/api/mail/template-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(payload)
        });
        const d = await res.json();
        if (d.success) {
            alert("Impostazioni firma e recapiti salvate con successo!");
            updateSignaturePreview();
        }
    } catch (e) {
        alert("Errore salvataggio impostazioni: " + e.message);
    }
}

// Utility
function showToast(msg, isError = false) {
    let t = document.getElementById('toastNotification');
    if (!t) {
        t = document.createElement('div');
        t.id = 'toastNotification';
        t.className = 'toast-notification';
        document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.borderColor = isError ? 'var(--danger, #ef4444)' : 'var(--accent, #3b82f6)';
    t.classList.add('show');
    if (window._mailToastTimer) clearTimeout(window._mailToastTimer);
    window._mailToastTimer = setTimeout(() => {
        t.classList.remove('show');
    }, 3200);
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

// ============================================================================
// GESTIONE TEMA CHIARO / SCURO
// ============================================================================

function setSharedCookie(name, value) {
    const maxAge = 60 * 60 * 24 * 365;
    const secure = location.protocol === 'https:' ? '; Secure' : '';
    const host = location.hostname;
    let domain = '';

    if (host.includes('.') && !/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
        const parts = host.split('.');
        domain = `; domain=.${parts.slice(-2).join('.')}`;
    }

    document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${maxAge}; SameSite=Lax${secure}${domain}`;
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);
    setSharedCookie('algeco_theme', theme);
    try {
        localStorage.setItem('algeco_theme', theme);
    } catch (_) {}
    if (typeof window.refreshLucideIcons === 'function') {
        window.refreshLucideIcons();
    } else if (typeof lucide !== 'undefined' && lucide.createIcons) {
        lucide.createIcons();
    }
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || document.body.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    applyTheme(newTheme);
    showToast(`Tema ${newTheme === 'dark' ? 'Scuro' : 'Chiaro'} attivato`);
}

// Chiudi dropdown al click fuori
window.addEventListener('click', (e) => {
    if (!e.target.closest('.dropdown-wrap') && !e.target.closest('.split-btn-wrap') && !e.target.closest('.sync-btn-group') && !e.target.closest('.schedule-popover')) {
        document.querySelectorAll('.templates-dropdown-menu, .snippets-dropdown-menu, .schedule-popover, .sync-dropdown-menu').forEach(m => m.style.display = 'none');
    }
    if (!e.target.closest('.composer-field-row')) {
        const m = document.getElementById('contactsAutocompleteMenu');
        if (m) m.style.display = 'none';
    }
});
