// -*- coding: utf-8 -*-
/**
 * sms_gateway_modal.js
 * Componente unificato per la configurazione del Gateway SMS (Android SMS Gateway / Webhook).
 * Condiviso tra il Pannello Admin del Router (admin.*) e la Gestione Distinte (singola provincia).
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
      window.showToast(message, type);
    } else if (typeof window.toast === 'function') {
      window.toast(message, type);
    } else {
      console.log(`[SMS Gateway ${type.toUpperCase()}] ${message}`);
    }
  }

  let _modalInjected = false;
  let _currentOptions = {
    mode: 'admin', // 'admin' | 'distinte'
    slug: '',
    provinciaLabel: '',
    onSaveSuccess: null
  };

  function _injectStyles() {
    if (document.getElementById('smsGatewayModalStyles')) return;
    const style = document.createElement('style');
    style.id = 'smsGatewayModalStyles';
    style.textContent = `
      .sms-modal-overlay {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        bottom: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        background: rgba(0, 0, 0, 0.65) !important;
        z-index: 99999 !important;
        display: none !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 20px !important;
        box-sizing: border-box !important;
      }
      .sms-modal-overlay.sms-modal-open {
        display: flex !important;
      }
      .sms-modal-overlay .sms-modal-content {
        animation: smsModalFadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
      }
      @keyframes smsModalFadeIn {
        from {
          opacity: 0;
          transform: translateY(12px) scale(0.97);
        }
        to {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
      }
      .sms-test-input {
        flex: 1 !important;
        min-width: 0 !important;
        box-sizing: border-box !important;
        height: 40px !important;
        padding: 9px 12px !important;
        background-color: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        color: var(--text-primary) !important;
        font-size: 14px !important;
        font-family: inherit !important;
        line-height: 1.4 !important;
        transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease !important;
      }
      .sms-test-input:focus {
        outline: none !important;
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(var(--accent-rgb, 59, 130, 246), 0.25) !important;
      }
      .sms-test-input::placeholder {
        color: var(--text-muted) !important;
        opacity: 0.7 !important;
      }
      .sms-test-btn {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 6px !important;
        white-space: nowrap !important;
        height: 40px !important;
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
        box-shadow: none !important;
        transform: none !important;
        transition: all 0.15s ease !important;
      }
      .sms-test-btn:hover:not(:disabled) {
        background: var(--surface) !important;
        border-color: var(--accent) !important;
        color: var(--text-primary) !important;
        box-shadow: 0 2px 6px var(--shadow, rgba(0, 0, 0, 0.2)) !important;
      }
      .sms-test-btn:active:not(:disabled) {
        transform: translateY(1px) !important;
      }
      .sms-test-btn:disabled {
        opacity: 0.5 !important;
        cursor: not-allowed !important;
        pointer-events: none !important;
      }
    `;
    const target = document.head || document.documentElement;
    if (target) {
      target.appendChild(style);
    }
  }

  function _injectModalDom() {
    _injectStyles();

    if (!document.body) {
      return;
    }
    if (_modalInjected || document.getElementById('smsGatewayModalOverlay')) {
      _modalInjected = true;
      return;
    }

    const modalHtml = `
<div id="smsGatewayModalOverlay" class="sms-modal-overlay" style="display: none;" onclick="if(event.target===this) SmsGatewayModal.close();">
  <div class="sms-modal-content" style="background: var(--surface-raised); border: 1px solid var(--border); border-radius: 16px; max-width: 640px; width: 100%; max-height: 90vh; overflow-y: auto; box-shadow: 0 10px 30px rgba(0,0,0,0.4); padding: 22px 24px; box-sizing: border-box; font-family: inherit;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border);">
      <h3 style="margin: 0; font-size: 1.15rem; font-weight: 700; color: var(--text-primary); display: flex; align-items: center; gap: 8px;">
        <i class="fas fa-sms" style="color: #10b981;"></i>
        <span>Configura Gateway SMS — <span id="smsModalProvinciaLabel"></span></span>
      </h3>
      <button type="button" onclick="SmsGatewayModal.close()" style="background: transparent; border: none; font-size: 24px; line-height: 1; cursor: pointer; color: var(--text-muted); padding: 4px 8px; border-radius: 4px;" title="Chiudi">&times;</button>
    </div>

    <form id="smsModalForm" onsubmit="SmsGatewayModal.save(event)">
      <input type="hidden" id="smsModalProvider" name="provider" value="android_sms_gateway">

      <!-- Switch Abilitazione Servizio -->
      <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
        <div>
          <div style="font-weight: 600; color: var(--text-primary); font-size: 0.92rem;">Servizio SMS per questa provincia</div>
          <div style="font-size: 0.8rem; color: var(--text-muted);">Abilita l'invio multiplo SMS nella gestione distinte</div>
        </div>
        <label style="display: flex; align-items: center; gap: 8px; font-weight: 600; cursor: pointer; margin: 0; color: var(--text-primary); font-size: 0.9rem;">
          <input type="checkbox" id="smsModalEnabled" name="enabled" checked style="width: 18px; height: 18px; cursor: pointer;">
          <span id="smsModalEnabledLabel" style="color: #10b981;">Abilitato</span>
        </label>
      </div>

      <!-- Selettore modalità a schede cliccabili -->
      <div style="margin-bottom: 16px;">
        <label style="font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 8px; display: block;">
          Seleziona il Provider SMS:
        </label>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;" id="smsModalProviderCards">
          <!-- Card 1: Android SMS Gateway -->
          <div class="sms-mode-card" id="smsModalCard_android" onclick="SmsGatewayModal.selezionaProvider('android_sms_gateway')"
               style="border: 2px solid var(--accent); background: rgba(var(--accent-rgb), 0.12); border-radius: 10px; padding: 12px; cursor: pointer; transition: all 0.2s;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
              <span style="font-weight: 700; color: var(--text-primary); font-size: 0.95rem;">
                📱 Android SMS Gateway
              </span>
              <span style="background: #2563eb; color: white; font-size: 0.65rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">CONSIGLIATO</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-secondary); line-height: 1.35;">
              App Capcom6 su smartphone con SIM. Cloud Relay o HTTP locale.
            </div>
          </div>

          <!-- Card 2: Webhook HTTP generico -->
          <div class="sms-mode-card" id="smsModalCard_generic" onclick="SmsGatewayModal.selezionaProvider('generic_webhook')"
               style="border: 1px solid var(--border); background: var(--surface); border-radius: 10px; padding: 12px; cursor: pointer; transition: all 0.2s;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px;">
              <span style="font-weight: 700; color: var(--text-primary); font-size: 0.95rem;">
                🌐 Webhook HTTP
              </span>
              <span style="background: #64748b; color: white; font-size: 0.65rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">GENERICO</span>
            </div>
            <div style="font-size: 0.8rem; color: var(--text-secondary); line-height: 1.35;">
              Invia richieste POST a qualsiasi gateway o aggregatore SMS HTTP custom.
            </div>
          </div>
        </div>
      </div>

      <!-- Box Guida Dinamico Integrato con il Tema -->
      <div id="smsModalGuideBox" style="background: var(--surface); border-left: 4px solid var(--accent); border-top: 1px solid var(--border); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 12px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 0.85rem; line-height: 1.45; color: var(--text-primary);">
      </div>

      <!-- Configurazione Endpoint e Autenticazione -->
      <div style="display: flex; gap: 12px; margin-bottom: 14px;">
        <div style="flex: 2;">
          <label style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">
            <span>URL Endpoint Gateway</span>
            <a href="javascript:void(0)" onclick="SmsGatewayModal.impostaEndpointConsigliato()" style="font-size: 0.75rem; text-decoration: underline; color: var(--accent); text-transform: none; letter-spacing: normal;">Usa default consigliato</a>
          </label>
          <input type="url" id="smsModalEndpointUrl" name="endpoint_url" required placeholder="https://api.sms-gate.app/3rdparty/v1/message"
                 oninput="SmsGatewayModal.checkPrivateIp(this.value)"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
          <div id="smsModalPrivateIpWarning" style="display:none; margin-top:6px; padding:8px 12px; border-radius:6px; background:rgba(245, 158, 11, 0.15); border:1px solid #f59e0b; color:#f59e0b; font-size:0.8rem; line-height:1.4;">
            <i class="fas fa-exclamation-triangle"></i> <strong>Attenzione IP locale:</strong> Questo gestionale si trova su un server cloud remoto. Non può contattare direttamente l'IP Wi-Fi privato del telefono (es. <code>192.168.x.x</code>). Per connettere il telefono usa la modalità <strong>Cloud Server</strong> dell'app (<a href="javascript:void(0)" onclick="SmsGatewayModal.impostaEndpointConsigliato()" style="color:#f59e0b; text-decoration:underline;">clicca qui per impostare l'endpoint Cloud</a>).
          </div>
        </div>
        <div style="flex: 1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Autenticazione</label>
          <select id="smsModalAuthType" name="auth_type" onchange="SmsGatewayModal.onAuthTypeChange()"
                  style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
            <option value="none">Nessuna</option>
            <option value="basic">Basic Auth (User/Pass)</option>
            <option value="bearer">Bearer Token</option>
            <option value="api_key">API Key Header</option>
          </select>
        </div>
      </div>

      <!-- Campi Auth Basic -->
      <div id="smsModalAuthBasicRow" style="display:none; gap: 12px; margin-bottom: 14px;">
        <div style="flex:1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Username</label>
          <input type="text" id="smsModalUsername" name="username" placeholder="Username impostato nell'app"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
        </div>
        <div style="flex:1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Password</label>
          <input type="password" id="smsModalPassword" name="password" placeholder="(Lascia vuoto per non modificare)"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
        </div>
      </div>

      <!-- Campi Auth Token -->
      <div id="smsModalAuthTokenRow" style="display:none; gap: 12px; margin-bottom: 14px;">
        <div style="flex:1;">
          <label id="smsModalTokenLabel" style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Token / API Key</label>
          <input type="password" id="smsModalApiKey" name="api_key" placeholder="(Lascia vuoto per non modificare)"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
        </div>
        <div id="smsModalHeaderNameGroup" style="display:none; flex:1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Nome Header</label>
          <input type="text" id="smsModalHeaderName" name="header_name" placeholder="X-API-Key" value="X-API-Key"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
        </div>
      </div>

      <!-- Pausa Anti-Flood e Timeout -->
      <div style="display: flex; gap: 12px; margin-bottom: 14px;">
        <div style="flex:1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Pausa tra invii (secondi)</label>
          <input type="number" id="smsModalRateLimitDelay" name="rate_limit_delay" min="1" max="60" value="4"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
          <div style="color: var(--text-muted); font-size: 11px; margin-top: 4px;">Consigliati 4-5 sec anti-flood.</div>
        </div>
        <div style="flex:1;">
          <label style="display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 5px;">Timeout chiamata (sec)</label>
          <input type="number" id="smsModalTimeoutSeconds" name="timeout_seconds" min="3" max="60" value="15"
                 style="width: 100%; box-sizing: border-box; padding: 9px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border); background-color: var(--surface); color: var(--text-primary); font-family: inherit;">
          <div style="color: var(--text-muted); font-size: 11px; margin-top: 4px;">Tempo massimo attesa risposta.</div>
        </div>
      </div>

      <!-- Test Connessione e Invio SMS -->
      <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-top: 10px; margin-bottom: 16px;">
        <label style="font-weight: 600; font-size: 0.88rem; color: var(--text-primary); margin-bottom: 8px; display: block; text-transform: none; letter-spacing: normal;">
          Test di Connessione e Invio:
        </label>
        <div style="display: flex; gap: 8px; align-items: center;">
          <input type="text" id="smsModalTestPhone" class="sms-test-input" placeholder="Numero cellulare di prova (es. 3471234567)"
                 onkeydown="if(event.key==='Enter'){event.preventDefault(); SmsGatewayModal.sendTest();}">
          <button type="button" class="btn secondary sms-test-btn" id="smsModalBtnTest" onclick="SmsGatewayModal.sendTest()">
            📱 Invia Prova
          </button>
        </div>
        <div id="smsModalTestResult" style="font-size: 0.85rem; margin-top: 8px; display: none; padding: 8px 12px; border-radius: 6px;"></div>
      </div>

      <!-- Azioni Modal -->
      <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px;">
        <button type="button" class="btn secondary" onclick="SmsGatewayModal.close()" style="padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-tertiary); color: var(--text-primary); cursor: pointer; font-weight: 600;">Annulla</button>
        <button type="submit" class="btn" style="background: linear-gradient(135deg, #10b981, #059669); color: white; border: none; padding: 9px 20px; border-radius: 8px; cursor: pointer; font-weight: 600;">
          💾 Salva Impostazioni
        </button>
      </div>
    </form>
  </div>
</div>
`;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = modalHtml;
    document.body.appendChild(wrapper.firstElementChild);
    _modalInjected = true;
  }

  const SmsGatewayModal = {
    async open(options = {}) {
      _injectModalDom();

      _currentOptions = Object.assign({
        mode: options.slug ? 'admin' : 'distinte',
        slug: '',
        provinciaLabel: '',
        onSaveSuccess: null
      }, options);

      const overlay = document.getElementById('smsGatewayModalOverlay');
      if (overlay) {
        overlay.style.setProperty('display', 'flex', 'important');
        overlay.classList.add('sms-modal-open');
      }

      const labelEl = document.getElementById('smsModalProvinciaLabel');
      const labelText = _currentOptions.provinciaLabel || _currentOptions.slug || (typeof window.provincia !== 'undefined' ? window.provincia : '') || '';
      if (labelEl) labelEl.textContent = labelText;

      const form = document.getElementById('smsModalForm');
      if (form) form.reset();

      const resEl = document.getElementById('smsModalTestResult');
      if (resEl) resEl.style.display = 'none';

      const phoneEl = document.getElementById('smsModalTestPhone');
      if (phoneEl) phoneEl.value = '';

      const enabledCb = document.getElementById('smsModalEnabled');
      const enabledLbl = document.getElementById('smsModalEnabledLabel');
      if (enabledCb) {
        enabledCb.onchange = function () {
          if (enabledLbl) {
            enabledLbl.textContent = this.checked ? 'Abilitato' : 'Disabilitato';
            enabledLbl.style.color = this.checked ? '#10b981' : 'var(--text-muted)';
          }
        };
      }

      let provValue = 'android_sms_gateway';
      const getUrl = _currentOptions.mode === 'admin'
        ? `/api/provinces/${_currentOptions.slug}/sms-settings`
        : '/api/distinte/sms-settings';

      try {
        const resp = await fetch(getUrl);
        const res = await resp.json();
        const data = res.sms_settings || (res.provider || res.endpoint_url !== undefined ? res : null);

        if (data) {
          if (enabledCb) {
            enabledCb.checked = data.enabled !== false;
            if (enabledLbl) {
              enabledLbl.textContent = enabledCb.checked ? 'Abilitato' : 'Disabilitato';
              enabledLbl.style.color = enabledCb.checked ? '#10b981' : 'var(--text-muted)';
            }
          }

          if (data.provider) provValue = data.provider;
          if (data.endpoint_url) document.getElementById('smsModalEndpointUrl').value = data.endpoint_url;
          if (data.auth_type) document.getElementById('smsModalAuthType').value = data.auth_type;
          if (data.username) document.getElementById('smsModalUsername').value = data.username;
          if (data.header_name) document.getElementById('smsModalHeaderName').value = data.header_name;
          if (data.rate_limit_delay !== undefined) document.getElementById('smsModalRateLimitDelay').value = data.rate_limit_delay;
          if (data.timeout_seconds !== undefined) document.getElementById('smsModalTimeoutSeconds').value = data.timeout_seconds;

          const passEl = document.getElementById('smsModalPassword');
          if (passEl) {
            passEl.placeholder = data.password_set
              ? '(Impostata - lascia vuoto per non modificare)'
              : 'Password gateway';
          }

          const keyEl = document.getElementById('smsModalApiKey');
          if (keyEl) {
            keyEl.placeholder = data.api_key_set
              ? '(Impostata - lascia vuoto per non modificare)'
              : '(Lascia vuoto per non modificare)';
          }
        }
      } catch (err) {
        console.warn('[SmsGatewayModal] Impossibile caricare impostazioni correnti:', err);
      }

      this.selezionaProvider(provValue);
      this.onAuthTypeChange();
      this.checkPrivateIp(document.getElementById('smsModalEndpointUrl').value);

      if (overlay) overlay.classList.add('sms-modal-open');
    },

    close() {
      const overlay = document.getElementById('smsGatewayModalOverlay');
      if (overlay) {
        overlay.style.setProperty('display', 'none', 'important');
        overlay.classList.remove('sms-modal-open');
      }
    },

    selezionaProvider(providerKey) {
      const pInput = document.getElementById('smsModalProvider');
      if (pInput) pInput.value = providerKey;

      const cardAndroid = document.getElementById('smsModalCard_android');
      const cardGeneric = document.getElementById('smsModalCard_generic');

      if (cardAndroid && cardGeneric) {
        if (providerKey === 'generic_webhook') {
          cardGeneric.style.border = '2px solid var(--accent)';
          cardGeneric.style.background = 'rgba(var(--accent-rgb), 0.12)';
          cardAndroid.style.border = '1px solid var(--border)';
          cardAndroid.style.background = 'var(--surface)';
        } else {
          cardAndroid.style.border = '2px solid var(--accent)';
          cardAndroid.style.background = 'rgba(var(--accent-rgb), 0.12)';
          cardGeneric.style.border = '1px solid var(--border)';
          cardGeneric.style.background = 'var(--surface)';
        }
      }

      this.aggiornaGuida(providerKey);
    },

    aggiornaGuida(providerKey) {
      const guide = document.getElementById('smsModalGuideBox');
      if (!guide) return;

      guide.style.borderLeftColor = 'var(--accent)';
      if (providerKey === 'generic_webhook') {
        guide.innerHTML = `
          <div style="font-weight:700; color:var(--text-primary); margin-bottom:6px; font-size:0.9rem;">
            <i class="fas fa-globe"></i> Modalità Webhook HTTP generico:
          </div>
          <div style="color:var(--text-secondary); margin-bottom:6px;">
            Invia i messaggi SMS effettuando chiamate HTTP POST JSON verso un servizio o gateway compatibile:
          </div>
          <ol style="margin-left:18px; margin-bottom:0; padding-left:0; color:var(--text-primary); line-height:1.5;">
            <li>Inserisci l'URL dell'endpoint webhook (es. aggregatore SMS aziendale o script server).</li>
            <li>Imposta il tipo di autenticazione richiesto (Nessuna, Basic Auth con User/Pass, Bearer Token o Header personalizzato).</li>
            <li>Il payload inviato conterrà i campi <code>recipient</code>, <code>phone</code>, <code>message</code> e <code>text</code>.</li>
          </ol>
        `;
      } else {
        guide.innerHTML = `
          <div style="font-weight:700; color:var(--accent); margin-bottom:6px; font-size:0.9rem;">
            <i class="fas fa-mobile-alt"></i> Modalità Android SMS Gateway (SIM Aziendale):
          </div>
          <div style="color:var(--text-secondary); margin-bottom:6px;">
            Invia SMS direttamente dallo smartphone aziendale, scalando dal piano dei 1000 SMS mensili:
          </div>
          <ol style="margin-left:18px; margin-bottom:0; padding-left:0; color:var(--text-primary); line-height:1.5;">
            <li>Installa sullo smartphone l'app gratuita <strong>SMS Gateway</strong> (di <em>capcom6 / Anton Vlasov</em>, scaricabile dal Google Play Store o GitHub).</li>
            <li><strong>Per server remoto / VPS (Consigliato):</strong> Nell'app tocca <strong>Cloud Server</strong> per connettere il telefono a costo zero. Inserisci come URL <code>https://api.sms-gate.app/3rdparty/v1/message</code> e seleziona <strong>Basic Auth</strong> inserendo Username e Password generati dall'app. Il telefono riceverà i messaggi anche fuori ufficio o sotto 4G/5G!</li>
            <li><strong>Per rete locale (stesso Wi-Fi):</strong> Avvia il <strong>Server Locale</strong> nell'app e inserisci come URL <code>http://[IP-DEL-TELEFONO]:8080/message</code>.</li>
          </ol>
        `;
      }
    },

    onAuthTypeChange() {
      const type = document.getElementById('smsModalAuthType')?.value;
      const basicRow = document.getElementById('smsModalAuthBasicRow');
      const tokenRow = document.getElementById('smsModalAuthTokenRow');
      const headerGroup = document.getElementById('smsModalHeaderNameGroup');
      const tokenLabel = document.getElementById('smsModalTokenLabel');

      if (basicRow) basicRow.style.display = (type === 'basic') ? 'flex' : 'none';
      if (tokenRow) tokenRow.style.display = (type === 'bearer' || type === 'api_key') ? 'flex' : 'none';
      if (headerGroup) headerGroup.style.display = (type === 'api_key') ? 'block' : 'none';
      if (tokenLabel) tokenLabel.textContent = (type === 'bearer') ? 'Bearer Token' : 'API Key';
    },

    impostaEndpointConsigliato() {
      const prov = document.getElementById('smsModalProvider')?.value;
      const ep = document.getElementById('smsModalEndpointUrl');
      const auth = document.getElementById('smsModalAuthType');

      if (!ep || !auth) return;

      if (prov === 'generic_webhook') {
        ep.value = '';
        auth.value = 'none';
        this.onAuthTypeChange();
        this.checkPrivateIp('');
        _notify("Inserisci l'URL del tuo webhook HTTP", 'info');
      } else {
        ep.value = 'https://api.sms-gate.app/3rdparty/v1/message';
        auth.value = 'basic';
        this.onAuthTypeChange();
        this.checkPrivateIp(ep.value);
        _notify('Impostato endpoint consigliato Cloud Android (https://api.sms-gate.app/3rdparty/v1/message)', 'info');
      }
    },

    checkPrivateIp(url) {
      const el = document.getElementById('smsModalPrivateIpWarning');
      if (!el) return;
      const val = (url || '').trim().toLowerCase();
      const isPrivate = /^(https?:\/\/)?(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|127\.|localhost)/.test(val);
      el.style.display = isPrivate ? 'block' : 'none';
    },

    async save(e) {
      if (e && e.preventDefault) e.preventDefault();
      const form = document.getElementById('smsModalForm');
      if (!form) return;

      const payload = {
        enabled: document.getElementById('smsModalEnabled').checked,
        provider: document.getElementById('smsModalProvider').value,
        endpoint_url: document.getElementById('smsModalEndpointUrl').value.trim(),
        auth_type: document.getElementById('smsModalAuthType').value,
        username: document.getElementById('smsModalUsername').value.trim(),
        header_name: document.getElementById('smsModalHeaderName').value.trim() || 'X-API-Key',
        rate_limit_delay: parseInt(document.getElementById('smsModalRateLimitDelay').value, 10) || 4,
        timeout_seconds: parseInt(document.getElementById('smsModalTimeoutSeconds').value, 10) || 15
      };

      const passVal = document.getElementById('smsModalPassword').value;
      if (passVal) payload.password = passVal;

      const keyVal = document.getElementById('smsModalApiKey').value;
      if (keyVal) payload.api_key = keyVal;

      const saveUrl = _currentOptions.mode === 'admin'
        ? `/api/provinces/${_currentOptions.slug}/sms-settings`
        : '/api/distinte/sms-settings';

      try {
        const resp = await fetch(saveUrl, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': _getCsrfToken()
          },
          body: JSON.stringify(payload)
        });
        const res = await resp.json();
        if (res.ok) {
          _notify('Impostazioni SMS Gateway salvate con successo!', 'success');
          this.close();
          if (typeof _currentOptions.onSaveSuccess === 'function') {
            await _currentOptions.onSaveSuccess(res);
          }
        } else {
          _notify('Errore salvataggio SMS Gateway: ' + (res.error || res.message || 'Errore server'), 'error');
        }
      } catch (err) {
        _notify('Errore comunicazione server: ' + err.message, 'error');
      }
    },

    async sendTest() {
      const phoneInput = document.getElementById('smsModalTestPhone');
      const testPhone = (phoneInput?.value || '').trim();
      const resEl = document.getElementById('smsModalTestResult');
      const btn = document.getElementById('smsModalBtnTest');

      if (!testPhone) {
        _notify('Inserisci un numero di cellulare per il test', 'warning');
        return;
      }

      if (btn) btn.disabled = true;
      const oldHtml = btn ? btn.innerHTML : '';
      if (btn) btn.innerHTML = '⏳ Invio test...';

      if (resEl) {
        resEl.style.display = 'block';
        resEl.style.background = 'rgba(59, 130, 246, 0.15)';
        resEl.style.color = '#3b82f6';
        resEl.style.border = '1px solid rgba(59, 130, 246, 0.3)';
        resEl.innerHTML = `Invio SMS di test verso <strong>${_escapeHtml(testPhone)}</strong> in corso...`;
      }

      const currentSettings = {
        enabled: document.getElementById('smsModalEnabled').checked,
        provider: document.getElementById('smsModalProvider').value,
        endpoint_url: document.getElementById('smsModalEndpointUrl').value.trim(),
        auth_type: document.getElementById('smsModalAuthType').value,
        username: document.getElementById('smsModalUsername').value.trim(),
        header_name: document.getElementById('smsModalHeaderName').value.trim() || 'X-API-Key',
        rate_limit_delay: parseInt(document.getElementById('smsModalRateLimitDelay').value, 10) || 4,
        timeout_seconds: parseInt(document.getElementById('smsModalTimeoutSeconds').value, 10) || 15
      };

      const passVal = document.getElementById('smsModalPassword').value;
      if (passVal) currentSettings.password = passVal;

      const keyVal = document.getElementById('smsModalApiKey').value;
      if (keyVal) currentSettings.api_key = keyVal;

      const payload = {
        test_phone: testPhone,
        phone: testPhone,
        settings: currentSettings
      };

      const testUrl = _currentOptions.mode === 'admin'
        ? `/api/provinces/${_currentOptions.slug}/sms-test`
        : '/api/distinte/sms-test';

      try {
        const resp = await fetch(testUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': _getCsrfToken()
          },
          body: JSON.stringify(payload)
        });
        const res = await resp.json();

        if (res.ok) {
          if (resEl) {
            resEl.style.background = 'rgba(16, 185, 129, 0.15)';
            resEl.style.border = '1px solid #10b981';
            resEl.style.color = '#10b981';
            resEl.innerHTML = `✅ ${_escapeHtml(res.message || 'SMS di prova inviato con successo!')}`;
          }
          _notify('SMS di prova inviato con successo!', 'success');
        } else {
          if (resEl) {
            resEl.style.background = 'rgba(239, 68, 68, 0.15)';
            resEl.style.border = '1px solid #ef4444';
            resEl.style.color = '#ef4444';
            resEl.innerHTML = `❌ ${_escapeHtml(res.error || res.message || 'Invio fallito')}`;
          }
          _notify('Test invio SMS fallito', 'error');
        }
      } catch (err) {
        if (resEl) {
          resEl.style.background = 'rgba(239, 68, 68, 0.15)';
          resEl.style.border = '1px solid #ef4444';
          resEl.style.color = '#ef4444';
          resEl.innerHTML = `❌ Errore comunicazione: ${_escapeHtml(err.message)}`;
        }
        _notify('Errore comunicazione server: ' + err.message, 'error');
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = oldHtml || '📱 Invia Prova';
        }
      }
    }
  };

  // Esporta componente su window
  window.SmsGatewayModal = SmsGatewayModal;

  // Wrapper retrocompatibili globali
  window.apriSmsSettings = function (slug) {
    SmsGatewayModal.open({ mode: 'admin', slug: slug });
  };
  window.chiudiSmsSettings = function () {
    SmsGatewayModal.close();
  };
  window.apriDistinteSmsConfigModal = function () {
    SmsGatewayModal.open({
      mode: 'distinte',
      onSaveSuccess: async function () {
        if (typeof window.caricaStatoSmsGateway === 'function') {
          await window.caricaStatoSmsGateway();
        }
      }
    });
  };
  window.chiudiDistinteSmsConfigModal = function () {
    SmsGatewayModal.close();
  };
  window.selezionaSmsProvider = function (p) {
    SmsGatewayModal.selezionaProvider(p);
  };
  window.selezionaDistinteSmsProvider = function (p) {
    SmsGatewayModal.selezionaProvider(p);
  };
  window.impostaEndpointConsigliato = function () {
    SmsGatewayModal.impostaEndpointConsigliato();
  };
  window.impostaDistinteEndpointConsigliato = function () {
    SmsGatewayModal.impostaEndpointConsigliato();
  };

  // Chiudi premendo Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      const overlay = document.getElementById('smsGatewayModalOverlay');
      if (overlay && (overlay.classList.contains('sms-modal-open') || overlay.style.display === 'flex')) {
        SmsGatewayModal.close();
      }
    }
  });

  // Iniezione automatica al caricamento della pagina
  _injectStyles();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _injectModalDom);
  } else {
    _injectModalDom();
  }
})();
