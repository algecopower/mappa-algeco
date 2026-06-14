// -*- coding: utf-8 -*-
let generazioni = [];
let aziende = [];
let selectedGenerazione = '';
let templates = {};
let allegatiInfo = {};
let bulkItems = [];
let bulkAbort = false;
let aziendeController = null;
let researchContactKey = '';
let filterIncompleteOnly = false;

const PAUSA_TRA_EMAIL_SEC = 4;
const RESEARCH_AUTO_OPEN_KEY = 'lettere_research_auto_open';

function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

function showToast(msg, error) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.borderColor = error ? 'var(--danger)' : 'var(--border)';
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2600);
}

function fmtTs(ts) {
    if (!ts) return '-';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function periodoLabel(g) {
    if (!g) return '';
    return `${g.periodo_da || '?'} → ${g.periodo_a || '?'}`;
}

function isPec(email) {
    if (!email) return false;
    const e = email.toLowerCase().trim();
    return /(@pec\.|@pec-|\.pec@|@legalmail\.|\.legalmail\.|@cert\.|@postacert\.|@cgn\.legalmail|@arubapec\.|@pcert\.|@sicurezzapostale\.|@certifiposta\.|@interfreepec\.)/.test(e)
        || e.endsWith('.pec.it')
        || e.endsWith('@pec.it');
}

function splitEmails(campo) {
    if (!campo) return [];
    const seen = new Set();
    const out = [];
    for (const part of String(campo).split(/[\/,;\s]+/)) {
        const email = part.trim();
        if (!email.includes('@')) continue;
        const key = email.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ email, pec: isPec(email) });
    }
    return out;
}

async function loadAll() {
    await Promise.all([loadGenerazioni(), loadTemplates(), loadAllegati(), loadStats()]);
    await loadAziende();
}

async function loadGenerazioni() {
    const res = await fetch('/api/lettere/generazioni');
    const data = await res.json();
    if (!data.success) throw new Error(data.error || 'Errore generazioni');
    generazioni = data.generazioni || [];
    renderGenerazioni();
}

async function loadTemplates() {
    const res = await fetch('/api/distinte/templates');
    const data = await res.json();
    templates = data.templates || {};
    const select = document.getElementById('templateSelect');
    select.innerHTML = '';
    for (const [id, tmpl] of Object.entries(templates)) {
        if (tmpl.type !== 'email') continue;
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = tmpl.label || id;
        select.appendChild(opt);
    }
}

async function loadAllegati() {
    const res = await fetch('/api/lettere/allegati');
    allegatiInfo = await res.json();
}

async function loadStats() {
    const res = await fetch('/api/lettere/statistiche');
    const data = await res.json();
    const s = data.lettere || {};
    document.getElementById('statsBox').innerHTML = `
        <div class="kpi"><span>${s.generazioni || 0}</span><small>Generazioni</small></div>
        <div class="kpi"><span>${s.aziende || 0}</span><small>Aziende</small></div>
        <div class="kpi"><span>${s.copertura_contatti || 0}%</span><small>Con email/PEC</small></div>
        <div class="kpi"><span>${s.tasso_feedback || 0}%</span><small>Feedback</small></div>
    `;
}

function renderGenerazioni() {
    const list = document.getElementById('generazioniList');
    if (!generazioni.length) {
        list.innerHTML = '<div class="empty">Nessuna generazione archiviata</div>';
        return;
    }
    list.innerHTML = generazioni.map(g => `
        <div class="generation-item ${String(selectedGenerazione) === String(g.id) ? 'active' : ''}"
             role="button" tabindex="0"
             onclick="selectGenerazione('${g.id}')"
             onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectGenerazione('${g.id}'); }">
            <strong>${esc(periodoLabel(g))}</strong>
            <span>${esc(g.file_name)} · ${g.totale_aziende || 0} aziende</span>
            <span>
                ${fmtTs(g.created_ts)}
                <a href="/api/lettere/generazioni/${g.id}/download" onclick="event.stopPropagation()">Scarica</a>
            </span>
        </div>
    `).join('');
    document.getElementById('genAllBtn').classList.toggle('active', selectedGenerazione === '');
}

async function selectGenerazione(id) {
    selectedGenerazione = id || '';
    renderGenerazioni();
    await loadAziende();
}

async function loadAziende() {
    if (aziendeController) aziendeController.abort();
    aziendeController = new AbortController();
    const body = document.getElementById('aziendeBody');
    body.innerHTML = '<tr><td colspan="6" class="loading"><i class="fas fa-spinner fa-spin"></i> Caricamento...</td></tr>';
    const q = document.getElementById('searchInput').value.trim();
    const params = new URLSearchParams();
    if (selectedGenerazione) params.set('generazione_id', selectedGenerazione);
    if (q) params.set('q', q);
    params.set('limit', '5000');
    try {
        const res = await fetch(`/api/lettere/aziende?${params.toString()}`, {
            signal: aziendeController.signal
        });
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || 'Errore caricamento aziende', true);
            return;
        }
        aziende = data.aziende || [];
        renderAziende();
        updateBulkButton();
    } catch (e) {
        if (e.name !== 'AbortError') {
            showToast(e.message || 'Errore caricamento aziende', true);
        }
    }
}

function renderEmailBadges(emailField) {
    const emails = splitEmails(emailField);
    if (!emails.length) return '<div class="mini">Nessun indirizzo</div>';
    return `<div class="badges">${emails.map(e =>
        `<span class="badge ${e.pec ? 'pec' : ''}">${e.pec ? 'PEC' : 'MAIL'}</span>`
    ).join('')}</div>`;
}

function hasMissingContact(a) {
    return !(a.telefono || '').trim() || splitEmails(a.email).length === 0;
}

function researchQueue() {
    const seen = new Set();
    return aziende.filter(a => {
        if (!hasMissingContact(a) || seen.has(a.contact_key)) return false;
        seen.add(a.contact_key);
        return true;
    });
}

function googleSearchUrl(query) {
    return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
}

function buildSearchTerms(a) {
    const nome = (a.nominativo || '').trim();
    const comune = (a.comune || '').trim();
    const nomeBreve = nome.replace(
        /\s+(S\.?R\.?L\.?S?\.?|S\.?P\.?A\.?|S\.?A\.?S\.?|S\.?N\.?C\.?|S\.?C\.?A\.?R\.?L\.?|S\.?D\.?F\.?)$/i,
        ''
    ).trim();
    const loose = [nome, comune].filter(Boolean).join(' ');
    const looseBreve = [nomeBreve || nome, comune].filter(Boolean).join(' ');
    const focused = [nome.includes(' ') ? `"${nome}"` : nome, comune].filter(Boolean).join(' ');
    return { nome, comune, loose, looseBreve, focused };
}

function buildResearchGroups(a) {
    const { nome, comune, loose, looseBreve, focused } = buildSearchTerms(a);
    return [
        {
            title: 'Google',
            links: [
                { label: 'Tutti i contatti', icon: 'fa-magnifying-glass', url: googleSearchUrl(`${loose} contatti telefono email`) },
                { label: 'Ricerca ampia', icon: 'fa-binoculars', url: googleSearchUrl(`${looseBreve} contatti`) },
                { label: 'Telefono', icon: 'fa-phone', url: googleSearchUrl(`${loose} telefono`) },
                { label: 'Email', icon: 'fa-envelope', url: googleSearchUrl(`${loose} email`) },
                { label: 'PEC', icon: 'fa-at', url: googleSearchUrl(`${looseBreve} PEC`) },
                { label: 'Sito ufficiale', icon: 'fa-globe', url: googleSearchUrl(`${loose} sito ufficiale`) },
            ]
        },
        {
            title: 'Mappe e social',
            links: [
                {
                    label: 'Google Maps',
                    icon: 'fa-map-location-dot',
                    url: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(loose)}`
                },
                { label: 'LinkedIn', icon: 'fa-linkedin', brand: true, url: googleSearchUrl(`site:linkedin.com ${looseBreve}`) },
                { label: 'Facebook', icon: 'fa-facebook', brand: true, url: googleSearchUrl(`site:facebook.com ${looseBreve}`) },
                { label: 'Instagram', icon: 'fa-instagram', brand: true, url: googleSearchUrl(`site:instagram.com ${looseBreve}`) },
            ]
        },
        {
            title: 'Fonti ufficiali e elenchi',
            links: [
                {
                    label: 'INI-PEC',
                    icon: 'fa-shield-halved',
                    url: googleSearchUrl(`site:inipec.gov.it ${looseBreve}`)
                },
                { label: 'Registro Imprese', icon: 'fa-landmark', url: googleSearchUrl(`site:registroimprese.it ${looseBreve}`) },
                {
                    label: 'Ufficio Camerale',
                    icon: 'fa-building',
                    url: googleSearchUrl(`site:ufficiocamerale.it ${looseBreve}`)
                },
                { label: 'Pagine Gialle', icon: 'fa-address-book', url: googleSearchUrl(`site:paginegialle.it ${looseBreve}`) },
                { label: 'Pagine Bianche', icon: 'fa-book-open', url: googleSearchUrl(`site:paginebianche.it ${looseBreve}`) },
            ]
        }
    ];
}

function currentResearchContact() {
    return aziende.find(a => a.contact_key === researchContactKey);
}

function openContactResearch(id, autoOpen) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    researchContactKey = row.contact_key;
    renderContactResearch();
    document.getElementById('researchModal').classList.add('active');
    if (autoOpen || autoOpenResearchesEnabled()) {
        openPrimaryResearches();
    }
}

function closeContactResearch() {
    document.getElementById('researchModal').classList.remove('active');
    researchContactKey = '';
}

function renderContactResearch() {
    const row = currentResearchContact();
    if (!row) {
        closeContactResearch();
        return;
    }
    const queue = researchQueue();
    const queueIndex = queue.findIndex(a => a.contact_key === row.contact_key);
    const missing = [
        !(row.telefono || '').trim() ? 'telefono' : '',
        splitEmails(row.email).length === 0 ? 'email/PEC' : ''
    ].filter(Boolean);

    document.getElementById('researchCompanyName').textContent = row.nominativo || 'Azienda';
    document.getElementById('researchCompanyMeta').textContent =
        [row.comune, row.pod, missing.length ? `Manca: ${missing.join(', ')}` : 'Recapiti completi']
            .filter(Boolean)
            .join(' · ');
    document.getElementById('researchProgress').textContent = queue.length
        ? `${queueIndex >= 0 ? queueIndex + 1 : '-'} di ${queue.length} contatti incompleti nella lista corrente`
        : 'Nessun altro contatto incompleto nella lista corrente';
    document.getElementById('researchPhone').value = row.telefono || '';
    document.getElementById('researchEmail').value = row.email || '';
    document.getElementById('researchGroups').innerHTML = buildResearchGroups(row).map(group => `
        <section class="research-group">
            <h3>${esc(group.title)}</h3>
            <div class="research-links">
                ${group.links.map(link => `
                    <a class="research-link" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">
                        <i class="${link.brand ? 'fa-brands' : 'fas'} ${link.icon}"></i>
                        <span>${esc(link.label)}</span>
                        <i class="fas fa-arrow-up-right-from-square"></i>
                    </a>
                `).join('')}
            </div>
        </section>
    `).join('');
}

function autoOpenResearchesEnabled() {
    const el = document.getElementById('researchAutoOpen');
    return !el || el.checked;
}

function getPrimaryResearchLinks(row) {
    const googleLinks = buildResearchGroups(row)[0].links;
    const missingPhone = !(row.telefono || '').trim();
    const missingEmail = splitEmails(row.email).length === 0;
    const links = [];
    if (missingPhone && missingEmail) {
        links.push(googleLinks[0]);
    } else if (missingPhone || missingEmail) {
        links.push(googleLinks[1]);
    }
    if (missingPhone) {
        links.push(googleLinks[2]);
    }
    if (missingEmail) {
        links.push(googleLinks[3], googleLinks[4]);
    }
    return links.length ? links : [googleLinks[0], googleLinks[1]];
}

function openPrimaryResearchesFor(row) {
    if (!row) return;
    const links = getPrimaryResearchLinks(row);
    for (const link of links) {
        window.open(link.url, '_blank', 'noopener,noreferrer');
    }
    const labels = links.map(link => link.label.toLowerCase()).join(', ');
    showToast(`Aperte ${links.length} ricerche: ${labels}`);
}

function getExternalSourceLinks(row) {
    const groups = buildResearchGroups(row);
    const pick = (title, label) => (
        groups.find(group => group.title === title)?.links.find(link => link.label === label)
    );
    return [
        pick('Mappe e social', 'Google Maps'),
        pick('Fonti ufficiali e elenchi', 'Pagine Gialle'),
        pick('Fonti ufficiali e elenchi', 'Ufficio Camerale'),
        pick('Fonti ufficiali e elenchi', 'INI-PEC'),
        pick('Fonti ufficiali e elenchi', 'Registro Imprese'),
    ].filter(Boolean);
}

function openExternalSources() {
    const row = currentResearchContact();
    if (!row) return;
    const links = getExternalSourceLinks(row);
    for (const link of links) {
        window.open(link.url, '_blank', 'noopener,noreferrer');
    }
    const labels = links.map(link => link.label.toLowerCase()).join(', ');
    showToast(`Aperte ${links.length} fonti esterne: ${labels}`);
}

function openPrimaryResearches() {
    openPrimaryResearchesFor(currentResearchContact());
}

function startContactResearchQueue() {
    const queue = researchQueue();
    if (!queue.length) {
        showToast('Nessun contatto incompleto nella lista corrente');
        return;
    }
    openContactResearch(queue[0].id, true);
}

function navigateResearch(delta) {
    const queue = researchQueue();
    const row = currentResearchContact();
    if (!row || !queue.length) return;
    const currentIndex = queue.findIndex(a => a.contact_key === row.contact_key);
    if (currentIndex < 0) return;
    const next = queue[(currentIndex + delta + queue.length) % queue.length];
    researchContactKey = next.contact_key;
    renderContactResearch();
    if (autoOpenResearchesEnabled()) {
        openPrimaryResearches();
    }
}

async function saveResearchContact(openNext) {
    const row = currentResearchContact();
    if (!row) return;
    const queue = researchQueue();
    const currentIndex = queue.findIndex(a => a.contact_key === row.contact_key);
    const nextKey = currentIndex >= 0 && queue.length > 1
        ? queue[(currentIndex + 1) % queue.length].contact_key
        : '';
    const nextRow = nextKey
        ? queue.find(a => a.contact_key === nextKey)
        : null;
    const telefono = document.getElementById('researchPhone').value.trim();
    const email = document.getElementById('researchEmail').value.trim();

    // Apri subito le ricerche del prossimo contatto, mentre il click utente
    // è ancora valido per i popup del browser.
    if (openNext && nextRow && nextKey !== row.contact_key && autoOpenResearchesEnabled()) {
        openPrimaryResearchesFor(nextRow);
    }

    try {
        const res = await fetch(`/api/lettere/contatti/${encodeURIComponent(row.contact_key)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ telefono, email })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore salvataggio');

        for (const item of aziende.filter(a => a.contact_key === row.contact_key)) {
            item.telefono = telefono;
            item.email = email;
        }
        renderAziende();
        updateBulkButton();
        loadStats();
        showToast(data.contatto?.sync_filone?.synced ? 'Salvato anche sul filone' : 'Contatto salvato');

        if (openNext && nextKey && nextKey !== row.contact_key) {
            researchContactKey = nextKey;
            renderContactResearch();
        } else if (openNext) {
            closeContactResearch();
            showToast('Nessun altro contatto incompleto nella lista corrente');
        } else {
            renderContactResearch();
        }
    } catch (e) {
        showToast(e.message, true);
    }
}

function visibleAziende() {
    if (!filterIncompleteOnly) return aziende;
    return aziende.filter(hasMissingContact);
}

function toggleIncompleteFilter(checked) {
    filterIncompleteOnly = Boolean(checked);
    renderAziende();
}

function renderAziende() {
    const visible = visibleAziende();
    const incompleteCount = aziende.filter(hasMissingContact).length;
    let info = `${visible.length} aziende`;
    if (filterIncompleteOnly) {
        info += ' senza recapiti completi';
    } else if (incompleteCount) {
        info += ` · ${incompleteCount} da completare`;
    }
    if (selectedGenerazione) {
        info += ' nella generazione selezionata';
    }
    document.getElementById('resultInfo').textContent = info;
    const body = document.getElementById('aziendeBody');
    if (!visible.length) {
        body.innerHTML = filterIncompleteOnly
            ? '<tr><td colspan="6" class="empty">Nessuna azienda senza recapiti completi</td></tr>'
            : '<tr><td colspan="6" class="empty">Nessuna azienda trovata</td></tr>';
        return;
    }
    body.innerHTML = visible.map(a => `
        <tr data-id="${a.id}">
            <td class="select-cell" data-label="Seleziona"><input type="checkbox" class="row-check" data-id="${a.id}" onchange="updateBulkButton()"></td>
            <td data-label="Azienda">
                <div class="azienda-title">${esc(a.nominativo)}</div>
                <div class="azienda-meta">${esc(a.comune || '')} · ${esc(a.pod || 'POD mancante')}</div>
                <div class="mini file-meta">${esc(a.file_name || '')}</div>
                <button class="research-trigger ${hasMissingContact(a) ? 'needs-contact' : ''}"
                        onclick="openContactResearch(${a.id})">
                    <i class="fas fa-magnifying-glass-location"></i>
                    ${hasMissingContact(a) ? 'Cerca recapiti' : 'Verifica recapiti'}
                </button>
            </td>
            <td data-label="Intervento">
                <strong>${esc(a.data_pianificazione || '-')}</strong>
                <div class="mini">${esc(a.ora_pianificazione || '')}</div>
                <div class="mini">${esc(a.operatore || '')}</div>
            </td>
            <td data-label="Telefono">
                <input class="edit-input" value="${esc(a.telefono || '')}"
                       placeholder="Telefono"
                       onkeydown="saveOnEnter(event)"
                       onblur="saveContact('${esc(a.contact_key)}', this, null)">
            </td>
            <td class="email-cell" data-label="Email / PEC">
                <input class="edit-input" value="${esc(a.email || '')}"
                       placeholder="mail@dominio.it / pec@pec.it"
                       onkeydown="saveOnEnter(event)"
                       onblur="saveContact('${esc(a.contact_key)}', null, this)">
                ${renderEmailBadges(a.email)}
            </td>
            <td class="response-cell" data-label="Invii e risposta">
                <div class="send-status">
                    ${a.invii_ok
                        ? `<span class="badge sent">${a.invii_ok} ${a.invii_ok === 1 ? 'invio' : 'invii'}</span>`
                        : '<span class="mini">Nessun invio registrato</span>'}
                    <div class="mini">${a.last_sent_ts ? 'Ultimo: ' + fmtTs(a.last_sent_ts) : ''}</div>
                </div>
                <label>
                    <input type="checkbox" id="risposta_${a.id}" ${a.risposta ? 'checked' : ''}
                           onchange="saveRisposta(${a.id}, this.checked)">
                    Risposta
                </label>
                <input class="note-input" value="${esc(a.note || '')}" placeholder="Nota risposta"
                       onblur="saveRisposta(${a.id}, document.getElementById('risposta_${a.id}').checked, this.value)">
            </td>
        </tr>
    `).join('');
}

function saveOnEnter(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.target.blur();
}

async function saveContact(contactKey, telInput, emailInput) {
    const row = aziende.find(a => a.contact_key === contactKey);
    if (!row) return;
    const payload = {};
    if (telInput) {
        const val = telInput.value.trim();
        if ((row.telefono || '') === val) return;
        payload.telefono = val;
    }
    if (emailInput) {
        const val = emailInput.value.trim();
        if ((row.email || '') === val) return;
        payload.email = val;
    }
    try {
        const res = await fetch(`/api/lettere/contatti/${encodeURIComponent(contactKey)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore salvataggio');
        if ('telefono' in payload) row.telefono = payload.telefono;
        if ('email' in payload) row.email = payload.email;
        showToast(data.contatto?.sync_filone?.synced ? 'Salvato anche sul filone' : 'Contatto salvato');
        renderAziende();
        updateBulkButton();
        loadStats();
    } catch (e) {
        showToast(e.message, true);
    }
}

async function saveRisposta(id, checked, noteValue) {
    const row = aziende.find(a => Number(a.id) === Number(id));
    if (!row) return;
    const note = noteValue !== undefined ? noteValue : row.note;
    try {
        const res = await fetch(`/api/lettere/aziende/${id}/risposta`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ risposta: checked, note })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Errore risposta');
        row.risposta = checked ? 1 : 0;
        if (noteValue !== undefined) row.note = noteValue;
        showToast('Risposta aggiornata');
        loadStats();
    } catch (e) {
        showToast(e.message, true);
    }
}

function selectedAziende() {
    const ids = [...document.querySelectorAll('.row-check:checked')].map(cb => Number(cb.dataset.id));
    return aziende.filter(a => ids.includes(Number(a.id)));
}

function toggleSelectAll(checked) {
    document.querySelectorAll('.row-check').forEach(cb => { cb.checked = checked; });
    updateBulkButton();
}

function updateBulkButton() {
    const count = selectedAziende().filter(a => splitEmails(a.email).length).length;
    document.getElementById('bulkOpenBtn').disabled = count === 0;
}

function getDefaultAllegati(tipo) {
    const prov = (generazioni[0]?.provincia || '').toUpperCase();
    const base = [...((allegatiInfo.default_provincia || {})[prov] || [])];
    if ((tipo || '').toLowerCase().includes('semidirett') && allegatiInfo.allegato_semidiretta) {
        base.push(allegatiInfo.allegato_semidiretta);
    }
    return base;
}

function openBulkModal() {
    const selected = selectedAziende();
    bulkItems = [];
    const seen = new Set();
    for (const a of selected) {
        for (const e of splitEmails(a.email)) {
            const key = `${a.contact_key}|${e.email.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            bulkItems.push({
                azienda_id: a.id,
                nome: a.nominativo,
                pod: a.pod,
                email: e.email,
                pec: e.pec,
                allegati: getDefaultAllegati(a.tipo_misuratore)
            });
        }
    }
    if (!bulkItems.length) {
        showToast('Nessun indirizzo email/PEC selezionato', true);
        return;
    }
    renderBulkList();
    document.getElementById('bulkProgress').style.display = 'none';
    document.getElementById('bulkSendBtn').disabled = false;
    document.getElementById('bulkModal').classList.add('active');
}

function closeBulkModal() {
    document.getElementById('bulkModal').classList.remove('active');
}

function renderBulkList() {
    const list = document.getElementById('bulkList');
    list.innerHTML = bulkItems.map((b, i) => `
        <div class="bulk-item">
            <input type="checkbox" class="bulk-check" data-idx="${i}" checked>
            <div>
                <div class="bulk-name">${esc(b.nome)} ${b.pec ? '<span class="badge pec">PEC</span>' : '<span class="badge">MAIL</span>'}</div>
                <div class="mini">${esc(b.email)} · ${esc(b.pod || '')}</div>
            </div>
            <div class="mini">${b.allegati.length ? b.allegati.length + ' allegati' : 'no allegati'}</div>
        </div>
    `).join('');
}

async function sendBulk() {
    const templateId = document.getElementById('templateSelect').value;
    const items = [...document.querySelectorAll('.bulk-check:checked')]
        .map(cb => bulkItems[Number(cb.dataset.idx)])
        .filter(Boolean);
    if (!templateId) {
        showToast('Seleziona un template', true);
        return;
    }
    if (!items.length) {
        showToast('Nessun indirizzo selezionato', true);
        return;
    }
    if (!confirm(`Inviare ${items.length} email/PEC?\nPausa di ${PAUSA_TRA_EMAIL_SEC}s tra un invio e il successivo.`)) {
        return;
    }

    bulkAbort = false;
    const progress = document.getElementById('bulkProgress');
    const fill = document.getElementById('progressFill');
    const text = document.getElementById('progressText');
    const log = document.getElementById('bulkLog');
    const sendBtn = document.getElementById('bulkSendBtn');
    progress.style.display = '';
    log.innerHTML = '';
    fill.style.width = '0%';
    sendBtn.innerHTML = '<i class="fas fa-stop"></i> Interrompi';
    sendBtn.onclick = () => { bulkAbort = true; };

    let ok = 0;
    let ko = 0;
    for (let i = 0; i < items.length; i++) {
        if (bulkAbort) {
            log.innerHTML += `<div class="log-err">Invio interrotto dopo ${ok} invii riusciti</div>`;
            break;
        }
        const item = items[i];
        text.textContent = `${i + 1} / ${items.length}`;
        fill.style.width = `${Math.round(((i + 1) / items.length) * 100)}%`;
        try {
            const res = await fetch('/api/lettere/invia-email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    azienda_id: item.azienda_id,
                    email: item.email,
                    template_id: templateId,
                    allegati: item.allegati
                })
            });
            const data = await res.json();
            if (data.ok) {
                ok++;
                log.innerHTML += `<div class="log-ok">OK ${esc(item.email)}</div>`;
            } else {
                ko++;
                log.innerHTML += `<div class="log-err">Errore ${esc(item.email)}: ${esc(data.errore || data.error || '')}</div>`;
            }
        } catch (e) {
            ko++;
            log.innerHTML += `<div class="log-err">Errore ${esc(item.email)}: rete</div>`;
        }
        log.scrollTop = log.scrollHeight;
        if (i < items.length - 1 && !bulkAbort) {
            await new Promise(r => setTimeout(r, PAUSA_TRA_EMAIL_SEC * 1000));
        }
    }

    sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Invia selezionate';
    sendBtn.onclick = sendBulk;
    text.textContent = `${ok} inviate, ${ko} errori`;
    showToast(`${ok} invii completati${ko ? ', ' + ko + ' errori' : ''}`, Boolean(ko));
    await loadAziende();
    await loadStats();
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('searchInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') loadAziende();
    });
    const autoOpenEl = document.getElementById('researchAutoOpen');
    if (autoOpenEl) {
        const saved = localStorage.getItem(RESEARCH_AUTO_OPEN_KEY);
        if (saved !== null) {
            autoOpenEl.checked = saved === '1';
        }
        autoOpenEl.addEventListener('change', () => {
            localStorage.setItem(RESEARCH_AUTO_OPEN_KEY, autoOpenEl.checked ? '1' : '0');
        });
    }
    document.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        if (document.getElementById('researchModal').classList.contains('active')) {
            closeContactResearch();
        } else if (document.getElementById('bulkModal').classList.contains('active')) {
            closeBulkModal();
        }
    });
    loadAll().catch(e => showToast(e.message, true));
});
