// -*- coding: utf-8 -*-
/**
 * smtp_config_modal.js
 * Componente unificato per la configurazione e il test del server SMTP / PEC / Email.
 * Condiviso tra il Pannello Admin del Router (admin.*), Gestione Distinte, Gestione Lettere, Gestione Comuni e Mail.
 */

(function () {
  'use strict';

  function _escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function _getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function _notify(message, type = 'info') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type === 'error');
    } else if (typeof window.toast === 'function') {
      window.toast(message, type);
    } else {
      console.log(`[SMTP ${type.toUpperCase()}] ${message}`);
    }
  }

  const PRESETS = {
    aruba_pec: {
      id: 'aruba_pec',
      name: 'Aruba PEC',
      badge: 'PEC',
      badgeColor: '#10b981',
      color: '#10b981',
      icon: 'fas fa-certificate',
      desc: 'smtps.pec.aruba.it',
      host: 'smtps.pec.aruba.it',
      port: 465,
      security: 'ssl',
      save_sent: true,
      imap_host: 'imaps.pec.aruba.it',
      imap_port: 993,
      imap_folder: 'Posta Inviata'
    },
    aruba_ord: {
      id: 'aruba_ord',
      name: 'Aruba Ordinaria',
      badge: 'ORDINARIA',
      badgeColor: '#2563eb',
      color: '#2563eb',
      icon: 'fas fa-envelope',
      desc: 'smtps.aruba.it',
      host: 'smtps.aruba.it',
      port: 465,
      security: 'ssl',
      save_sent: true,
      imap_host: 'imaps.aruba.it',
      imap_port: 993,
      imap_folder: 'Sent'
    },
    m365: {
      id: 'm365',
      name: 'Microsoft 365',
      badge: 'M365',
      badgeColor: '#0284c7',
      color: '#0284c7',
      icon: 'fab fa-microsoft',
      desc: 'smtp.office365.com',
      host: 'smtp.office365.com',
      port: 587,
      security: 'tls',
      save_sent: false,
      imap_host: 'outlook.office365.com',
      imap_port: 993,
      imap_folder: 'Sent Items'
    },
    gmail: {
      id: 'gmail',
      name: 'Google Gmail',
      badge: 'GMAIL',
      badgeColor: '#ea4335',
      color: '#ea4335',
      icon: 'fab fa-google',
      desc: 'smtp.gmail.com',
      host: 'smtp.gmail.com',
      port: 587,
      security: 'tls',
      save_sent: false,
      imap_host: 'imap.gmail.com',
      imap_port: 993,
      imap_folder: '[Gmail]/Sent Mail'
    },
    custom: {
      id: 'custom',
      name: 'Personalizzato',
      badge: 'CUSTOM',
      badgeColor: '#64748b',
      color: '#94a3b8',
      icon: 'fas fa-server',
      desc: 'Server dedicato / VPS',
      host: '',
      port: 465,
      security: 'ssl',
      save_sent: false,
      imap_host: '',
      imap_port: 993,
      imap_folder: 'Posta Inviata'
    }
  };

  let _modalInjected = false;
  let _activePreset = 'custom';
  let _currentOptions = {
    mode: 'provincia', // 'admin' | 'provincia'
    slug: '',
    provinciaLabel: '',
    onSaveSuccess: null,
    initialEmail: ''
  };

  function _injectStyles() {
    if (document.getElementById('smtpConfigModalStyles')) return;
    const style = document.createElement('style');
    style.id = 'smtpConfigModalStyles';
    style.textContent = `
      .smtp-modal-overlay {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        bottom: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        background: rgba(0, 0, 0, 0.72) !important;
        z-index: 99999 !important;
        display: none !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 16px !important;
        box-sizing: border-box !important;
      }
      .smtp-modal-overlay.smtp-modal-open {
        display: flex !important;
      }
      .smtp-modal-overlay .smtp-modal-content {
        animation: smtpModalFadeIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) !important;
      }
      @keyframes smtpModalFadeIn {
        from {
          opacity: 0;
          transform: translateY(12px) scale(0.98);
        }
        to {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
      }

      /* Griglia Preset 5 colonne uniformi */
      .smtp-presets-grid {
        display: grid !important;
        grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
        gap: 10px !important;
      }
      @media (max-width: 760px) {
        .smtp-presets-grid {
          grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        }
      }
      @media (max-width: 480px) {
        .smtp-presets-grid {
          grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        }
      }

      .smtp-preset-card {
        border: 1px solid var(--border) !important;
        background: var(--surface) !important;
        border-radius: 10px !important;
        padding: 9px 11px !important;
        cursor: pointer !important;
        transition: all 0.18s ease !important;
        text-align: left !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: space-between !important;
        min-height: 82px !important;
        height: 82px !important;
        box-sizing: border-box !important;
        overflow: hidden !important;
      }
      .smtp-preset-card:hover {
        border-color: var(--accent) !important;
        background: var(--surface-raised, var(--surface)) !important;
        transform: translateY(-1px) !important;
      }
      .smtp-preset-card.active {
        border: 2px solid var(--accent) !important;
        background: rgba(var(--accent-rgb, 59, 130, 246), 0.14) !important;
        box-shadow: 0 0 0 1px var(--accent) !important;
      }
      .smtp-preset-top {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        width: 100% !important;
        margin-bottom: 2px !important;
      }
      .smtp-preset-icon {
        font-size: 13px !important;
        display: inline-flex !important;
        align-items: center !important;
      }
      .smtp-preset-badge {
        color: #ffffff !important;
        font-size: 0.62rem !important;
        font-weight: 700 !important;
        padding: 2px 5px !important;
        border-radius: 4px !important;
        line-height: 1 !important;
        white-space: nowrap !important;
        letter-spacing: 0.3px !important;
        flex-shrink: 0 !important;
      }
      .smtp-preset-name {
        font-weight: 700 !important;
        font-size: 12.5px !important;
        color: var(--text-primary) !important;
        white-space: nowrap !important;
        line-height: 1.25 !important;
      }
      .smtp-preset-desc {
        font-size: 11px !important;
        color: var(--text-muted) !important;
        line-height: 1.2 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
      }

      /* Card di Sezione */
      .smtp-section-card {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
        padding: 16px !important;
        margin-bottom: 14px !important;
        box-sizing: border-box !important;
      }
      .smtp-section-title {
        font-size: 11.5px !important;
        font-weight: 700 !important;
        color: var(--text-primary) !important;
        text-transform: uppercase !important;
        letter-spacing: 0.5px !important;
        margin-bottom: 12px !important;
        display: flex !important;
        align-items: center !important;
        gap: 7px !important;
      }

      /* Layout a Griglia con Dimensioni Uniformi */
      .smtp-grid-2col {
        display: grid !important;
        grid-template-columns: 1fr 1fr !important;
        gap: 14px !important;
      }
      @media (max-width: 580px) {
        .smtp-grid-2col {
          grid-template-columns: 1fr !important;
        }
      }

      .smtp-grid-host-port-sec {
        display: grid !important;
        grid-template-columns: 1fr 85px 200px !important;
        gap: 14px !important;
      }
      @media (max-width: 660px) {
        .smtp-grid-host-port-sec {
          grid-template-columns: 1fr 85px !important;
        }
        .smtp-grid-host-port-sec > *:last-child {
          grid-column: span 2 !important;
        }
      }
      @media (max-width: 440px) {
        .smtp-grid-host-port-sec {
          grid-template-columns: 1fr !important;
        }
        .smtp-grid-host-port-sec > *:last-child {
          grid-column: auto !important;
        }
      }

      .smtp-grid-tests {
        display: grid !important;
        grid-template-columns: 1fr 1.3fr !important;
        gap: 14px !important;
      }
      @media (max-width: 680px) {
        .smtp-grid-tests {
          grid-template-columns: 1fr !important;
        }
      }

      /* Input e Select Standardizzati */
      .smtp-test-input,
      .smtp-select {
        width: 100% !important;
        box-sizing: border-box !important;
        height: 38px !important;
        padding: 0 12px !important;
        background-color: var(--bg-tertiary, var(--surface)) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-primary) !important;
        font-size: 13px !important;
        font-family: inherit !important;
        line-height: 36px !important;
        transition: border-color 0.18s ease, box-shadow 0.18s ease !important;
      }
      .smtp-test-input:focus,
      .smtp-select:focus {
        outline: none !important;
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(var(--accent-rgb, 59, 130, 246), 0.22) !important;
      }
      .smtp-select {
        cursor: pointer !important;
        appearance: auto !important;
      }

      .smtp-label {
        display: block !important;
        font-size: 11.5px !important;
        font-weight: 600 !important;
        color: var(--text-secondary) !important;
        margin-bottom: 5px !important;
      }

      /* Pulsanti di Azione Uniformi */
      .smtp-btn-action {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 6px !important;
        white-space: nowrap !important;
        height: 38px !important;
        padding: 0 16px !important;
        border-radius: 8px !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        font-family: inherit !important;
        background: var(--bg-tertiary) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border) !important;
        cursor: pointer !important;
        line-height: 1 !important;
        text-decoration: none !important;
        box-sizing: border-box !important;
        transition: all 0.15s ease !important;
      }
      .smtp-btn-action:hover:not(:disabled) {
        background: var(--surface-raised, var(--surface)) !important;
        border-color: var(--accent) !important;
        color: var(--text-primary) !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2) !important;
      }
      .smtp-btn-action:active:not(:disabled) {
        transform: translateY(1px) !important;
      }
      .smtp-btn-action:disabled {
        opacity: 0.5 !important;
        cursor: not-allowed !important;
        pointer-events: none !important;
      }
    `;
    const target = document.head || document.documentElement;
    if (target) target.appendChild(style);
  }

  function _injectModalDom() {
    _injectStyles();
    if (!document.body) return;
    if (_modalInjected || document.getElementById('smtpConfigModalOverlay')) {
      _modalInjected = true;
      return;
    }

    const modalHtml = `
<div id="smtpConfigModalOverlay" class="smtp-modal-overlay" style="display: none;" onclick="if(event.target===this) SmtpConfigModal.close();">
  <div class="smtp-modal-content" style="background: var(--surface-raised); border: 1px solid var(--border); border-radius: 16px; max-width: 820px; width: 100%; max-height: 92vh; overflow-y: auto; box-shadow: 0 16px 48px rgba(0,0,0,0.55); padding: 22px 24px; box-sizing: border-box; font-family: inherit; color: var(--text-primary);">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border);">
      <h3 style="margin: 0; font-size: 1.15rem; font-weight: 700; color: var(--text-primary); display: flex; align-items: center; gap: 8px;">
        <i class="fas fa-envelope-open-text" style="color: #2563eb;"></i>
        <span>Configurazione SMTP / PEC — <span id="smtpModalProvinciaLabel"></span></span>
      </h3>
      <button type="button" onclick="SmtpConfigModal.close()" style="background: transparent; border: none; font-size: 24px; line-height: 1; cursor: pointer; color: var(--text-muted); padding: 4px 8px; border-radius: 4px;" title="Chiudi">&times;</button>
    </div>

    <!-- Selettore Provider Preimpostati (1-Click Presets) -->
    <div style="margin-bottom: 16px;">
      <label style="font-size: 11px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: block;">
        ⚡ Provider Preimpostati (Configurazione Rapida):
      </label>
      <div class="smtp-presets-grid" id="smtpModalPresetsContainer">
        <!-- Generati dinamicamente da PRESETS -->
      </div>
    </div>

    <form id="smtpModalForm" onsubmit="SmtpConfigModal.save(event)">
      <input type="hidden" id="smtpModalProvincia" name="provincia" value="">

      <!-- Sezione Mittente -->
      <div class="smtp-section-card">
        <div class="smtp-section-title">
          <i class="fas fa-user-check" style="color: var(--accent);"></i> Indirizzo Mittente Visibile
        </div>
        <div class="smtp-grid-2col">
          <div>
            <label class="smtp-label">Email Mittente (From)</label>
            <input type="email" id="smtpModalFromEmail" name="from_email" required placeholder="es. posta@azienda.it"
                   class="smtp-test-input">
          </div>
          <div>
            <label class="smtp-label">Nome Mittente (Visualizzato)</label>
            <input type="text" id="smtpModalFromName" name="from_name" placeholder="es. Algeco Power - Ufficio Tecnico"
                   class="smtp-test-input">
          </div>
        </div>
      </div>

      <!-- Sezione Server SMTP -->
      <div class="smtp-section-card">
        <div class="smtp-section-title">
          <i class="fas fa-server" style="color: var(--accent);"></i> Server di Posta in Uscita (SMTP)
        </div>

        <!-- Riga 1: Host, Porta, Crittografia -->
        <div class="smtp-grid-host-port-sec" style="margin-bottom: 12px;">
          <div>
            <label class="smtp-label">Host Server SMTP</label>
            <input type="text" id="smtpModalHost" name="host" required placeholder="es. smtps.pec.aruba.it"
                   class="smtp-test-input">
          </div>
          <div>
            <label class="smtp-label" style="text-align: center;">Porta</label>
            <input type="number" id="smtpModalPort" name="port" required placeholder="465" value="465"
                   class="smtp-test-input" style="text-align: center;">
          </div>
          <div>
            <label class="smtp-label">Crittografia / Sicurezza</label>
            <select id="smtpModalSecurity" name="security" onchange="SmtpConfigModal.onSecurityChange()"
                    class="smtp-select">
              <option value="ssl">SSL / TLS (porta 465)</option>
              <option value="tls">STARTTLS (porta 587)</option>
              <option value="none">Nessuna (porta 25)</option>
            </select>
          </div>
        </div>

        <!-- Riga 2: Username e Password -->
        <div class="smtp-grid-2col">
          <div>
            <label class="smtp-label">Username / Login SMTP</label>
            <input type="text" id="smtpModalUsername" name="username" required placeholder="es. posta@azienda.it"
                   class="smtp-test-input">
          </div>
          <div>
            <label class="smtp-label">Password SMTP</label>
            <div style="position: relative; width: 100%; display: flex; align-items: center;">
              <input type="password" id="smtpModalPassword" name="password" placeholder="(Lascia vuoto per non modificare)"
                     class="smtp-test-input" style="padding-right: 38px;">
              <button type="button" onclick="SmtpConfigModal.togglePasswordVisibility()" title="Mostra/Nascondi password"
                      style="position: absolute; right: 0; top: 0; height: 38px; width: 36px; background: transparent; border: none; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 13px;">
                <i class="fas fa-eye" id="smtpModalEyeIcon"></i>
              </button>
            </div>
            <div id="smtpModalPasswordNotice" style="display: none; font-size: 0.75rem; color: #10b981; margin-top: 4px;">
              <i class="fas fa-check-circle"></i> Password memorizzata sul server
            </div>
          </div>
        </div>
      </div>

      <!-- Sezione Avanzata: Posta Inviata IMAP (Opzionale) -->
      <div class="smtp-section-card" style="padding: 14px 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; cursor: pointer;" onclick="SmtpConfigModal.toggleImapSection()">
          <div style="display: flex; align-items: center; gap: 8px;">
            <input type="checkbox" id="smtpModalSaveSent" name="save_sent" checked onclick="event.stopPropagation()" style="width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent);">
            <label for="smtpModalSaveSent" onclick="event.stopPropagation()" style="font-size: 11.5px; font-weight: 700; color: var(--text-primary); text-transform: uppercase; letter-spacing: 0.5px; cursor: pointer;">
              Salva copia in Posta Inviata (IMAP)
            </label>
          </div>
          <span id="smtpModalImapToggleIcon" style="font-size: 12px; color: var(--text-muted); padding: 4px 6px;"><i class="fas fa-chevron-down"></i></span>
        </div>

        <div id="smtpModalImapDetails" style="display: none; margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border);">
          <div style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 10px;">
            Archivia una copia conforme del messaggio inviato nella cartella IMAP del tuo account (consultabile da Webmail o client di posta).
          </div>
          <div class="smtp-grid-host-port-sec">
            <div>
              <label class="smtp-label">Host Server IMAP</label>
              <input type="text" id="smtpModalImapHost" name="imap_host" placeholder="es. imaps.pec.aruba.it"
                     class="smtp-test-input">
            </div>
            <div>
              <label class="smtp-label" style="text-align: center;">Porta IMAP</label>
              <input type="number" id="smtpModalImapPort" name="imap_port" placeholder="993" value="993"
                     class="smtp-test-input" style="text-align: center;">
            </div>
            <div>
              <label class="smtp-label">Cartella Posta Inviata</label>
              <input type="text" id="smtpModalImapFolder" name="imap_folder" placeholder="Posta Inviata o Sent" value="Posta Inviata"
                     class="smtp-test-input">
            </div>
          </div>
        </div>
      </div>

      <!-- Sezione Test Live: Connessione e Invio Email di Prova -->
      <div class="smtp-section-card" style="margin-bottom: 18px;">
        <div class="smtp-section-title">
          <i class="fas fa-vial" style="color: #10b981;"></i> Test & Diagnostica in Tempo Reale
        </div>

        <div class="smtp-grid-tests">
          <!-- Box 1: Connessione e Handshake -->
          <div style="background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; justify-content: space-between;">
            <div>
              <div style="font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 2px;">1. Connessione & Autenticazione</div>
              <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 10px;">Verifica handshake e credenziali sul server</div>
            </div>
            <button type="button" class="smtp-btn-action" id="smtpModalBtnCheckConn" onclick="SmtpConfigModal.testConnection()" style="width: 100%;">
              <i class="fas fa-plug"></i> Verifica Connessione
            </button>
            <div id="smtpModalConnStatus" style="display: none; padding: 8px 10px; border-radius: 6px; font-size: 0.8rem; margin-top: 8px; word-break: break-word;"></div>
          </div>

          <!-- Box 2: Invio Email di Prova -->
          <div style="background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; justify-content: space-between;">
            <div>
              <div style="font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 2px;">2. Invio Reale Email di Test</div>
              <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 10px;">Invia un messaggio di prova alla casella indicata</div>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <input type="email" id="smtpModalTestEmailInput" placeholder="Destinatario test (es. tuo@dominio.it)"
                     class="smtp-test-input"
                     onkeydown="if(event.key==='Enter'){event.preventDefault(); SmtpConfigModal.sendTestEmail();}">
              <button type="button" class="smtp-btn-action" id="smtpModalBtnSendTest" onclick="SmtpConfigModal.sendTestEmail()" style="background: #2563eb; color: white; border-color: #2563eb; flex-shrink: 0; padding: 0 14px;">
                <i class="fas fa-paper-plane"></i> Invia Prova
              </button>
            </div>
            <div id="smtpModalSendStatus" style="display: none; padding: 8px 10px; border-radius: 6px; font-size: 0.8rem; margin-top: 8px; word-break: break-word;"></div>
          </div>
        </div>
      </div>

      <!-- Azioni Salva e Chiudi -->
      <div style="display: flex; justify-content: flex-end; gap: 10px; padding-top: 12px; border-top: 1px solid var(--border);">
        <button type="button" class="smtp-btn-action" onclick="SmtpConfigModal.close()" style="background: var(--bg-tertiary); height: 40px; padding: 0 18px;">Annulla</button>
        <button type="submit" class="smtp-btn-action" id="smtpModalBtnSave" style="background: linear-gradient(135deg, #10b981, #059669); color: white; border: none; font-weight: 700; height: 40px; padding: 0 22px;">
          <i class="fas fa-save"></i> Salva Configurazione
        </button>
      </div>
    </form>
  </div>
</div>
`;

    document.body.insertAdjacentHTML('beforeend', modalHtml);
    _modalInjected = true;

    _renderPresetCards();
  }

  function _renderPresetCards() {
    const container = document.getElementById('smtpModalPresetsContainer');
    if (!container) return;

    let html = '';
    for (const [key, p] of Object.entries(PRESETS)) {
      const isActive = key === _activePreset;
      html += `
        <div class="smtp-preset-card ${isActive ? 'active' : ''}" id="smtpPresetCard_${key}" onclick="SmtpConfigModal.applyPreset('${key}')" title="${_escapeHtml(p.name)} (${_escapeHtml(p.desc)})">
          <div class="smtp-preset-top">
            <span class="smtp-preset-icon" style="color: ${p.color};"><i class="${p.icon}"></i></span>
            <span class="smtp-preset-badge" style="background: ${p.badgeColor};">${_escapeHtml(p.badge)}</span>
          </div>
          <div class="smtp-preset-name">${_escapeHtml(p.name)}</div>
          <div class="smtp-preset-desc">${_escapeHtml(p.desc)}</div>
        </div>
      `;
    }
    container.innerHTML = html;
  }

  const SmtpConfigModal = {
    open: async function (options = {}) {
      _currentOptions = Object.assign({
        mode: options.slug ? 'admin' : 'provincia',
        slug: options.slug || '',
        provinciaLabel: options.provinciaLabel || options.slug || window.PROVINCIA_ATTIVA || '',
        onSaveSuccess: options.onSaveSuccess || null,
        initialEmail: options.initialEmail || ''
      }, options);

      _injectModalDom();

      const overlay = document.getElementById('smtpConfigModalOverlay');
      if (!overlay) return;

      const labelEl = document.getElementById('smtpModalProvinciaLabel');
      if (labelEl) {
        labelEl.textContent = _currentOptions.provinciaLabel.toUpperCase();
      }
      const slugInput = document.getElementById('smtpModalProvincia');
      if (slugInput) {
        slugInput.value = _currentOptions.slug;
      }

      // Reset stato alert e campi
      const form = document.getElementById('smtpModalForm');
      if (form) form.reset();

      this._hideStatus('smtpModalConnStatus');
      this._hideStatus('smtpModalSendStatus');

      const pwdNotice = document.getElementById('smtpModalPasswordNotice');
      if (pwdNotice) pwdNotice.style.display = 'none';

      // Carica credenziali correnti
      await this.loadCurrentSettings();

      if (_currentOptions.initialEmail && !document.getElementById('smtpModalTestEmailInput').value) {
        document.getElementById('smtpModalTestEmailInput').value = _currentOptions.initialEmail;
      }

      overlay.style.display = 'flex';
      overlay.classList.add('smtp-modal-open');
    },

    close: function () {
      const overlay = document.getElementById('smtpConfigModalOverlay');
      if (overlay) {
        overlay.classList.remove('smtp-modal-open');
        overlay.style.display = 'none';
      }
    },

    applyPreset: function (presetKey) {
      const p = PRESETS[presetKey];
      if (!p) return;
      _activePreset = presetKey;
      _renderPresetCards();

      if (p.host) document.getElementById('smtpModalHost').value = p.host;
      if (p.port) document.getElementById('smtpModalPort').value = p.port;
      if (p.security) document.getElementById('smtpModalSecurity').value = p.security;
      if (typeof p.save_sent === 'boolean') {
        const cb = document.getElementById('smtpModalSaveSent');
        if (cb) cb.checked = p.save_sent;
      }
      if (p.imap_host) document.getElementById('smtpModalImapHost').value = p.imap_host;
      if (p.imap_port) document.getElementById('smtpModalImapPort').value = p.imap_port;
      if (p.imap_folder) document.getElementById('smtpModalImapFolder').value = p.imap_folder;

      this.onSecurityChange();
    },

    onSecurityChange: function () {
      const sec = document.getElementById('smtpModalSecurity').value;
      const portEl = document.getElementById('smtpModalPort');
      if (!portEl) return;
      if (sec === 'ssl' && portEl.value === '587') {
        portEl.value = '465';
      } else if (sec === 'tls' && portEl.value === '465') {
        portEl.value = '587';
      }
    },

    togglePasswordVisibility: function () {
      const input = document.getElementById('smtpModalPassword');
      const icon = document.getElementById('smtpModalEyeIcon');
      if (!input || !icon) return;
      if (input.type === 'password') {
        input.type = 'text';
        icon.className = 'fas fa-eye-slash';
      } else {
        input.type = 'password';
        icon.className = 'fas fa-eye';
      }
    },

    toggleImapSection: function () {
      const details = document.getElementById('smtpModalImapDetails');
      const icon = document.getElementById('smtpModalImapToggleIcon');
      if (!details) return;
      if (details.style.display === 'none') {
        details.style.display = 'block';
        if (icon) icon.innerHTML = '<i class="fas fa-chevron-up"></i>';
      } else {
        details.style.display = 'none';
        if (icon) icon.innerHTML = '<i class="fas fa-chevron-down"></i>';
      }
    },

    loadCurrentSettings: async function () {
      let url = '';
      if (_currentOptions.mode === 'admin') {
        url = `/api/provinces/${_currentOptions.slug}/smtp-settings`;
      } else {
        url = '/api/smtp-settings';
      }

      try {
        const resp = await fetch(url, { credentials: 'same-origin' });
        if (!resp.ok) {
          // fallback se endpoint generico non risponde
          if (_currentOptions.mode !== 'admin') {
            const fallbackResp = await fetch('/api/distinte/smtp-settings', { credentials: 'same-origin' });
            if (fallbackResp.ok) {
              const data = await fallbackResp.json();
              this._populateForm(data);
              return;
            }
          }
          return;
        }
        const data = await resp.json();
        this._populateForm(data);
      } catch (e) {
        console.warn('[SmtpConfigModal] Impossibile caricare impostazioni correnti:', e);
      }
    },

    _populateForm: function (data) {
      if (!data) return;
      if (data.host) document.getElementById('smtpModalHost').value = data.host;
      if (data.port) document.getElementById('smtpModalPort').value = data.port;
      if (data.username) document.getElementById('smtpModalUsername').value = data.username;
      if (data.from_email) {
        document.getElementById('smtpModalFromEmail').value = data.from_email;
        const testInput = document.getElementById('smtpModalTestEmailInput');
        if (testInput && !testInput.value) {
          testInput.value = data.from_email;
        }
      }
      if (data.from_name) document.getElementById('smtpModalFromName').value = data.from_name;
      if (data.imap_host) document.getElementById('smtpModalImapHost').value = data.imap_host;
      if (data.imap_port) document.getElementById('smtpModalImapPort').value = data.imap_port;
      if (data.imap_folder) document.getElementById('smtpModalImapFolder').value = data.imap_folder;

      if (typeof data.save_sent === 'boolean') {
        const cb = document.getElementById('smtpModalSaveSent');
        if (cb) cb.checked = data.save_sent;
      }

      const secEl = document.getElementById('smtpModalSecurity');
      if (secEl) {
        if (data.use_ssl || (data.port === 465 && data.use_tls !== true)) {
          secEl.value = 'ssl';
        } else if (data.use_tls) {
          secEl.value = 'tls';
        } else if (data.use_ssl === false && data.use_tls === false) {
          secEl.value = 'none';
        }
      }

      const pwdNotice = document.getElementById('smtpModalPasswordNotice');
      const pwdInput = document.getElementById('smtpModalPassword');
      if (data.password_set) {
        if (pwdNotice) pwdNotice.style.display = 'block';
        if (pwdInput) pwdInput.placeholder = '●●●●●●●● (già salvata)';
      } else {
        if (pwdNotice) pwdNotice.style.display = 'none';
        if (pwdInput) pwdInput.placeholder = 'Inserisci password SMTP...';
      }

      // Riconosci eventuale preset corrispondente
      const host = (data.host || '').toLowerCase();
      if (host.includes('pec.aruba.it')) {
        _activePreset = 'aruba_pec';
      } else if (host.includes('aruba.it')) {
        _activePreset = 'aruba_ord';
      } else if (host.includes('office365.com') || host.includes('outlook.com')) {
        _activePreset = 'm365';
      } else if (host.includes('gmail.com')) {
        _activePreset = 'gmail';
      } else {
        _activePreset = 'custom';
      }
      _renderPresetCards();
    },

    _getFormPayload: function () {
      const sec = document.getElementById('smtpModalSecurity').value;
      const payload = {
        host: document.getElementById('smtpModalHost').value.trim(),
        port: parseInt(document.getElementById('smtpModalPort').value, 10) || 465,
        username: document.getElementById('smtpModalUsername').value.trim(),
        from_email: document.getElementById('smtpModalFromEmail').value.trim(),
        from_name: document.getElementById('smtpModalFromName').value.trim(),
        use_ssl: sec === 'ssl',
        use_tls: sec === 'tls',
        save_sent: document.getElementById('smtpModalSaveSent').checked,
        imap_host: document.getElementById('smtpModalImapHost').value.trim(),
        imap_port: parseInt(document.getElementById('smtpModalImapPort').value, 10) || 993,
        imap_folder: document.getElementById('smtpModalImapFolder').value.trim() || 'Posta Inviata'
      };

      const pwd = document.getElementById('smtpModalPassword').value;
      if (pwd) {
        payload.password = pwd;
      }
      return payload;
    },

    testConnection: async function () {
      const btn = document.getElementById('smtpModalBtnCheckConn');
      const statusEl = document.getElementById('smtpModalConnStatus');
      const payload = this._getFormPayload();

      if (!payload.host || !payload.username) {
        this._showStatus('smtpModalConnStatus', 'Inserisci almeno Host e Username per testare la connessione.', 'error');
        return;
      }

      let url = '';
      if (_currentOptions.mode === 'admin') {
        url = `/api/provinces/${_currentOptions.slug}/smtp-test`;
      } else {
        url = '/api/smtp-test';
      }

      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Verifica...';
      this._showStatus('smtpModalConnStatus', 'Tentativo di connessione al server SMTP in corso...', 'info');

      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': _getCsrfToken()
          },
          body: JSON.stringify({ settings: payload })
        });
        const data = await resp.json();
        if (data.ok) {
          this._showStatus('smtpModalConnStatus', `✅ ${data.message || 'Connessione e autenticazione riuscite con successo!'}`, 'success');
        } else {
          this._showStatus('smtpModalConnStatus', `❌ ${data.error || data.errore || 'Errore di autenticazione/connessione'}`, 'error');
        }
      } catch (err) {
        this._showStatus('smtpModalConnStatus', `❌ Errore di rete: ${err.message}`, 'error');
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-plug"></i> Verifica Connessione';
      }
    },

    sendTestEmail: async function () {
      const btn = document.getElementById('smtpModalBtnSendTest');
      const input = document.getElementById('smtpModalTestEmailInput');
      const recipient = (input.value || '').trim();

      if (!recipient || !recipient.includes('@')) {
        this._showStatus('smtpModalSendStatus', 'Inserisci un indirizzo email di prova valido.', 'error');
        input.focus();
        return;
      }

      const payload = this._getFormPayload();
      if (!payload.host || !payload.from_email) {
        this._showStatus('smtpModalSendStatus', 'Configura almeno Host e Email Mittente prima dell\'invio.', 'error');
        return;
      }

      let url = '';
      if (_currentOptions.mode === 'admin') {
        url = `/api/provinces/${_currentOptions.slug}/smtp-test-email`;
      } else {
        url = '/api/smtp-test-email';
      }

      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Invio...';
      this._showStatus('smtpModalSendStatus', `Invio email di test a ${recipient}...`, 'info');

      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': _getCsrfToken()
          },
          body: JSON.stringify({
            email: recipient,
            recipient: recipient,
            settings: payload
          })
        });
        const data = await resp.json();
        if (data.ok) {
          this._showStatus('smtpModalSendStatus', `✅ ${data.message || 'Email di prova inviata con successo! Controlla la casella di posta.'}`, 'success');
          _notify('Email di prova inviata con successo!', 'success');
        } else {
          this._showStatus('smtpModalSendStatus', `❌ ${data.error || data.errore || 'Invio email fallito'}`, 'error');
        }
      } catch (err) {
        this._showStatus('smtpModalSendStatus', `❌ Errore di rete durante l'invio: ${err.message}`, 'error');
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-paper-plane"></i> Invia Prova';
      }
    },

    save: async function (e) {
      if (e) e.preventDefault();
      const btn = document.getElementById('smtpModalBtnSave');
      const payload = this._getFormPayload();

      let url = '';
      if (_currentOptions.mode === 'admin') {
        url = `/api/provinces/${_currentOptions.slug}/smtp-settings`;
      } else {
        url = '/api/smtp-settings';
      }

      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Salvataggio...';

      try {
        let resp = await fetch(url, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': _getCsrfToken()
          },
          body: JSON.stringify(payload)
        });

        // Fallback su distinte se in modalità provincia e /api/smtp-settings fallisce
        if (!resp.ok && _currentOptions.mode !== 'admin') {
          resp = await fetch('/api/distinte/smtp-settings', {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': _getCsrfToken()
            },
            body: JSON.stringify(payload)
          });
        }

        const data = await resp.json();
        if (resp.ok && (data.ok || data.success)) {
          _notify('Configurazione SMTP salvata con successo!', 'success');
          if (typeof _currentOptions.onSaveSuccess === 'function') {
            try {
              _currentOptions.onSaveSuccess(payload);
            } catch (cbErr) {
              console.error('[SmtpConfigModal] onSaveSuccess error:', cbErr);
            }
          }
          this.close();
        } else {
          const msg = data.error || data.errore || 'Errore salvataggio impostazioni';
          this._showStatus('smtpModalConnStatus', `❌ ${msg}`, 'error');
          _notify(`Errore: ${msg}`, 'error');
        }
      } catch (err) {
        this._showStatus('smtpModalConnStatus', `❌ Errore di connessione: ${err.message}`, 'error');
        _notify(`Errore di rete: ${err.message}`, 'error');
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-save"></i> Salva Configurazione';
      }
    },

    _showStatus: function (elementId, message, type) {
      const el = document.getElementById(elementId);
      if (!el) return;
      el.style.display = 'block';
      if (type === 'success') {
        el.style.background = 'rgba(16, 185, 129, 0.15)';
        el.style.border = '1px solid #10b981';
        el.style.color = '#10b981';
      } else if (type === 'error') {
        el.style.background = 'rgba(239, 68, 68, 0.15)';
        el.style.border = '1px solid #ef4444';
        el.style.color = '#ef4444';
      } else {
        el.style.background = 'rgba(37, 99, 235, 0.12)';
        el.style.border = '1px solid #2563eb';
        el.style.color = '#2563eb';
      }
      el.innerHTML = message;
    },

    _hideStatus: function (elementId) {
      const el = document.getElementById(elementId);
      if (el) {
        el.style.display = 'none';
        el.innerHTML = '';
      }
    }
  };

  // Esportazioni globali
  window.SmtpConfigModal = SmtpConfigModal;

  window.apriSmtpSettings = function (slug) {
    SmtpConfigModal.open({ mode: slug ? 'admin' : 'provincia', slug: slug });
  };

  window.chiudiSmtpSettings = function () {
    SmtpConfigModal.close();
  };

  window.apriSmtpModalDistinte = function (provinciaLabel) {
    SmtpConfigModal.open({
      mode: 'provincia',
      provinciaLabel: provinciaLabel || window.PROVINCIA_ATTIVA || ''
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _injectModalDom);
  } else {
    _injectModalDom();
  }
})();
