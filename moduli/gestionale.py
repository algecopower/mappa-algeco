# -*- coding: utf-8 -*-
"""
Modulo per generare il JavaScript del gestionale nella mappa.
Contiene le funzioni per editing popup e upload BOXBEAT.
"""
import json
from config import CAMPI_MODIFICABILI, OPERATORI_DICT, PROVINCIA_ATTIVA


def genera_css_gestionale():
    """Genera CSS per i componenti del gestionale."""
    return '''
        /* ========================================
           GESTIONALE - Stili per editing popup
           ======================================== */
        
        /* Pulsante modifica nel popup */
        .popup-edit-toggle {
            display: flex;
            justify-content: flex-end;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid var(--border);
        }
        
        .btn-toggle-edit {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 8px 14px;
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 0.85em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn-toggle-edit:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(52, 152, 219, 0.3);
        }
        
        .btn-toggle-edit.active {
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
        }
        
        /* Form editabile nel popup - nascosto di default */
        .popup-edit-form {
            display: none;
            margin-top: 15px;
            padding-top: 15px;
            border-top: 2px solid var(--accent);
            animation: slideDown 0.2s ease;
        }
        
        .popup-edit-form.visible {
            display: block;
        }
        
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes modalSlideUp {
            from { opacity: 0; transform: translateY(24px) scale(0.97); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        
        .popup-edit-title {
            font-weight: 600;
            color: var(--accent);
            margin-bottom: 12px;
            font-size: 0.9em;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .popup-edit-field {
            margin-bottom: 10px;
        }
        
        .popup-edit-label {
            display: block;
            font-size: 0.75em;
            color: var(--text-secondary);
            margin-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .popup-edit-input {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .popup-edit-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(233, 69, 96, 0.2);
        }
        
        .popup-edit-input:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .popup-edit-select {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.9em;
            cursor: pointer;
        }
        
        .popup-edit-textarea {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.9em;
            resize: vertical;
            min-height: 60px;
        }
        
        .popup-edit-actions {
            display: flex;
            gap: 8px;
            margin-top: 15px;
        }
        
        .btn-save-edit {
            flex: 1;
            padding: 10px 16px;
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .btn-save-edit:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(39, 174, 96, 0.3);
        }
        
        .btn-save-edit:disabled {
            background: #95a5a6;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .btn-cancel-edit {
            padding: 10px 16px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        
        .btn-cancel-edit:hover {
            border-color: var(--accent);
        }
        
        .btn-appuntamento {
            width: 100%;
            padding: 10px 16px;
            background: linear-gradient(135deg, #f39c12 0%, #f1c40f 100%);
            color: #333;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            font-size: 0.9em;
            margin-top: 8px;
            transition: all 0.2s;
        }
        
        .btn-appuntamento:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(243, 156, 18, 0.3);
        }
        
        /* Stato salvataggio - Toast fisso in alto a destra */
        .edit-status {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10001;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 0.9em;
            font-weight: 500;
            display: none;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            animation: slideInRight 0.3s ease;
            max-width: 300px;
        }
        
        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        .edit-status.success {
            display: block;
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
        }
        
        .edit-status.error {
            display: block;
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
            color: white;
            border: none;
        }
        
        .edit-status.loading {
            display: block;
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            border: none;
        }
        
        /* ========================================
           BOXBEAT UPLOAD
           ======================================== */
        
        .BOXBEAT-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--modal-overlay-bg);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
        }
        
        .BOXBEAT-modal.visible {
            display: flex;
        }
        
        .BOXBEAT-content {
            background: var(--panel-bg-strong);
            backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            -webkit-backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            border-radius: 16px;
            padding: 24px;
            width: 90%;
            max-width: 550px;
            max-height: 90vh;
            overflow-y: auto;
            box-shadow: var(--panel-shadow);
            border: var(--panel-border);
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        .BOXBEAT-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .BOXBEAT-title {
            font-size: 1.3em;
            font-weight: 700;
            color: var(--accent);
        }
        
        .btn-close-modal {
            background: none;
            border: none;
            font-size: 1.5em;
            color: var(--text-secondary);
            cursor: pointer;
        }
        
        .BOXBEAT-dropzone {
            border: 2px dashed var(--border);
            border-radius: 12px;
            padding: 40px 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .BOXBEAT-dropzone:hover,
        .BOXBEAT-dropzone.dragover {
            border-color: var(--accent);
            background: rgba(233, 69, 96, 0.1);
        }
        
        .BOXBEAT-dropzone-icon {
            font-size: 3em;
            margin-bottom: 15px;
        }
        
        .BOXBEAT-dropzone-text {
            color: var(--text-secondary);
            margin-bottom: 10px;
        }
        
        .BOXBEAT-dropzone-subtext {
            color: var(--text-muted);
            font-size: 0.85em;
        }
        
        .BOXBEAT-progress {
            display: none;
            margin-top: 20px;
        }
        
        .BOXBEAT-progress.visible {
            display: block;
        }
        
        .BOXBEAT-progress-bar {
            height: 8px;
            background: var(--bg-tertiary);
            border-radius: 4px;
            overflow: hidden;
        }
        
        .BOXBEAT-progress-fill {
            height: 100%;
            background: var(--accent);
            width: 0%;
            transition: width 0.3s;
        }
        
        .BOXBEAT-progress-text {
            margin-top: 10px;
            text-align: center;
            font-size: 0.9em;
            color: var(--text-secondary);
        }
        
        .BOXBEAT-result {
            display: none;
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            overflow-wrap: break-word;
            word-break: break-word;
        }
        
        .BOXBEAT-result.visible {
            display: block;
        }
        
        .BOXBEAT-result.success {
            background: rgba(39, 174, 96, 0.2);
            border: 1px solid rgba(39, 174, 96, 0.3);
        }
        
        .BOXBEAT-result.error {
            background: rgba(231, 76, 60, 0.2);
            border: 1px solid rgba(231, 76, 60, 0.3);
        }
        
        /* ========================================
           PULSANTE GESTIONALE NELLA SIDEBAR
           ======================================== */
        
        .gestionale-buttons {
            display: flex;
            gap: 8px;
            padding: 10px 15px;
            border-top: 1px solid var(--border);
            background: var(--bg-tertiary);
        }
        
        .btn-gestionale {
            flex: 1;
            padding: 10px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        
        .btn-BOXBEAT-upload {
            background: linear-gradient(135deg, #9b59b6 0%, #8e44ad 100%);
            color: white;
        }
        
        .btn-BOXBEAT-upload:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(155, 89, 182, 0.3);
        }
        
        .btn-sigilli {
            background: linear-gradient(135deg, #f39c12 0%, #e67e22 100%);
            color: white;
        }
        
        .btn-sigilli:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(243, 156, 18, 0.3);
        }
        
        .btn-distinte {
            background: linear-gradient(135deg, #25D366 0%, #128C7E 100%);
            color: white;
        }
        
        .btn-distinte:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(37, 211, 102, 0.3);
        }
        
        /* ========================================
           VISTA SIGILLI FULLSCREEN
           ======================================== */
        
        .sigilli-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--panel-bg-strong);
            backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            -webkit-backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            z-index: 10002;
            overflow-y: auto;
        }
        
        .sigilli-modal.visible {
            display: block;
        }
        
        .sigilli-container {
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
        }
        
        /* Tab per switch Sigilli/Matricole */
        .sigilli-tabs {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid var(--border);
        }
        
        .sigilli-tab {
            flex: 1;
            padding: 12px 20px;
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-secondary);
            font-size: 0.95em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .sigilli-tab:hover {
            background: var(--bg-secondary);
        }
        
        .sigilli-tab.active {
            background: linear-gradient(135deg, #f39c12 0%, #e67e22 100%);
            color: white;
        }
        
        .sigilli-tab.active.matricole {
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        }
        
        .sigilli-tab-count {
            background: rgba(255,255,255,0.2);
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8em;
        }
        
        .sigilli-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 0;
            margin-bottom: 20px;
            border-bottom: 2px solid #f39c12;
            position: sticky;
            top: 0;
            background: var(--bg-primary);
            z-index: 10;
        }
        
        .sigilli-header.matricole {
            border-bottom-color: #3498db;
        }
        
        .sigilli-title {
            font-size: 1.4em;
            font-weight: 700;
            color: #f39c12;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .sigilli-title.matricole {
            color: #3498db;
        }
        
        .sigilli-count {
            font-size: 0.7em;
            color: var(--text-secondary);
            font-weight: 400;
            margin-left: 10px;
        }
        
        .sigilli-loading {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        
        .sigilli-loading-spinner {
            font-size: 2em;
            animation: spin 1s linear infinite;
            display: block;
            margin-bottom: 15px;
        }
        
        @keyframes spin {
            from { transform: rotate(0deg); }
            to { transform: rotate(360deg); }
        }
        
        .sigilli-empty {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        
        .sigilli-empty-icon {
            font-size: 3em;
            margin-bottom: 15px;
        }
        
        /* Filtri sigilli/matricole */
        .sigilli-filter-row {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 12px 15px;
            background: var(--bg-secondary);
            border-radius: 10px;
            margin-bottom: 15px;
            flex-wrap: wrap;
        }
        
        .sigilli-search-input {
            flex: 1;
            min-width: 180px;
            padding: 8px 12px;
            border: 1px solid var(--border);
            background: var(--bg-primary);
            color: var(--text-primary);
            border-radius: 8px;
            font-size: 0.85em;
            outline: none;
            transition: border-color 0.2s;
        }
        .sigilli-search-input:focus {
            border-color: #f39c12;
        }
        .sigilli-search-input::placeholder {
            color: var(--text-muted);
        }
        
        .sigilli-filter-label {
            color: var(--text-muted);
            font-size: 0.9em;
            font-weight: 500;
        }
        
        .sigilli-filter-btn {
            padding: 8px 14px;
            border: 1px solid var(--border);
            background: var(--bg-primary);
            color: var(--text-primary);
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
        }
        
        .sigilli-filter-btn:hover {
            border-color: #f39c12;
            background: rgba(243, 156, 18, 0.1);
        }
        
        .sigilli-filter-btn.active {
            background: linear-gradient(135deg, #f39c12 0%, #e67e22 100%);
            color: white;
            border-color: #f39c12;
        }
        
        .sigilli-filter-select {
            padding: 8px 12px;
            border: 1px solid var(--border);
            background: var(--bg-primary);
            color: var(--text-primary);
            border-radius: 8px;
            font-size: 0.9em;
            min-width: 200px;
            cursor: pointer;
        }
        
        .sigilli-filter-select:focus {
            outline: none;
            border-color: #3498db;
        }
        
        .sigilli-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .sigilli-card {
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 15px;
            transition: all 0.2s;
        }
        
        .sigilli-card:hover {
            border-color: #f39c12;
            box-shadow: 0 4px 12px rgba(243, 156, 18, 0.1);
        }
        
        .sigilli-card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 10px;
        }
        
        .sigilli-card-cliente {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 1em;
        }
        
        .sigilli-card-badges {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        
        .sigilli-badge {
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 0.7em;
            font-weight: 600;
        }
        
        .sigilli-badge-data {
            background: rgba(243, 156, 18, 0.2);
            color: #f39c12;
        }
        
        .sigilli-badge-operatore {
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            color: var(--text-primary);
        }
        
        .sigilli-badge-tipo {
            background: rgba(155, 89, 182, 0.2);
            color: #9b59b6;
        }
        
        .sigilli-badge-tipo.fotovoltaico {
            background: rgba(46, 204, 113, 0.2);
            color: #27ae60;
        }
        
        .sigilli-card-info {
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            font-size: 0.85em;
            color: var(--text-secondary);
            margin-bottom: 12px;
        }
        
        .sigilli-card-info span {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        .sigilli-input-row {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .sigilli-input {
            flex: 1;
            padding: 10px 14px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.95em;
            transition: all 0.2s;
        }
        
        .sigilli-input:focus {
            outline: none;
            border-color: #f39c12;
            box-shadow: 0 0 0 3px rgba(243, 156, 18, 0.2);
        }
        
        .sigilli-input.matricola:focus {
            border-color: #3498db;
            box-shadow: 0 0 0 3px rgba(52, 152, 219, 0.2);
        }
        
        .sigilli-input::placeholder {
            color: var(--text-muted);
        }
        
        .btn-salva-sigillo {
            padding: 10px 20px;
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
            white-space: nowrap;
        }
        
        .btn-salva-sigillo:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(39, 174, 96, 0.3);
        }
        
        .btn-salva-sigillo:disabled {
            background: #95a5a6;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .btn-modifica-sigillo {
            padding: 10px 20px;
            background: linear-gradient(135deg, #e67e22 0%, #f39c12 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            font-size: 0.9em;
            transition: all 0.2s;
            white-space: nowrap;
        }
        
        .btn-modifica-sigillo:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(230, 126, 34, 0.3);
        }
        
        .sigilli-card-status {
            margin-top: 8px;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.85em;
            display: none;
        }
        
        .sigilli-card-status.visible {
            display: block;
        }
        
        .sigilli-card-status.success {
            background: rgba(39, 174, 96, 0.2);
            color: #27ae60;
        }
        
        .sigilli-card-status.error {
            background: rgba(231, 76, 60, 0.2);
            color: #e74c3c;
        }
        
        .sigilli-card-status.loading {
            background: rgba(52, 152, 219, 0.2);
            color: #3498db;
        }
        
        .sigilli-card.completato {
            opacity: 0.6;
            background: rgba(39, 174, 96, 0.1);
            border-color: rgba(39, 174, 96, 0.3);
        }

        .sigilli-card.completato .sigilli-input {
            background: rgba(39, 174, 96, 0.1);
            border-color: rgba(39, 174, 96, 0.3);
        }

        /* Smart Test toggle (solo matricole) */
        .smart-test-row {
            margin-top: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .smart-test-toggle {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            font-size: 0.9em;
            color: var(--text-secondary);
            user-select: none;
        }

        .smart-test-toggle input[type="checkbox"] {
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: #e74c3c;
        }

        .smart-test-toggle.checked {
            color: #e74c3c;
            font-weight: 600;
        }

        .smart-test-spiegazione {
            display: none;
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.9em;
            transition: all 0.2s;
        }

        .smart-test-spiegazione.visible {
            display: block;
        }

        .smart-test-spiegazione:focus {
            outline: none;
            border-color: #e74c3c;
            box-shadow: 0 0 0 3px rgba(231, 76, 60, 0.2);
        }

        .sigilli-card.completato .smart-test-toggle,
        .sigilli-card.completato .smart-test-spiegazione {
            pointer-events: none;
        }
        .sigilli-card.completato .smart-test-toggle input,
        .sigilli-card.completato .smart-test-spiegazione {
            opacity: 0.85;
        }

        /* Modale appuntamento */
        .appuntamento-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--modal-overlay-bg);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 10001;
            align-items: center;
            justify-content: center;
        }
        
        .appuntamento-modal.visible {
            display: flex;
        }
        
        .appuntamento-content {
            background: var(--panel-bg-strong);
            backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            -webkit-backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            border-radius: 16px;
            padding: 24px;
            width: 90%;
            max-width: 350px;
            box-shadow: var(--panel-shadow);
            border: var(--panel-border);
            animation: modalSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        .time-picker-group {
            display: flex;
            gap: 10px;
            align-items: center;
            margin: 20px 0;
        }
        
        .time-input {
            flex: 1;
            padding: 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 1.1em;
            text-align: center;
        }
        
        .time-separator {
            color: var(--text-secondary);
            font-weight: 600;
        }
        
        /* ========================================
           VISTA FULLSCREEN MODIFICA
           ======================================== */
        
        .fullscreen-edit-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: var(--panel-bg-strong);
            backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            -webkit-backdrop-filter: blur(var(--panel-blur)) saturate(var(--panel-saturate));
            z-index: 10002;
            overflow-y: auto;
        }
        
        .fullscreen-edit-modal.visible {
            display: block;
        }
        
        .fullscreen-edit-container {
            max-width: 700px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .fullscreen-edit-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 0;
            margin-bottom: 20px;
            border-bottom: 2px solid var(--accent);
            position: sticky;
            top: 0;
            background: var(--bg-primary);
            z-index: 10;
        }
        
        .fullscreen-edit-title {
            font-size: 1.4em;
            font-weight: 700;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .fullscreen-edit-eneltel {
            font-size: 0.7em;
            color: var(--text-secondary);
            font-weight: 400;
        }
        
        .btn-close-fullscreen {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 20px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 1em;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn-close-fullscreen:hover {
            border-color: var(--accent);
            background: var(--bg-secondary);
        }
        
        /* Info card stile sidebar */
        .fullscreen-info-card {
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .fullscreen-info-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 15px;
        }
        
        .fullscreen-info-cliente {
            font-size: 1.2em;
            font-weight: 700;
            color: var(--text-primary);
        }
        
        .fullscreen-info-badges {
            display: flex;
            gap: 8px;
        }
        
        .fullscreen-badge {
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75em;
            font-weight: 600;
        }
        
        .fullscreen-badge-tappa {
            background: var(--accent);
            color: white;
        }
        
        .fullscreen-badge-tecnico {
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            color: var(--text-primary);
        }
        
        .fullscreen-info-row {
            display: flex;
            padding: 8px 0;
            border-bottom: 1px solid var(--border);
        }
        
        .fullscreen-info-row:last-child {
            border-bottom: none;
        }
        
        .fullscreen-info-label {
            width: 140px;
            font-size: 0.8em;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            flex-shrink: 0;
        }
        
        .fullscreen-info-value {
            flex: 1;
            color: var(--text-primary);
            font-size: 0.95em;
        }
        
        /* Form modifica fullscreen */
        .fullscreen-edit-section {
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .fullscreen-section-title {
            font-size: 1.1em;
            font-weight: 600;
            color: var(--accent);
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .fullscreen-edit-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
        }
        
        @media (max-width: 600px) {
            .fullscreen-edit-grid {
                grid-template-columns: 1fr;
            }
            .fullscreen-edit-container { padding: 12px; }
            .fullscreen-edit-title { font-size: 1.1em; }
            .fullscreen-edit-eneltel { font-size: 0.65em; }
            .fullscreen-edit-footer { flex-direction: column; padding: 14px; bottom: 10px; }
            .btn-fullscreen-save, .btn-fullscreen-cancel { width: 100%; padding: 12px 16px; font-size: 0.95em; }
            .fullscreen-field-input,
            .fullscreen-field-textarea,
            .fullscreen-field-select { padding: 10px 12px; font-size: 0.9em; }
        }
        
        .fullscreen-field {
            display: flex;
            flex-direction: column;
        }
        
        .fullscreen-field.full-width {
            grid-column: 1 / -1;
        }
        
        .fullscreen-field-label {
            font-size: 0.75em;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }
        
        .fullscreen-field-input {
            padding: 12px 14px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.95em;
            transition: all 0.2s;
        }
        
        .fullscreen-field-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(233, 69, 96, 0.2);
        }
        
        .fullscreen-field-textarea {
            padding: 12px 14px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.95em;
            resize: vertical;
            min-height: 80px;
            font-family: inherit;
        }
        
        .fullscreen-field-textarea:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(233, 69, 96, 0.2);
        }
        
        .fullscreen-field-select {
            padding: 12px 14px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 0.95em;
            cursor: pointer;
        }
        
        /* Nominativo protetto */
        .nominativo-protected-wrapper {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .nominativo-original {
            padding: 10px 14px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 0.95em;
            font-weight: 600;
            opacity: 0.8;
            user-select: none;
        }
        .nominativo-append-input,
        .popup-nominativo-append-input {
            border: 1px dashed var(--accent) !important;
            background: var(--bg-tertiary) !important;
        }
        .nominativo-append-input::placeholder,
        .popup-nominativo-append-input::placeholder {
            color: var(--accent);
            opacity: 0.6;
            font-style: italic;
        }
        
        /* Bottone aggiungi nota */
        .btn-aggiungi-nota {
            margin-top: 6px;
            padding: 8px 14px;
            border: 1px dashed var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s;
        }
        .btn-aggiungi-nota:hover {
            background: var(--accent);
            color: white;
            border-style: solid;
        }
        
        /* Footer azioni */
        .fullscreen-edit-footer {
            display: flex;
            gap: 12px;
            padding: 20px;
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            position: sticky;
            bottom: 20px;
        }
        
        .btn-fullscreen-save {
            flex: 1;
            padding: 14px 24px;
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-weight: 700;
            font-size: 1em;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .btn-fullscreen-save:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(39, 174, 96, 0.4);
        }
        
        .btn-fullscreen-save:disabled {
            background: #95a5a6;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .btn-fullscreen-appuntamento {
            padding: 14px 24px;
            background: linear-gradient(135deg, #f39c12 0%, #f1c40f 100%);
            color: #333;
            border: none;
            border-radius: 10px;
            font-weight: 700;
            font-size: 1em;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .btn-fullscreen-appuntamento:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(243, 156, 18, 0.4);
        }
        
        .btn-fullscreen-cancel {
            padding: 14px 24px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 10px;
            font-weight: 600;
            font-size: 1em;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn-fullscreen-cancel:hover {
            border-color: var(--accent);
        }
        
        /* Sezione appuntamento inline */
        .fullscreen-appuntamento-section {
            background: var(--bg-secondary);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .appuntamento-inline-row {
            display: flex;
            gap: 15px;
            align-items: flex-end;
            flex-wrap: wrap;
        }
        
        .appuntamento-time-group {
            display: flex;
            gap: 8px;
            align-items: center;
        }
        
        .appuntamento-time-input {
            width: 110px;
            padding: 12px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-tertiary);
            color: var(--text-primary);
            font-size: 1em;
            text-align: center;
        }
        
        .appuntamento-time-input:focus {
            outline: none;
            border-color: #f39c12;
            box-shadow: 0 0 0 3px rgba(243, 156, 18, 0.2);
        }
        
        .appuntamento-time-sep {
            color: var(--text-secondary);
            font-weight: 600;
        }
        
        .btn-fissa-appuntamento {
            padding: 12px 20px;
            background: linear-gradient(135deg, #f39c12 0%, #f1c40f 100%);
            color: #333;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.95em;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        
        .btn-fissa-appuntamento:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(243, 156, 18, 0.4);
        }
        
        .btn-fissa-appuntamento:disabled {
            background: #95a5a6;
            cursor: not-allowed;
            transform: none;
        }
        
        .appuntamento-status {
            margin-top: 10px;
            padding: 10px;
            border-radius: 6px;
            font-size: 0.9em;
            display: none;
        }
        
        .appuntamento-status.visible {
            display: block;
        }
        
        .appuntamento-status.success {
            background: rgba(39, 174, 96, 0.15);
            color: #27ae60;
        }
        
        .appuntamento-status.error {
            background: rgba(231, 76, 60, 0.15);
            color: #e74c3c;
        }


        /* Status toast - fisso in alto a destra */
        .fullscreen-status {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 100001;
            padding: 14px 24px;
            border-radius: 10px;
            font-size: 1em;
            font-weight: 600;
            display: none;
            box-shadow: 0 6px 25px rgba(0, 0, 0, 0.35);
            animation: toastSlideIn 0.4s ease;
            max-width: 350px;
        }
        
        @keyframes toastSlideIn {
            from { transform: translateX(120%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        .fullscreen-status.visible {
            display: block;
        }
        
        .fullscreen-status.success {
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
        }
        
        .fullscreen-status.error {
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
            color: white;
            border: none;
        }
        
        .fullscreen-status.loading {
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            border: none;
        }
        
        /* ========================================
           BACKUP / RIPRISTINO
           ======================================== */
        .backup-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            margin-bottom: 8px;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            border-radius: 8px;
            transition: all 0.2s;
        }
        
        .backup-item:hover {
            border-color: var(--accent);
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .backup-item-info {
            flex: 1;
        }
        
        .backup-item-date {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 0.95em;
        }
        
        .backup-item-meta {
            font-size: 0.8em;
            color: var(--text-secondary);
            margin-top: 3px;
        }
        
        .backup-item-actions {
            display: flex;
            gap: 6px;
        }
        
        .backup-btn-excel {
            padding: 6px 14px;
            background: linear-gradient(135deg, #2980b9 0%, #3498db 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 0.8em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .backup-btn-excel:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(41, 128, 185, 0.3);
        }
        
        .backup-btn-ripristina {
            padding: 6px 14px;
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 0.8em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .backup-btn-ripristina:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(39, 174, 96, 0.3);
        }
        
        .backup-btn-elimina {
            padding: 6px 10px;
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 0.8em;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .backup-btn-elimina:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(231, 76, 60, 0.3);
        }
        
        .backup-empty {
            text-align: center;
            padding: 30px;
            color: var(--text-secondary);
            font-size: 0.9em;
        }

        /* ========================================
           ACCESS POINT (UPS-style) — sezione Modifica
           ======================================== */
        .access-point-section {
            border-left: 3px solid #3498db;
        }
        .access-point-status-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }
        .access-point-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
            color: white;
            background: #95a5a6;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .access-point-badge.osrm {
            background: linear-gradient(135deg, #2ecc71 0%, #27ae60 100%);
        }
        .access-point-badge.override {
            background: linear-gradient(135deg, #f39c12 0%, #e67e22 100%);
        }
        .access-point-badge.fallback {
            background: linear-gradient(135deg, #95a5a6 0%, #7f8c8d 100%);
        }
        .access-point-badge.no_civico {
            background: linear-gradient(135deg, #34495e 0%, #2c3e50 100%);
        }
        .access-point-road {
            color: var(--text-secondary);
            font-size: 0.9em;
            font-style: italic;
        }
        .access-point-map {
            width: 100%;
            height: 280px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg-tertiary);
            margin-bottom: 12px;
            cursor: crosshair;
        }
        .access-point-map .leaflet-container {
            border-radius: 10px;
            background: var(--bg-tertiary);
        }
        .access-point-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }
        .btn-access-save,
        .btn-access-snap,
        .btn-access-undo,
        .btn-access-reset {
            padding: 8px 14px;
            border: none;
            border-radius: 8px;
            font-size: 0.85em;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            color: white;
        }
        .btn-access-save {
            background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%);
        }
        .btn-access-save:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(39, 174, 96, 0.3);
        }
        .btn-access-snap {
            background: linear-gradient(135deg, #2980b9 0%, #3498db 100%);
        }
        .btn-access-snap:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(41, 128, 185, 0.3);
        }
        .btn-access-undo {
            background: linear-gradient(135deg, #e67e22 0%, #f39c12 100%);
        }
        .btn-access-undo:hover:not([disabled]) {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(230, 126, 34, 0.3);
        }
        .btn-access-undo[disabled] {
            background: linear-gradient(135deg, #bdc3c7 0%, #95a5a6 100%);
            cursor: not-allowed;
            opacity: 0.65;
        }
        .btn-access-reset {
            background: linear-gradient(135deg, #95a5a6 0%, #7f8c8d 100%);
        }
        .btn-access-reset:hover {
            transform: translateY(-1px);
            box-shadow: 0 3px 10px rgba(127, 140, 141, 0.3);
        }
        .access-point-status {
            margin-top: 10px;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.85em;
            display: none;
        }
        .access-point-status.visible {
            display: block;
        }
        .access-point-status.success {
            background: rgba(39, 174, 96, 0.15);
            color: #27ae60;
            border: 1px solid rgba(39, 174, 96, 0.3);
        }
        .access-point-status.error {
            background: rgba(231, 76, 60, 0.15);
            color: #e74c3c;
            border: 1px solid rgba(231, 76, 60, 0.3);
        }
        .access-point-status.loading {
            background: rgba(52, 152, 219, 0.15);
            color: #3498db;
            border: 1px solid rgba(52, 152, 219, 0.3);
        }

        /* iOS Safari mobile fix: previeni auto-zoom su focus input <16px */
        @media (max-width: 600px) {
            input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="color"]):not([type="file"]),
            select, textarea { font-size: 16px; }
        }
    '''


def genera_js_gestionale():
    """Genera JavaScript per le funzionalità del gestionale."""
    
    campi = CAMPI_MODIFICABILI.get(PROVINCIA_ATTIVA, {})
    campi_json = json.dumps(campi, ensure_ascii=False)
    operatori_json = json.dumps(OPERATORI_DICT, ensure_ascii=False)
    provincia = PROVINCIA_ATTIVA
    
    return f'''
        // ========================================
        // GESTIONALE - Funzioni editing popup
        // ========================================
        
        const campiModificabili = {campi_json};
        const listaOperatori = {operatori_json};
        const provinciaAttiva = "{provincia}";
        
        // URL API base (cambia in produzione)
        const API_BASE = window.location.origin;
        
        // Mappa labels amichevoli per i campi
        const campiLabels = {{
            'data_pian': 'DATA_PIAN',
            'note_app': 'NOTE',
            'nominativo': 'NOMINATIVO',
            'indirizzo': 'INDIRIZZO',
            'civico': 'N. CIVICO',
            'telefono': 'TELEFONO',
            'email': 'EMAIL',
            'posizione_mis': 'POSIZIONE',
            'operatore': 'OPERATORE',
            'smart_test': 'SMART TEST'
        }};
        
        // Opzioni per posizione misuratore
        const opzioniPosizioneMis = [
            {{ value: '0 - Non conosciuta', label: '0 - Non conosciuta' }},
            {{ value: '1 - Nell\\'appartamento', label: '1 - Nell\\'appartamento' }},
            {{ value: '2 - Centralizzato', label: '2 - Centralizzato' }},
            {{ value: '3 - Accessibile dall\\'esterno', label: '3 - Accessibile dall\\'esterno' }}
        ];
        
        // Variabile per tracciare l'eneltel corrente in modifica fullscreen
        let currentFullscreenEneltel = null;
        
        // ========================================
        // VISTA FULLSCREEN MODIFICA
        // ========================================
        
        function apriVistaModifica(eneltel) {{
            // Trova il contatore
            const contatore = contatori.find(c => c.eneltel === eneltel);
            if (!contatore) {{
                console.error('Contatore non trovato:', eneltel);
                return;
            }}
            
            currentFullscreenEneltel = eneltel;
            
            // Popola i dati
            document.getElementById('fullscreenEneltel').textContent = '(' + eneltel + ')';
            document.getElementById('fullscreenCliente').textContent = contatore.cliente || 'N/D';
            
            // Badge tappa - usa numeroTappa dal marker (corretto anche per date passate)
            const tappaBadge = document.getElementById('fullscreenTappaBadge');
            const markerTappa = markers.find(m => m.contatore && m.contatore.eneltel === eneltel);
            const numTappa = markerTappa ? markerTappa.numeroTappa : (contatore.ordine_tappa < 999 ? contatore.ordine_tappa : null);
            tappaBadge.textContent = '📍 Tappa ' + (numTappa || '-');
            
            // Badge tecnico
            const tecnicoBadge = document.getElementById('fullscreenTecnicoBadge');
            tecnicoBadge.textContent = typeof getDisplayName === 'function' ? getDisplayName(contatore.tecnico) : (contatore.tecnico || 'Non assegnato');
            
            // Popola info rows (dati di sola lettura)
            const infoRowsHtml = `
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">POD</div>
                    <div class="fullscreen-info-value">${{contatore.pod || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Eneltel</div>
                    <div class="fullscreen-info-value">${{contatore.eneltel || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Indirizzo</div>
                    <div class="fullscreen-info-value">${{contatore.via || ''}} ${{contatore.civico || ''}}, ${{contatore.comune || ''}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Telefono</div>
                    <div class="fullscreen-info-value">${{contatore.telefono || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Data</div>
                    <div class="fullscreen-info-value">${{formatDate(contatore.data) || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Ora Pianificata</div>
                    <div class="fullscreen-info-value">${{contatore.ora_pianificazione || contatore.orari_lettere || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Posizione</div>
                    <div class="fullscreen-info-value">${{contatore.note || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Note App</div>
                    <div class="fullscreen-info-value">${{contatore.note_app || '-'}}</div>
                </div>
            `;
            document.getElementById('fullscreenInfoRows').innerHTML = infoRowsHtml;
            
            // Genera i campi editabili
            generaCampiEditFullscreen(contatore);
            
            // Reset status
            const status = document.getElementById('fullscreenStatus');
            status.className = 'fullscreen-status';
            status.textContent = '';
            
            // Mostra modal
            document.getElementById('fullscreenEditModal').classList.add('visible');
            
            // Mostra/nascondi pulsante rimuovi appuntamento
            const btnRimuoviApp = document.getElementById('btnRimuoviAppuntamento');
            if (btnRimuoviApp) {{
                btnRimuoviApp.style.display = contatore.is_appuntamento ? 'inline-flex' : 'none';
            }}

            // Inizializza mini-mappa Access Point UPS-style
            try {{
                inizializzaAccessPointMap(contatore);
            }} catch (e) {{
                console.warn('AccessPoint init error:', e);
            }}

            // Chiudi il popup se aperto
            map.closePopup();
        }}
        
        function generaCampiEditFullscreen(contatore) {{
            let html = '';
            
            for (const [campoKey, colonnaSheet] of Object.entries(campiModificabili)) {{
                const label = campiLabels[campoKey] || campoKey;
                const valoreCurrent = getValoreContatore(contatore, campoKey, colonnaSheet);
                
                if (campoKey === 'operatore') {{
                    // Dropdown per operatore
                    let optionsHtml = '<option value="">-- Seleziona --</option>';
                    Object.entries(listaOperatori).forEach(([nome, mat]) => {{
                        const selected = valoreCurrent === mat || valoreCurrent === nome ? 'selected' : '';
                        optionsHtml += `<option value="${{mat}}" ${{selected}}>${{nome}}</option>`;
                    }});
                    html += `
                        <div class="fullscreen-field">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <select class="fullscreen-field-select fullscreen-edit-input-field" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">
                                ${{optionsHtml}}
                            </select>
                        </div>
                    `;
                }} else if (campoKey === 'posizione_mis') {{
                    // Dropdown per posizione misuratore
                    let optionsHtml = '<option value="">-- Seleziona --</option>';
                    opzioniPosizioneMis.forEach(opt => {{
                        const isMatch = matchPosizioneMis(valoreCurrent, opt.value);
                        const selected = isMatch ? 'selected' : '';
                        optionsHtml += `<option value="${{opt.value}}" ${{selected}}>${{opt.label}}</option>`;
                    }});
                    html += `
                        <div class="fullscreen-field">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <select class="fullscreen-field-select fullscreen-edit-input-field" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">
                                ${{optionsHtml}}
                            </select>
                        </div>
                    `;
                }} else if (campoKey === 'data_pian') {{
                    // Input date per data pianificazione
                    let dataFormatted = '';
                    if (valoreCurrent) {{
                        if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(valoreCurrent)) {{
                            dataFormatted = valoreCurrent;
                        }} else if (/^\\d{{2}}\\/\\d{{2}}\\/\\d{{4}}$/.test(valoreCurrent)) {{
                            const parts = valoreCurrent.split('/');
                            dataFormatted = `${{parts[2]}}-${{parts[1]}}-${{parts[0]}}`;
                        }} else {{
                            try {{
                                const d = new Date(valoreCurrent);
                                if (!isNaN(d.getTime())) {{
                                    dataFormatted = d.toISOString().split('T')[0];
                                }}
                            }} catch (e) {{}}
                        }}
                    }}
                    html += `
                        <div class="fullscreen-field">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <input type="date" class="fullscreen-field-input fullscreen-edit-input-field" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" value="${{dataFormatted}}" autocomplete="off">
                        </div>
                    `;
                }} else if (campoKey === 'nominativo') {{
                    // Campo nominativo protetto: valore originale readonly + input per aggiungere appunti
                    const valOrig = escapeHtml(valoreCurrent || '');
                    html += `
                        <div class="fullscreen-field full-width">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <div class="nominativo-protected-wrapper">
                                <span class="nominativo-original">${{valOrig}}</span>
                                <input type="text" class="fullscreen-field-input nominativo-append-input" 
                                    data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" 
                                    data-original="${{valOrig}}"
                                    placeholder="Aggiungi appunti..."
                                    value="" autocomplete="off">
                            </div>
                        </div>
                    `;
                }} else if (campoKey === 'note_app') {{
                    // Textarea per note lunghe - full width + bottone Aggiungi nota
                    html += `
                        <div class="fullscreen-field full-width">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <textarea class="fullscreen-field-textarea fullscreen-edit-input-field" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">${{valoreCurrent || ''}}</textarea>
                            <button type="button" class="btn-aggiungi-nota" onclick="aggiungiNotaConData('fullscreen')">📝 Aggiungi nota</button>
                        </div>
                    `;
                }} else {{
                    // Input normale
                    html += `
                        <div class="fullscreen-field">
                            <label class="fullscreen-field-label">${{label}}</label>
                            <input type="text" class="fullscreen-field-input fullscreen-edit-input-field" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" value="${{escapeHtml(valoreCurrent || '')}}" autocomplete="off">
                        </div>
                    `;
                }}
            }}
            
            document.getElementById('fullscreenEditFields').innerHTML = html;
        }}
        
        // Funzione helper per match posizione misuratore
        function matchPosizioneMis(valoreCorrente, opzioneValore) {{
            if (!valoreCorrente) return false;
            
            const valoreNorm = valoreCorrente.trim().toLowerCase();
            const optValoreNorm = opzioneValore.trim().toLowerCase();
            
            // Match esatto
            if (valoreNorm === optValoreNorm) return true;
            
            // Match per numero iniziale
            const primoCarattere = valoreNorm.charAt(0);
            const optPrimoCarattere = optValoreNorm.charAt(0);
            
            if (primoCarattere >= '0' && primoCarattere <= '9' && primoCarattere === optPrimoCarattere) {{
                return true;
            }}
            
            // Match per parole chiave
            const keywords = {{
                'appartamento': '1',
                'centralizzato': '2', 
                'centralizzata': '2',
                'esterno': '3',
                'accessibile': '3',
                'non conosciuta': '0',
                'sconosciuta': '0'
            }};
            
            for (const [kw, num] of Object.entries(keywords)) {{
                if (valoreNorm.includes(kw) && optPrimoCarattere === num) {{
                    return true;
                }}
            }}
            
            return false;
        }}
        
        // Utility condivisa: costruisce nota con data DD/MM in testa, separata da //
        function buildNotaConData(testoNota, notaAttuale) {{
            const now = new Date();
            const dd = String(now.getDate()).padStart(2, '0');
            const mm = String(now.getMonth() + 1).padStart(2, '0');
            let nuovaNota = `${{dd}}/${{mm}} ${{testoNota.trim()}}`;
            if (notaAttuale && notaAttuale.trim()) {{
                return nuovaNota + ' // ' + notaAttuale.trim();
            }}
            return nuovaNota;
        }}
        
        // Funzione per aggiungere una nota con data DD/MM in testa
        function aggiungiNotaConData(context) {{
            const testoNota = prompt('Inserisci la nota da aggiungere:');
            if (!testoNota || !testoNota.trim()) return;
            
            // Trova la textarea delle note nel contesto corretto
            let textarea;
            if (context === 'fullscreen') {{
                textarea = document.querySelector('.fullscreen-edit-input-field[data-campo="note_app"]');
            }} else {{
                textarea = document.querySelector('.popup-edit-textarea[data-campo="note_app"]');
            }}
            
            if (textarea) {{
                textarea.value = buildNotaConData(testoNota, textarea.value);
            }}
        }}

        // ===========================================================
        // ACCESS POINT (UPS-style) — mini-mappa nella vista Modifica
        // ===========================================================
        let _apMap = null;
        let _apRawMarker = null;
        let _apAccessMarker = null;
        let _apTileLayer = null;
        let _apCurrent = null;  // {{lat, lon, source, road, eneltel, raw:{{lat,lon}}}}

        function _setAccessPointStatus(msg, kind) {{
            const el = document.getElementById('accessPointStatusMsg');
            if (!el) return;
            el.className = 'access-point-status';
            if (msg) {{
                el.classList.add('visible');
                if (kind) el.classList.add(kind);
                el.textContent = msg;
            }} else {{
                el.textContent = '';
            }}
        }}

        function _aggiornaAccessPointBadge(source, road) {{
            const badge = document.getElementById('accessPointSourceBadge');
            const roadLbl = document.getElementById('accessPointRoadLabel');
            if (!badge) return;
            const s = (source || 'fallback').toLowerCase();
            const labels = {{
                'override': '🔒 Manuale',
                'osrm': '🛣️ Auto (OSRM)',
                'fallback': '⚠️ Fallback',
                'no_civico': '📍 Senza civico'
            }};
            badge.className = 'access-point-badge ' + s;
            badge.textContent = labels[s] || s;
            if (roadLbl) {{
                roadLbl.textContent = road ? ('su ' + road) : '';
            }}
        }}

        function _aggiornaUndoButton(canUndo) {{
            const btn = document.getElementById('btnAccessPointUndo');
            if (!btn) return;
            if (canUndo) {{
                btn.removeAttribute('disabled');
                btn.title = "Ripristina lo stato precedente dell'access point";
            }} else {{
                btn.setAttribute('disabled', 'disabled');
                btn.title = "Nessuna modifica da annullare";
            }}
            if (_apCurrent) {{
                _apCurrent.canUndo = !!canUndo;
            }}
        }}

        async function _caricaStatoAccessPoint(eneltel) {{
            // Recupera lo stato persistito (incluso can_undo) dal backend
            try {{
                const resp = await fetch(`${{API_BASE}}/api/access-point/${{encodeURIComponent(eneltel)}}`);
                if (!resp.ok) return;
                const data = await resp.json();
                if (!data || !data.ok) return;
                _aggiornaUndoButton(!!data.can_undo);
            }} catch (e) {{
                // silenzioso: il bottone resta disabilitato di default
            }}
        }}

        function inizializzaAccessPointMap(contatore) {{
            distruggiAccessPointMap();
            if (!contatore || typeof L === 'undefined') return;

            const rawLat = parseFloat(contatore.lat);
            const rawLon = parseFloat(contatore.lon);
            const accLat = parseFloat(contatore.lat_access != null ? contatore.lat_access : contatore.lat);
            const accLon = parseFloat(contatore.lon_access != null ? contatore.lon_access : contatore.lon);
            if (!isFinite(rawLat) || !isFinite(rawLon)) return;

            _apCurrent = {{
                eneltel: contatore.eneltel || contatore.id || '',
                raw: {{ lat: rawLat, lon: rawLon }},
                lat: accLat,
                lon: accLon,
                source: contatore.access_source || 'fallback',
                road: contatore.access_road || null
            }};

            _aggiornaAccessPointBadge(_apCurrent.source, _apCurrent.road);
            _setAccessPointStatus('', null);
            _aggiornaUndoButton(false);
            // Carica can_undo dal backend (asincrono, non blocca il render)
            if (_apCurrent.eneltel) {{
                _caricaStatoAccessPoint(_apCurrent.eneltel);
            }}

            const container = document.getElementById('accessPointMap');
            if (!container) return;
            // Reset eventuale stato Leaflet residuo
            if (container._leaflet_id) {{
                container._leaflet_id = null;
            }}

            _apMap = L.map(container, {{
                zoomControl: true,
                attributionControl: false,
                scrollWheelZoom: true
            }}).setView([accLat, accLon], 18);

            _apTileLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxZoom: 19,
                minZoom: 14
            }}).addTo(_apMap);

            const blueIcon = L.divIcon({{
                className: 'ap-marker-raw',
                html: '<div style="background:#3498db;border:2px solid #fff;border-radius:50%;width:18px;height:18px;box-shadow:0 0 0 2px rgba(52,152,219,0.4);"></div>',
                iconSize: [18, 18],
                iconAnchor: [9, 9]
            }});
            const redIcon = L.divIcon({{
                className: 'ap-marker-access',
                html: '<div style="background:#e74c3c;border:2px solid #fff;border-radius:50%;width:22px;height:22px;box-shadow:0 0 0 3px rgba(231,76,60,0.45);"></div>',
                iconSize: [22, 22],
                iconAnchor: [11, 11]
            }});

            _apRawMarker = L.marker([rawLat, rawLon], {{
                icon: blueIcon, interactive: true, keyboard: false, title: 'Fabbricato (dato grezzo)'
            }}).addTo(_apMap);
            _apRawMarker.bindTooltip('🏠 Fabbricato (lat/lon dal filone)', {{direction: 'top'}});

            _apAccessMarker = L.marker([accLat, accLon], {{
                icon: redIcon, draggable: true, autoPan: true, title: 'Access point (per il navigatore)'
            }}).addTo(_apMap);
            _apAccessMarker.bindTooltip('📍 Access point — trascinami', {{direction: 'top'}});

            _apAccessMarker.on('dragend', () => {{
                const p = _apAccessMarker.getLatLng();
                _apCurrent.lat = p.lat;
                _apCurrent.lon = p.lng;
                _apCurrent.source = 'override';
                _apCurrent.road = null;
                _aggiornaAccessPointBadge('override', null);
                _setAccessPointStatus('Marker spostato. Premi 💾 Salva per renderlo permanente.', null);
            }});

            _apMap.on('click', (e) => {{
                _apAccessMarker.setLatLng(e.latlng);
                _apCurrent.lat = e.latlng.lat;
                _apCurrent.lon = e.latlng.lng;
                _apCurrent.source = 'override';
                _apCurrent.road = null;
                _aggiornaAccessPointBadge('override', null);
                _setAccessPointStatus('Marker spostato. Premi 💾 Salva per renderlo permanente.', null);
            }});

            // Forza il rendering quando il modale diventa visibile
            setTimeout(() => {{ if (_apMap) _apMap.invalidateSize(); }}, 60);
        }}

        function distruggiAccessPointMap() {{
            try {{
                if (_apMap) {{ _apMap.remove(); }}
            }} catch (e) {{}}
            _apMap = null;
            _apRawMarker = null;
            _apAccessMarker = null;
            _apTileLayer = null;
            _apCurrent = null;
            _setAccessPointStatus('', null);
        }}

        async function snapAccessPointAStrada() {{
            if (!_apCurrent || !_apAccessMarker) return;
            _setAccessPointStatus('Snap in corso...', 'loading');
            try {{
                const ll = _apAccessMarker.getLatLng();
                const resp = await fetch(`${{API_BASE}}/api/access-point/${{encodeURIComponent(_apCurrent.eneltel)}}/snap`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ lat: ll.lat, lon: ll.lng }})
                }});
                const data = await resp.json();
                if (!resp.ok || !data.ok) {{
                    _setAccessPointStatus('Snap fallito: ' + (data.error || resp.status), 'error');
                    return;
                }}
                _apAccessMarker.setLatLng([data.lat, data.lon]);
                _apMap.panTo([data.lat, data.lon]);
                _apCurrent.lat = data.lat;
                _apCurrent.lon = data.lon;
                _apCurrent.source = 'override';
                _apCurrent.road = data.road || null;
                _aggiornaAccessPointBadge('override', data.road);
                _setAccessPointStatus(
                    data.road ? `Snappato su ${{data.road}} (Δ ${{data.distance_m || 0}} m). Premi 💾 per salvare.` : 'Snappato. Premi 💾 per salvare.',
                    null
                );
            }} catch (e) {{
                _setAccessPointStatus('Errore snap: ' + e.message, 'error');
            }}
        }}

        async function salvaAccessPoint() {{
            if (!_apCurrent || !_apCurrent.eneltel) return;
            _setAccessPointStatus('Salvataggio in corso...', 'loading');
            try {{
                const ll = _apAccessMarker ? _apAccessMarker.getLatLng() : {{lat: _apCurrent.lat, lng: _apCurrent.lon}};
                const resp = await fetch(`${{API_BASE}}/api/access-point/${{encodeURIComponent(_apCurrent.eneltel)}}`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ lat: ll.lat, lon: ll.lng }})
                }});
                const data = await resp.json();
                if (!resp.ok || !data.ok) {{
                    _setAccessPointStatus('Salvataggio fallito: ' + (data.error || resp.status), 'error');
                    return;
                }}
                _apCurrent.lat = data.lat;
                _apCurrent.lon = data.lon;
                _apCurrent.source = 'override';
                _aggiornaAccessPointBadge('override', null);
                _aggiornaUndoButton(!!data.can_undo);
                _setAccessPointStatus('✅ Access point salvato. La rotta verrà aggiornata al prossimo ricalcolo.', 'success');
                if (typeof contatori !== 'undefined' && Array.isArray(contatori)) {{
                    const c = contatori.find(x => (x.eneltel || x.id) === _apCurrent.eneltel);
                    if (c) {{
                        c.lat_access = data.lat;
                        c.lon_access = data.lon;
                        c.access_source = 'override';
                        c.access_override = true;
                    }}
                }}
            }} catch (e) {{
                _setAccessPointStatus('Errore salvataggio: ' + e.message, 'error');
            }}
        }}

        async function undoAccessPoint() {{
            if (!_apCurrent || !_apCurrent.eneltel) return;
            const btn = document.getElementById('btnAccessPointUndo');
            if (btn && btn.hasAttribute('disabled')) return;
            _setAccessPointStatus('Annullamento ultima modifica...', 'loading');
            try {{
                const resp = await fetch(`${{API_BASE}}/api/access-point/${{encodeURIComponent(_apCurrent.eneltel)}}/undo`, {{
                    method: 'POST'
                }});
                const data = await resp.json();
                if (resp.status === 409) {{
                    _setAccessPointStatus('Niente da annullare.', 'error');
                    _aggiornaUndoButton(false);
                    return;
                }}
                if (!resp.ok || !data.ok) {{
                    _setAccessPointStatus('Undo fallito: ' + (data.error || resp.status), 'error');
                    _aggiornaUndoButton(!!(data && data.can_undo));
                    return;
                }}
                if (_apAccessMarker && _apMap) {{
                    _apAccessMarker.setLatLng([data.lat, data.lon]);
                    _apMap.panTo([data.lat, data.lon]);
                }}
                _apCurrent.lat = data.lat;
                _apCurrent.lon = data.lon;
                _apCurrent.source = 'override';
                _apCurrent.road = null;
                _aggiornaAccessPointBadge('override', null);
                _aggiornaUndoButton(!!data.can_undo);
                _setAccessPointStatus('↩️ Ripristinato lo stato precedente.', 'success');
                if (typeof contatori !== 'undefined' && Array.isArray(contatori)) {{
                    const c = contatori.find(x => (x.eneltel || x.id) === _apCurrent.eneltel);
                    if (c) {{
                        c.lat_access = data.lat;
                        c.lon_access = data.lon;
                        c.access_source = 'override';
                        c.access_override = true;
                    }}
                }}
            }} catch (e) {{
                _setAccessPointStatus('Errore undo: ' + e.message, 'error');
            }}
        }}

        async function resetAccessPointAuto() {{
            if (!_apCurrent || !_apCurrent.eneltel) return;
            if (!confirm('Rimuovere l\\'override manuale e tornare allo snap automatico?')) return;
            _setAccessPointStatus('Ripristino automatico...', 'loading');
            try {{
                const resp = await fetch(`${{API_BASE}}/api/access-point/${{encodeURIComponent(_apCurrent.eneltel)}}`, {{
                    method: 'DELETE'
                }});
                const data = await resp.json();
                if (!resp.ok || !data.ok) {{
                    _setAccessPointStatus('Reset fallito: ' + (data.error || resp.status), 'error');
                    return;
                }}
                if (data.lat != null && data.lon != null && _apAccessMarker && _apMap) {{
                    _apAccessMarker.setLatLng([data.lat, data.lon]);
                    _apMap.panTo([data.lat, data.lon]);
                    _apCurrent.lat = data.lat;
                    _apCurrent.lon = data.lon;
                    _apCurrent.source = data.source || 'osrm';
                    _apCurrent.road = data.road || null;
                    _aggiornaAccessPointBadge(_apCurrent.source, _apCurrent.road);
                }} else {{
                    _aggiornaAccessPointBadge('osrm', null);
                }}
                _aggiornaUndoButton(!!data.can_undo);
                _setAccessPointStatus('✅ Override rimosso, ripristinato lo snap automatico.', 'success');
                if (typeof contatori !== 'undefined' && Array.isArray(contatori)) {{
                    const c = contatori.find(x => (x.eneltel || x.id) === _apCurrent.eneltel);
                    if (c) {{
                        c.lat_access = _apCurrent.lat;
                        c.lon_access = _apCurrent.lon;
                        c.access_source = _apCurrent.source;
                        c.access_override = false;
                    }}
                }}
            }} catch (e) {{
                _setAccessPointStatus('Errore reset: ' + e.message, 'error');
            }}
        }}
        
        function chiudiVistaModifica() {{
            document.getElementById('fullscreenEditModal').classList.remove('visible');
            currentFullscreenEneltel = null;
            // Reset appuntamento inline
            document.getElementById('fullscreenAppOrarioInizio').value = '09:00';
            document.getElementById('fullscreenAppOrarioFine').value = '';
            const appStatus = document.getElementById('appuntamentoInlineStatus');
            appStatus.className = 'appuntamento-status';
            appStatus.textContent = '';
            // Cleanup mini-mappa access point
            try {{ distruggiAccessPointMap(); }} catch (e) {{}}
        }}
        
        async function fissaAppuntamentoInline() {{
            if (!currentFullscreenEneltel) return;
            
            const oraInizio = document.getElementById('fullscreenAppOrarioInizio').value;
            const oraFine = document.getElementById('fullscreenAppOrarioFine').value;
            const btn = document.getElementById('btnFissaAppuntamento');
            const statusDiv = document.getElementById('appuntamentoInlineStatus');
            
            if (!oraInizio) {{
                statusDiv.className = 'appuntamento-status visible error';
                statusDiv.textContent = '⚠️ Inserisci almeno l\\'orario di inizio';
                return;
            }}
            
            let orario = oraInizio;
            if (oraFine) {{
                orario += '-' + oraFine;
            }}
            
            // Disabilita bottone
            btn.disabled = true;
            btn.textContent = '⏳ Salvataggio...';
            statusDiv.className = 'appuntamento-status';

            // Pre-verifica conflitti
            const contatore = contatori.find(c => c.eneltel === currentFullscreenEneltel);
            if (contatore && contatore.data && orario) {{
                try {{
                    const vResp = await fetch(`${{API_BASE}}/api/appuntamenti/verifica`, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            pod: contatore.pod || '',
                            data: contatore.data,
                            orario: orario,
                            operatore: contatore.tecnico || ''
                        }})
                    }});
                    const vData = await vResp.json();
                    if (vData.success && vData.errori && vData.errori.length) {{
                        const msg = '⚠️ Conflitti rilevati:\\n\\n' + vData.errori.join('\\n') +
                                    (vData.warnings && vData.warnings.length ? '\\n\\nAvvisi:\\n' + vData.warnings.join('\\n') : '') +
                                    '\\n\\nContinuare comunque?';
                        if (!confirm(msg)) {{
                            btn.disabled = false;
                            btn.textContent = '✅ Fissa Appuntamento';
                            return;
                        }}
                    }}
                }} catch(e) {{ /* verifica non bloccante */ }}
            }}
            
            try {{
                // Includi il contenuto corrente delle note per non perdere modifiche non salvate
                const noteTextareaCorrente = document.querySelector('.fullscreen-edit-input-field[data-campo="note_app"]');
                const payload = {{ orario }};
                if (noteTextareaCorrente) {{
                    payload.nota_corrente = noteTextareaCorrente.value;
                }}
                // LCL della riga (per disambiguare duplicate_eneltel)
                try {{
                    const _c = contatori.find(cc => cc.eneltel === currentFullscreenEneltel);
                    if (_c && _c.lcl !== undefined) payload._anchor_lcl = String(_c.lcl || '');
                }} catch (e) {{ /* ignora */ }}
                const response = await fetch(`${{API_BASE}}/api/contatore/${{currentFullscreenEneltel}}/appuntamento`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                
                const result = await response.json();
                
                if (response.ok && result.success) {{
                    statusDiv.className = 'appuntamento-status visible success';
                    statusDiv.textContent = '✅ Appuntamento impostato: APP ' + orario;
                    
                    // Aggiorna il contatore locale
                    const idx = contatori.findIndex(c => c.eneltel === currentFullscreenEneltel);
                    if (idx >= 0) {{
                        contatori[idx].is_appuntamento = true;
                        contatori[idx].ora_appuntamento = orario;
                        contatori[idx].note_app = result.nota;
                    }}
                    
                    // Aggiorna anche la textarea delle note se visibile
                    const noteTextarea = document.querySelector('.fullscreen-edit-input-field[data-campo="note_app"]');
                    if (noteTextarea && result.nota) {{
                        noteTextarea.value = result.nota;
                    }}
                    
                    // Mostra il pulsante rimuovi appuntamento
                    const btnRimuoviApp = document.getElementById('btnRimuoviAppuntamento');
                    if (btnRimuoviApp) {{
                        btnRimuoviApp.style.display = 'inline-flex';
                    }}
                    
                    // Refresh istantaneo della mappa
                    if (typeof updateContatoriVisibili === 'function') updateContatoriVisibili();
                    if (typeof refreshCurrentView === 'function') refreshCurrentView();
                    
                }} else {{
                    statusDiv.className = 'appuntamento-status visible error';
                    statusDiv.textContent = '❌ ' + (result.error || 'Errore sconosciuto');
                }}
            }} catch (err) {{
                statusDiv.className = 'appuntamento-status visible error';
                statusDiv.textContent = '❌ Errore di connessione: ' + err.message;
            }} finally {{
                btn.disabled = false;
                btn.textContent = '✅ Fissa Appuntamento';
            }}
        }}
        
        async function salvaModificheFullscreen() {{
            if (!currentFullscreenEneltel) return;
            
            const statusDiv = document.getElementById('fullscreenStatus');
            statusDiv.className = 'fullscreen-status visible loading';
            statusDiv.textContent = '⏳ Salvataggio in corso...';
            
            // Raccogli valori dal form fullscreen
            const fields = {{}};
            document.querySelectorAll('.fullscreen-edit-input-field').forEach(el => {{
                const colonna = el.dataset.colonna;
                let value = el.value;
                
                // Se è un campo data in formato YYYY-MM-DD, converti a DD/MM/YYYY per Google Sheets
                if (el.type === 'date' && value && /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(value)) {{
                    const parts = value.split('-');
                    value = `${{parts[2]}}/${{parts[1]}}/${{parts[0]}}`;
                }}
                
                fields[colonna] = value;
            }});
            
            // Gestisci campo nominativo protetto (original + appunti aggiunti)
            const nominativoAppend = document.querySelector('.nominativo-append-input');
            if (nominativoAppend) {{
                const original = nominativoAppend.dataset.original || '';
                const appunti = nominativoAppend.value.trim();
                const colonna = nominativoAppend.dataset.colonna;
                if (appunti) {{
                    fields[colonna] = original + ' ' + appunti;
                }} else {{
                    fields[colonna] = original;
                }}
            }}
            
            // LCL della riga corrente (ancora secondaria per disambiguare i
            // duplicate_eneltel su Pavia: stesso Eneltel su 2 righe diverse).
            try {{
                const _cFs = contatori.find(c => c.eneltel === currentFullscreenEneltel);
                if (_cFs && _cFs.lcl !== undefined) fields['_anchor_lcl'] = String(_cFs.lcl || '');
            }} catch (e) {{ /* ignora */ }}

            try {{
                const response = await fetch(`${{API_BASE}}/api/contatore/${{currentFullscreenEneltel}}`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(fields)
                }});

                const result = await response.json();

                if (response.ok && result.success) {{
                    statusDiv.className = 'fullscreen-status visible success';
                    statusDiv.textContent = '✅ Salvato con successo!';

                    // Aggiorna dati locali
                    const idx = contatori.findIndex(c => c.eneltel === currentFullscreenEneltel);
                    if (idx >= 0) {{
                        // Aggiorna i campi modificati
                        Object.entries(fields).forEach(([col, val]) => {{
                            for (const [k, c] of Object.entries(campiModificabili)) {{
                                if (c === col) {{
                                    const mapping = {{
                                        'data_pian': 'data',
                                        'note_app': 'note_app',
                                        'nominativo': 'cliente',
                                        'indirizzo': 'via',
                                        'civico': 'civico',
                                        'telefono': 'telefono',
                                        'email': 'email',
                                        'posizione_mis': 'note',  // Posizione misuratore è nel campo 'note'
                                        'operatore': 'tecnico',
                                        'smart_test': 'smart_test'
                                    }};
                                    if (mapping[k]) {{
                                        contatori[idx][mapping[k]] = val;
                                    }}
                                    break;
                                }}
                            }}
                        }});
                        
                        // Ricalcola is_appuntamento e ora_appuntamento se note_app è cambiato
                        if (fields[campiModificabili.note_app]) {{
                            const noteApp = (contatori[idx].note_app || '').toLowerCase();
                            contatori[idx].is_appuntamento = noteApp.includes('app');
                            
                            // Estrai ora appuntamento dalle note
                            const oraMatch = noteApp.match(/app\\s*(\\d{{1,2}})[:\\.]?(\\d{{2}})?/i);
                            if (oraMatch) {{
                                const ore = oraMatch[1].padStart(2, '0');
                                const minuti = oraMatch[2] || '00';
                                contatori[idx].ora_appuntamento = `${{ore}}:${{minuti}}`;
                            }} else if (contatori[idx].is_appuntamento) {{
                                contatori[idx].ora_appuntamento = 'APP';
                            }} else {{
                                contatori[idx].ora_appuntamento = null;
                            }}
                        }}
                        
                        // Aggiorna anche la vista fullscreen corrente
                        aggiornaVistaFullscreen(contatori[idx]);
                        
                        // Aggiorna sidebar e popup (ma non la mappa intera)
                        aggiornaUIDopoModifica(currentFullscreenEneltel, contatori[idx]);
                        
                        // Refresh istantaneo della mappa con i dati locali aggiornati
                        if (typeof updateContatoriVisibili === 'function') updateContatoriVisibili();
                        if (typeof refreshCurrentView === 'function') refreshCurrentView();
                    }}
                    
                }} else {{
                    statusDiv.className = 'fullscreen-status visible error';
                    statusDiv.textContent = '❌ ' + (result.error || 'Errore sconosciuto');
                }}
            }} catch (err) {{
                statusDiv.className = 'fullscreen-status visible error';
                statusDiv.textContent = '❌ Errore di connessione: ' + err.message;
            }}
        }}
        
        // Aggiorna la vista fullscreen dopo il salvataggio
        function aggiornaVistaFullscreen(contatore) {{
            // Aggiorna info card
            document.getElementById('fullscreenCliente').textContent = contatore.cliente || 'N/D';
            document.getElementById('fullscreenTecnicoBadge').textContent = typeof getDisplayName === 'function' ? getDisplayName(contatore.tecnico) : (contatore.tecnico || 'Non assegnato');
            
            // Ricarica info rows
            const infoRowsHtml = `
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">POD</div>
                    <div class="fullscreen-info-value">${{contatore.pod || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Eneltel</div>
                    <div class="fullscreen-info-value">${{contatore.eneltel || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Indirizzo</div>
                    <div class="fullscreen-info-value">${{contatore.via || ''}} ${{contatore.civico || ''}}, ${{contatore.comune || ''}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Telefono</div>
                    <div class="fullscreen-info-value">${{contatore.telefono || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Data</div>
                    <div class="fullscreen-info-value">${{formatDate(contatore.data) || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Ora Pianificata</div>
                    <div class="fullscreen-info-value">${{contatore.ora_pianificazione || contatore.orari_lettere || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Posizione</div>
                    <div class="fullscreen-info-value">${{contatore.note || 'N/D'}}</div>
                </div>
                <div class="fullscreen-info-row">
                    <div class="fullscreen-info-label">Note App</div>
                    <div class="fullscreen-info-value">${{contatore.note_app || '-'}}</div>
                </div>
            `;
            document.getElementById('fullscreenInfoRows').innerHTML = infoRowsHtml;
        }}
        
        // ========================================
        // POPUP CON TOGGLE MODIFICA (legacy - ora usa vista fullscreen)
        // ========================================
        
        function creaPulsanteModifica(eneltel) {{
            return `
                <div class="popup-edit-toggle">
                    <button class="btn-toggle-edit" onclick="toggleEditMode('${{eneltel}}', this)">
                        ✏️ Modifica
                    </button>
                </div>
            `;
        }}
        
        function toggleEditMode(eneltel, btn) {{
            const formId = 'editForm-' + eneltel;
            const form = document.getElementById(formId);
            
            if (form.classList.contains('visible')) {{
                // Chiudi form
                form.classList.remove('visible');
                btn.classList.remove('active');
                btn.innerHTML = '✏️ Modifica';
            }} else {{
                // Apri form
                form.classList.add('visible');
                btn.classList.add('active');
                btn.innerHTML = '✕ Chiudi modifica';
            }}
        }}
        
        function creaFormEditPopup(contatore) {{
            let html = `<div id="editForm-${{contatore.eneltel}}" class="popup-edit-form">`;
            html += '<div class="popup-edit-title">✏️ Modifica dati</div>';
            
            // Genera campi per ogni campo modificabile
            for (const [campoKey, colonnaSheet] of Object.entries(campiModificabili)) {{
                const label = campiLabels[campoKey] || campoKey;
                const valoreCurrent = getValoreContatore(contatore, campoKey, colonnaSheet);
                
                if (campoKey === 'operatore') {{
                    // Dropdown per operatore
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <select class="popup-edit-select" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">
                                <option value="">-- Seleziona --</option>
                    `;
                    Object.entries(listaOperatori).forEach(([nome, mat]) => {{
                        const selected = valoreCurrent === mat || valoreCurrent === nome ? 'selected' : '';
                        html += `<option value="${{mat}}" ${{selected}}>${{nome}}</option>`;
                    }});
                    html += `
                            </select>
                        </div>
                    `;
                }} else if (campoKey === 'posizione_mis') {{
                    // Dropdown per posizione misuratore
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <select class="popup-edit-select" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">
                                <option value="">-- Seleziona --</option>
                    `;
                    opzioniPosizioneMis.forEach(opt => {{
                        // Normalizza il valore corrente per il confronto
                        const valoreNorm = (valoreCurrent || '').trim().toLowerCase();
                        const optValoreNorm = opt.value.trim().toLowerCase();
                        
                        // Match se:
                        // 1. Valore esatto uguale
                        // 2. Il valore corrente inizia con lo stesso numero (es. "1" matcha "1 - Nell'appartamento")
                        // 3. Il valore corrente contiene la parola chiave (es. "appartamento" matcha opzione 1)
                        // 4. L'opzione contiene il valore corrente (es. "Nell'appartamento" è in "1 - Nell'appartamento")
                        const primoCarattere = valoreNorm.charAt(0);
                        const optPrimoCarattere = optValoreNorm.charAt(0);
                        
                        let isMatch = false;
                        if (valoreNorm === optValoreNorm) {{
                            isMatch = true;
                        }} else if (primoCarattere >= '0' && primoCarattere <= '9' && primoCarattere === optPrimoCarattere) {{
                            // Match per numero iniziale (es. "1" o "1 -" matcha "1 - Nell'appartamento")
                            isMatch = true;
                        }} else if (valoreNorm && optValoreNorm.includes(valoreNorm)) {{
                            // Il valore è contenuto nell'opzione (es. "appartamento" in "1 - Nell'appartamento")
                            isMatch = true;
                        }} else if (valoreNorm) {{
                            // Cerca parole chiave
                            const keywords = {{
                                'appartamento': '1',
                                'centralizzato': '2', 
                                'centralizzata': '2',
                                'esterno': '3',
                                'accessibile': '3',
                                'non conosciuta': '0',
                                'sconosciuta': '0'
                            }};
                            for (const [kw, num] of Object.entries(keywords)) {{
                                if (valoreNorm.includes(kw) && optPrimoCarattere === num) {{
                                    isMatch = true;
                                    break;
                                }}
                            }}
                        }}
                        
                        const selected = isMatch ? 'selected' : '';
                        html += `<option value="${{opt.value}}" ${{selected}}>${{opt.label}}</option>`;
                    }});
                    html += `
                            </select>
                        </div>
                    `;
                }} else if (campoKey === 'data_pian') {{
                    // Input date per data pianificazione
                    // Converti il formato da vari formati possibili a YYYY-MM-DD
                    let dataFormatted = '';
                    if (valoreCurrent) {{
                        // Se è già YYYY-MM-DD
                        if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(valoreCurrent)) {{
                            dataFormatted = valoreCurrent;
                        }}
                        // Se è DD/MM/YYYY
                        else if (/^\\d{{2}}\\/\\d{{2}}\\/\\d{{4}}$/.test(valoreCurrent)) {{
                            const parts = valoreCurrent.split('/');
                            dataFormatted = `${{parts[2]}}-${{parts[1]}}-${{parts[0]}}`;
                        }}
                        // Se è altro formato, prova parsing
                        else {{
                            try {{
                                const d = new Date(valoreCurrent);
                                if (!isNaN(d.getTime())) {{
                                    dataFormatted = d.toISOString().split('T')[0];
                                }}
                            }} catch (e) {{}}
                        }}
                    }}
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <input type="date" class="popup-edit-input" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" value="${{dataFormatted}}" autocomplete="off">
                        </div>
                    `;
                }} else if (campoKey === 'nominativo') {{
                    // Campo nominativo protetto: valore originale readonly + input per aggiungere appunti
                    const valOrig = escapeHtml(valoreCurrent || '');
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <div class="nominativo-protected-wrapper">
                                <span class="nominativo-original">${{valOrig}}</span>
                                <input type="text" class="popup-edit-input popup-nominativo-append-input" 
                                    data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" 
                                    data-original="${{valOrig}}"
                                    placeholder="Aggiungi appunti..."
                                    value="" autocomplete="off">
                            </div>
                        </div>
                    `;
                }} else if (campoKey === 'note_app') {{
                    // Textarea per note lunghe + bottone Aggiungi nota
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <textarea class="popup-edit-textarea" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}">${{valoreCurrent || ''}}</textarea>
                            <button type="button" class="btn-aggiungi-nota" onclick="aggiungiNotaConData('popup')">📝 Aggiungi nota</button>
                        </div>
                    `;
                }} else {{
                    // Input normale
                    html += `
                        <div class="popup-edit-field">
                            <label class="popup-edit-label">${{label}}</label>
                            <input type="text" class="popup-edit-input" data-campo="${{campoKey}}" data-colonna="${{colonnaSheet}}" value="${{escapeHtml(valoreCurrent || '')}}" autocomplete="off">
                        </div>
                    `;
                }}
            }}
            
            // Pulsanti azione
            html += `
                <div class="popup-edit-actions">
                    <button class="btn-save-edit" onclick="salvaModificheContatore('${{contatore.eneltel}}')">💾 Salva</button>
                    <button class="btn-cancel-edit" onclick="chiudiEditMode('${{contatore.eneltel}}')">Annulla</button>
                </div>
                <button class="btn-appuntamento" onclick="apriModalAppuntamento('${{contatore.eneltel}}')">📞 Fissa appuntamento</button>
                ${{contatore.is_appuntamento ? `<button class="btn-appuntamento" onclick="rimuoviAppuntamentoGestionale('${{contatore.eneltel}}')" style="background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); margin-top: 5px;">🗑️ Rimuovi appuntamento</button>` : ''}}
                <div id="editStatus-${{contatore.eneltel}}" class="edit-status"></div>
            `;
            
            html += '</div>';
            return html;
        }}
        
        function chiudiEditMode(eneltel) {{
            const form = document.getElementById('editForm-' + eneltel);
            const btn = form?.parentElement?.querySelector('.btn-toggle-edit');
            if (form) form.classList.remove('visible');
            if (btn) {{
                btn.classList.remove('active');
                btn.innerHTML = '✏️ Modifica';
            }}
        }}
        
        function getValoreContatore(contatore, campoKey, colonnaSheet) {{
            if (campoKey === 'posizione_mis') {{
                return contatore.posizione_mis || contatore.note || '';
            }}

            // Mappa i campi del contatore ai nomi usati nel popup
            const mapping = {{
                'data_pian': 'data',
                'note_app': 'note_app',
                'nominativo': 'cliente',
                'indirizzo': 'via',
                'civico': 'civico',
                'telefono': 'telefono',
                'email': 'email',
                'posizione_mis': 'note',  // Posizione misuratore viene da NOTE_K (campo 'note' nel contatore)
                'operatore': 'tecnico',
                'smart_test': 'smart_test'
            }};
            
            const key = mapping[campoKey] || campoKey;
            return contatore[key] || '';
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        async function salvaModificheContatore(eneltel) {{
            const statusDiv = document.getElementById(`editStatus-${{eneltel}}`);
            statusDiv.className = 'edit-status loading';
            statusDiv.textContent = '⏳ Salvataggio in corso...';
            
            // Raccogli valori dal form
            const fields = {{}};
            document.querySelectorAll('[data-colonna]').forEach(el => {{
                // Ignora i campi nominativo protetti (gestiti dopo)
                if (el.classList.contains('popup-nominativo-append-input') || el.classList.contains('nominativo-append-input')) return;
                
                const colonna = el.dataset.colonna;
                let value = el.value;
                
                // Se è un campo data in formato YYYY-MM-DD, converti a DD/MM/YYYY per Google Sheets
                if (el.type === 'date' && value && /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(value)) {{
                    const parts = value.split('-');
                    value = `${{parts[2]}}/${{parts[1]}}/${{parts[0]}}`;
                }}
                
                fields[colonna] = value;
            }});
            
            // Gestisci campo nominativo protetto (original + appunti aggiunti)
            const nominativoAppend = document.querySelector('.popup-nominativo-append-input');
            if (nominativoAppend) {{
                const original = nominativoAppend.dataset.original || '';
                const appunti = nominativoAppend.value.trim();
                const colonna = nominativoAppend.dataset.colonna;
                if (appunti) {{
                    fields[colonna] = original + ' ' + appunti;
                }} else {{
                    fields[colonna] = original;
                }}
            }}
            
            // LCL della riga (ancora secondaria per i duplicate_eneltel su Pavia)
            try {{
                const _c = contatori.find(cc => cc.eneltel === eneltel);
                if (_c && _c.lcl !== undefined) fields['_anchor_lcl'] = String(_c.lcl || '');
            }} catch (e) {{ /* ignora */ }}

            try {{
                const response = await fetch(`${{API_BASE}}/api/contatore/${{eneltel}}`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(fields)
                }});

                const result = await response.json();

                if (response.ok && result.success) {{
                    statusDiv.className = 'edit-status success';
                    statusDiv.textContent = '✅ Salvato con successo!';
                    
                    // Aggiorna dati locali
                    const idx = contatori.findIndex(c => c.eneltel === eneltel);
                    if (idx >= 0) {{
                        // Aggiorna i campi modificati
                        Object.entries(fields).forEach(([col, val]) => {{
                            // Trova la chiave corrispondente nel contatore
                            for (const [k, c] of Object.entries(campiModificabili)) {{
                                if (c === col) {{
                                    const mapping = {{
                                        'data_pian': 'data',
                                        'note_app': 'note_app',
                                        'nominativo': 'cliente',
                                        'indirizzo': 'via',
                                        'civico': 'civico',
                                        'telefono': 'telefono',
                                        'email': 'email',
                                        'posizione_mis': 'note',  // Posizione misuratore è nel campo 'note'
                                        'operatore': 'tecnico',
                                        'smart_test': 'smart_test'
                                    }};
                                    if (mapping[k]) {{
                                        contatori[idx][mapping[k]] = val;
                                        if (k === 'posizione_mis') {{
                                            contatori[idx].posizione_mis = val;
                                        }}
                                    }}
                                    break;
                                }}
                            }}
                        }});
                        
                        // AGGIORNAMENTO IMMEDIATO UI
                        aggiornaUIDopoModifica(eneltel, contatori[idx]);
                    }}
                    
                    // Chiudi form dopo 1 secondo
                    setTimeout(() => {{
                        chiudiEditMode(eneltel);
                    }}, 1000);
                }} else {{
                    statusDiv.className = 'edit-status error';
                    statusDiv.textContent = '❌ ' + (result.error || 'Errore sconosciuto');
                }}
            }} catch (err) {{
                statusDiv.className = 'edit-status error';
                statusDiv.textContent = '❌ Errore di connessione: ' + err.message;
            }}
        }}
        
        // Aggiorna UI (sidebar + popup) dopo una modifica
        function aggiornaUIDopoModifica(eneltel, contatoreAggiornato) {{
            console.log('🔄 aggiornaUIDopoModifica chiamata per:', eneltel, contatoreAggiornato);
            
            // 1. Aggiorna la card nella sidebar
            const card = document.querySelector(`.tappa-card[data-eneltel="${{eneltel}}"]`);
            console.log('📍 Card trovata:', card);
            
            if (card) {{
                // Aggiorna cliente (sempre presente nel .tappa-header)
                const clienteEl = card.querySelector('.tappa-cliente');
                if (clienteEl) {{
                    clienteEl.textContent = contatoreAggiornato.cliente || 'N/D';
                    console.log('✅ Cliente aggiornato:', contatoreAggiornato.cliente);
                }}
                
                // Aggiorna dettagli (comune, via, eneltel, telefono, note)
                const dettagli = card.querySelector('.tappa-dettagli');
                if (dettagli) {{
                    // Ricostruisci l'HTML dei dettagli
                    let dettagliHtml = `<div>📍 ${{contatoreAggiornato.comune || ''}} - ${{contatoreAggiornato.via || 'N/D'}}</div>`;
                    dettagliHtml += `<div>🔌 ${{contatoreAggiornato.eneltel}}${{contatoreAggiornato.lcl ? ' | 🏷️ ' + contatoreAggiornato.lcl : ''}}</div>`;
                    if (contatoreAggiornato.telefono) {{
                        dettagliHtml += `<div>📱 ${{contatoreAggiornato.telefono}}</div>`;
                    }}
                    if (contatoreAggiornato.note) {{
                        dettagliHtml += `<div>📝 ${{contatoreAggiornato.note}}</div>`;
                    }}
                    dettagli.innerHTML = dettagliHtml;
                    console.log('✅ Dettagli aggiornati');
                }}
                
                // Aggiorna badge appuntamento
                let appBadge = card.querySelector('.tappa-app-badge');
                const hasApp = contatoreAggiornato.is_appuntamento || 
                               (contatoreAggiornato.note_app && contatoreAggiornato.note_app.toLowerCase().includes('app'));
                const oraApp = contatoreAggiornato.ora_appuntamento || 'APP';
                
                if (hasApp) {{
                    if (appBadge) {{
                        // Aggiorna il badge esistente con l'ora
                        appBadge.textContent = '☎️ ' + oraApp;
                    }} else {{
                        // Crea nuovo badge
                        const header = card.querySelector('.tappa-header');
                        if (header) {{
                            const badge = document.createElement('div');
                            badge.className = 'tappa-app-badge';
                            badge.textContent = '☎️ ' + oraApp;
                            header.appendChild(badge);
                        }}
                    }}
                    // Aggiungi classe appuntamento alla card
                    card.classList.add('appuntamento');
                    console.log('✅ Badge appuntamento aggiornato:', oraApp);
                }} else if (!hasApp && appBadge) {{
                    appBadge.remove();
                    card.classList.remove('appuntamento');
                    console.log('✅ Badge appuntamento rimosso');
                }}
            }} else {{
                console.log('⚠️ Card non trovata per eneltel:', eneltel);
            }}
            
            // 2. Aggiorna anche nell'array globale contatori
            const idxGlobal = contatori.findIndex(c => c.eneltel === eneltel);
            if (idxGlobal >= 0) {{
                contatori[idxGlobal] = contatoreAggiornato;
            }}
            
            // 3. Aggiorna il popup del marker normale
            const marker = markers.find(m => m.contatore && m.contatore.eneltel === eneltel);
            console.log('📍 Marker trovato:', marker);
            
            if (marker) {{
                // Aggiorna i dati del contatore nel marker
                marker.contatore = contatoreAggiornato;
                
                // Se il popup è aperto, aggiornalo forzatamente
                if (marker.isPopupOpen && marker.isPopupOpen()) {{
                    const num = marker.numeroTappa || '';
                    const colore = marker.coloreTecnico || '#e94560';
                    const prevPoint = marker.prevPoint || null;
                    const nuovoContenuto = createContatorePopup(contatoreAggiornato, num, colore, prevPoint);
                    marker.closePopup();
                    marker.unbindPopup();
                    marker.bindPopup(nuovoContenuto, {{ maxWidth: 350, className: 'custom-popup' }});
                    setTimeout(() => {{ marker.openPopup(); }}, 50);
                }} else {{
                    const num = marker.numeroTappa || '';
                    const colore = marker.coloreTecnico || '#e94560';
                    const prevPoint = marker.prevPoint || null;
                    const nuovoContenuto = createContatorePopup(contatoreAggiornato, num, colore, prevPoint);
                    marker.unbindPopup();
                    marker.bindPopup(nuovoContenuto, {{ maxWidth: 350, className: 'custom-popup' }});
                }}
            }}
            
            console.log('✅ UI aggiornata per contatore:', eneltel);
        }}
        
        // ========================================
        // APPUNTAMENTI
        // ========================================
        
        let currentAppuntamentoEneltel = null;
        
        function apriModalAppuntamento(eneltel) {{
            currentAppuntamentoEneltel = eneltel;
            document.getElementById('appuntamentoModal').classList.add('visible');
            document.getElementById('appOrarioInizio').value = '09:00';
            document.getElementById('appOrarioFine').value = '';
        }}
        
        function chiudiModalAppuntamento() {{
            document.getElementById('appuntamentoModal').classList.remove('visible');
            currentAppuntamentoEneltel = null;
        }}
        
        async function confermaAppuntamento() {{
            if (!currentAppuntamentoEneltel) return;
            
            const oraInizio = document.getElementById('appOrarioInizio').value;
            const oraFine = document.getElementById('appOrarioFine').value;
            
            if (!oraInizio) {{
                alert('Inserisci almeno l\\'orario di inizio');
                return;
            }}
            
            let orario = oraInizio;
            if (oraFine) {{
                orario += '-' + oraFine;
            }}
            
            try {{
                const _payloadApp = {{ orario }};
                try {{
                    const _c = contatori.find(cc => cc.eneltel === currentAppuntamentoEneltel);
                    if (_c && _c.lcl !== undefined) _payloadApp._anchor_lcl = String(_c.lcl || '');
                }} catch (e) {{ /* ignora */ }}
                const response = await fetch(`${{API_BASE}}/api/contatore/${{currentAppuntamentoEneltel}}/appuntamento`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(_payloadApp)
                }});
                
                const result = await response.json();
                
                if (response.ok && result.success) {{
                    alert('✅ Appuntamento impostato: ' + orario);
                    chiudiModalAppuntamento();
                    map.closePopup();
                    
                    // Aggiorna il contatore locale
                    const idx = contatori.findIndex(c => c.eneltel === currentAppuntamentoEneltel);
                    if (idx >= 0) {{
                        contatori[idx].is_appuntamento = true;
                        contatori[idx].ora_appuntamento = orario;
                        contatori[idx].note_app = result.nota;
                    }}
                }} else {{
                    alert('❌ Errore: ' + (result.error || 'Errore sconosciuto'));
                }}
            }} catch (err) {{
                alert('❌ Errore di connessione: ' + err.message);
            }}
        }}
        
        async function rimuoviAppuntamentoGestionale(eneltel) {{
            if (!confirm('Vuoi rimuovere l\\'appuntamento da questo contatore?')) return;
            
            try {{
                // Includi il contenuto corrente delle note per non perdere modifiche non salvate
                const noteTextareaCorrente = document.querySelector('.fullscreen-edit-input-field[data-campo="note_app"]');
                const payload = {{}};
                if (noteTextareaCorrente) {{
                    payload.nota_corrente = noteTextareaCorrente.value;
                }}
                try {{
                    const _c = contatori.find(cc => cc.eneltel === eneltel);
                    if (_c && _c.lcl !== undefined) payload._anchor_lcl = String(_c.lcl || '');
                }} catch (e) {{ /* ignora */ }}
                const response = await fetch(`${{API_BASE}}/api/contatore/${{eneltel}}/rimuovi-appuntamento`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                
                const result = await response.json();
                
                if (response.ok && result.success) {{
                    alert('✅ Appuntamento rimosso');
                    map.closePopup();
                    
                    // Aggiorna il contatore locale
                    const idx = contatori.findIndex(c => c.eneltel === eneltel);
                    if (idx >= 0) {{
                        contatori[idx].is_appuntamento = false;
                        contatori[idx].ora_appuntamento = null;
                        contatori[idx].note_app = result.nota || '';
                    }}
                    
                    // Aggiorna la vista fullscreen se aperta
                    if (currentFullscreenEneltel === eneltel) {{
                        aggiornaVistaFullscreen(contatori[contatori.findIndex(c => c.eneltel === eneltel)]);
                        // Aggiorna anche la textarea delle note se visibile
                        const noteTextarea = document.querySelector('.fullscreen-edit-input-field[data-campo="note_app"]');
                        if (noteTextarea) {{
                            noteTextarea.value = result.nota || '';
                        }}
                        // Nascondi pulsante rimuovi appuntamento
                        const btnRimuoviApp = document.getElementById('btnRimuoviAppuntamento');
                        if (btnRimuoviApp) {{
                            btnRimuoviApp.style.display = 'none';
                        }}
                        // Aggiorna status inline
                        const appStatus = document.getElementById('appuntamentoInlineStatus');
                        if (appStatus) {{
                            appStatus.className = 'appuntamento-status visible success';
                            appStatus.textContent = '✅ Appuntamento rimosso';
                        }}
                    }}
                    
                    // Refresh istantaneo della mappa
                    if (typeof updateContatoriVisibili === 'function') updateContatoriVisibili();
                    if (typeof refreshCurrentView === 'function') refreshCurrentView();
                }} else {{
                    alert('❌ Errore: ' + (result.error || 'Errore sconosciuto'));
                }}
            }} catch (err) {{
                alert('❌ Errore di connessione: ' + err.message);
            }}
        }}
        
        // ========================================
        // BOXBEAT UPLOAD
        // ========================================
        
        function apriModalBOXBEAT() {{
            document.getElementById('BOXBEATModal').classList.add('visible');
            // Reset stato
            document.querySelector('.BOXBEAT-progress').classList.remove('visible');
            document.querySelector('.BOXBEAT-result').classList.remove('visible');
            document.querySelector('.BOXBEAT-result').classList.remove('success', 'error');
        }}
        
        function chiudiModalBOXBEAT() {{
            document.getElementById('BOXBEATModal').classList.remove('visible');
        }}
        
        function handleBOXBEATDrop(e) {{
            e.preventDefault();
            e.stopPropagation();
            document.getElementById('BOXBEATDropzone').classList.remove('dragover');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {{
                uploadBOXBEATFiles(files);
            }}
        }}
        
        function handleBOXBEATDragOver(e) {{
            e.preventDefault();
            e.stopPropagation();
            document.getElementById('BOXBEATDropzone').classList.add('dragover');
        }}
        
        function handleBOXBEATDragLeave(e) {{
            e.preventDefault();
            e.stopPropagation();
            document.getElementById('BOXBEATDropzone').classList.remove('dragover');
        }}
        
        function selectBOXBEATFile() {{
            document.getElementById('BOXBEATFileInput').click();
        }}
        
        function handleBOXBEATFileSelect(e) {{
            const files = e.target.files;
            if (files.length > 0) {{
                uploadBOXBEATFiles(files);
            }}
        }}
        
        async function uploadBOXBEATFiles(files) {{
            const progress = document.querySelector('.BOXBEAT-progress');
            const progressFill = document.querySelector('.BOXBEAT-progress-fill');
            const progressText = document.querySelector('.BOXBEAT-progress-text');
            const result = document.querySelector('.BOXBEAT-result');
            
            progress.classList.add('visible');
            progressFill.style.width = '10%';
            progressText.textContent = `Caricamento ${{files.length}} file...`;
            
            const formData = new FormData();
            for (const file of files) {{
                formData.append('files', file);
            }}
            
            try {{
                progressFill.style.width = '30%';
                progressText.textContent = 'Elaborazione in corso...';
                
                const response = await fetch(`${{API_BASE}}/api/upload-BOXBEAT`, {{
                    method: 'POST',
                    body: formData
                }});
                
                progressFill.style.width = '100%';
                
                const data = await response.json();
                
                progress.classList.remove('visible');
                result.classList.add('visible');
                
                if (response.ok && data.success) {{
                    result.classList.add('success');
                    
                    // Mostra dettaglio per file se multipli
                    let dettaglioFile = '';
                    if (data.file_elaborati && data.file_elaborati.length > 1) {{
                        dettaglioFile = '<br><br><strong>Dettaglio file:</strong><br>';
                        for (const f of data.file_elaborati) {{
                            dettaglioFile += `📄 ${{f.nome}}: ${{f.righe}} righe, ${{f.esito_ok}} OK${{f.esito_ko ? ', ' + f.esito_ko + ' KO' : ''}}, ${{f.aggiornati}} agg.<br>`;
                        }}
                    }}
                    
                    // Costruisci dettaglio KO se presenti
                    let dettaglioKO = '';
                    if (data.con_esito_ko > 0 && data.esito_ko_dettagli && data.esito_ko_dettagli.length > 0) {{
                        dettaglioKO = '<br><div style="background: rgba(231,76,60,0.15); border: 1px solid #e74c3c; border-radius: 8px; padding: 10px; margin-top: 8px; max-height: 200px; overflow-y: auto;">';
                        dettaglioKO += '<strong style="color: #e74c3c;">❌ Dettaglio Esito KO:</strong><br>';
                        for (const ko of data.esito_ko_dettagli) {{
                            dettaglioKO += `<div style="padding: 3px 0; border-bottom: 1px solid rgba(231,76,60,0.2); font-size: 0.9em;">` +
                                `<strong>${{ko.pod}}</strong>` +
                                `${{ko.cliente ? ' — ' + ko.cliente : ''}}` +
                                `<span style="color: #e74c3c; margin-left: 8px;">(${{ko.esito}})</span>` +
                                `</div>`;
                        }}
                        dettaglioKO += '</div>';
                    }}
                    
                    result.innerHTML = `
                        <strong>✅ Importazione completata!</strong><br>
                        <br>
                        📁 File elaborati: ${{data.file_elaborati ? data.file_elaborati.length : 1}}<br>
                        📊 Righe totali: ${{data.totale_file}}<br>
                        ✅ Con Esito OK: ${{data.con_esito_ok}}<br>
                        ${{data.con_esito_ko > 0 ? '❌ Con Esito KO: ' + data.con_esito_ko + '<br>' : ''}}
                        📝 Aggiornati nel FILONE: ${{data.aggiornati}}
                        ${{dettaglioFile}}
                        ${{dettaglioKO}}
                        ${{data.errori && data.errori.length > 0 ? '<br><br>⚠️ Errori: ' + data.errori.join(', ') : ''}}
                    `;
                }} else {{
                    result.classList.add('error');
                                        if (k === 'posizione_mis') {{
                                            contatori[idx].posizione_mis = val;
                                        }}
                    result.innerHTML = `<strong>❌ Errore</strong><br>${{data.error || 'Errore sconosciuto'}}`;
                }}
            }} catch (err) {{
                progress.classList.remove('visible');
                result.classList.add('visible', 'error');
                result.innerHTML = `<strong>❌ Errore di connessione</strong><br>${{err.message}}`;
            }}
        }}
        
        // ========================================
        // SIGILLI E MATRICOLE - Gestione inserimento
        // ========================================
        
        let sigilliData = [];      // Cache dati contatori per sigilli
        let matricoleData = [];    // Cache dati contatori per matricole
        let currentTab = 'sigilli'; // Tab attivo
        let filtroTipoSigilli = 'tutti';  // Filtro per tipo sigilli: tutti, semidiretto, fotovoltaico
        let filtroOperatoreMatricole = ''; // Filtro per operatore matricole
        let ricercaEneltel = '';  // Ricerca ENELTEL
        let _sigilliSearchActive = false;  // Flag per evitare che il focus vada sull'input card durante la ricerca
        
        async function apriVistaSigilli() {{
            // Mostra modal
            document.getElementById('sigilliModal').classList.add('visible');
            
            // Reset tab a sigilli e filtri
            currentTab = 'sigilli';
            filtroTipoSigilli = 'tutti';
            filtroOperatoreMatricole = '';
            ricercaEneltel = '';
            updateTabUI();
            
            // Carica entrambi i dati
            await caricaDatiSigilliMatricole();
        }}
        
        async function caricaDatiSigilliMatricole() {{
            // Mostra loading
            document.getElementById('sigilliContent').innerHTML = `
                <div class="sigilli-loading">
                    <span class="sigilli-loading-spinner">⏳</span>
                    Caricamento contatori installati...
                </div>
            `;
            
            try {{
                // Carica sigilli e matricole in parallelo
                const [sigilliRes, matricoleRes] = await Promise.all([
                    fetch(`${{API_BASE}}/api/sigilli/pending`),
                    fetch(`${{API_BASE}}/api/matricole/pending`)
                ]);
                
                const sigilliJson = await sigilliRes.json();
                const matricoleJson = await matricoleRes.json();
                
                if (!sigilliRes.ok) throw new Error(sigilliJson.error || 'Errore sigilli');
                if (!matricoleRes.ok) throw new Error(matricoleJson.error || 'Errore matricole');
                
                sigilliData = sigilliJson.contatori || [];
                matricoleData = matricoleJson.contatori || [];
                
                // Aggiorna contatori nei tab
                document.getElementById('tabSigilliCount').textContent = sigilliData.length;
                document.getElementById('tabMatricoleCount').textContent = matricoleData.length;
                
                // Renderizza tab attivo
                renderCurrentTab();
                
            }} catch (err) {{
                document.getElementById('sigilliContent').innerHTML = `
                    <div class="sigilli-empty">
                        <div class="sigilli-empty-icon">❌</div>
                        <div>Errore: ${{err.message}}</div>
                    </div>
                `;
            }}
        }}
        
        function switchTab(tab) {{
            currentTab = tab;
            updateTabUI();
            renderCurrentTab();
        }}
        
        function updateTabUI() {{
            const tabSigilli = document.getElementById('tabSigilli');
            const tabMatricole = document.getElementById('tabMatricole');
            const header = document.getElementById('sigilliHeader');
            const title = document.getElementById('sigilliTitle');
            
            // Conta elementi filtrati
            const sigilliFiltered = getSigilliFiltrati();
            const matricoleFiltered = getMatricoleFiltrate();
            
            if (currentTab === 'sigilli') {{
                tabSigilli.classList.add('active');
                tabSigilli.classList.remove('matricole');
                tabMatricole.classList.remove('active', 'matricole');
                header.classList.remove('matricole');
                title.classList.remove('matricole');
                title.innerHTML = `🔐 Inserisci Sigilli <span id="sigilliCount" class="sigilli-count">(${{sigilliFiltered.length}} da completare)</span>`;
            }} else {{
                tabSigilli.classList.remove('active');
                tabMatricole.classList.add('active', 'matricole');
                header.classList.add('matricole');
                title.classList.add('matricole');
                title.innerHTML = `🔢 Inserisci Matricole <span id="sigilliCount" class="sigilli-count">(${{matricoleFiltered.length}} da completare)</span>`;
            }}
        }}
        
        function getSigilliFiltrati() {{
            let data = sigilliData;
            if (filtroTipoSigilli !== 'tutti') {{
                data = data.filter(c => {{
                    const tipo = (c.tipo || '').toLowerCase();
                    if (filtroTipoSigilli === 'semidiretto') return tipo.includes('semidiretto');
                    if (filtroTipoSigilli === 'fotovoltaico') return tipo.includes('fotovoltaico');
                    return true;
                }});
            }}
            if (ricercaEneltel) {{
                const q = ricercaEneltel.toLowerCase();
                data = data.filter(c => (c.eneltel || '').toLowerCase().includes(q));
            }}
            return data;
        }}
        
        function getMatricoleFiltrate() {{
            let data = matricoleData;
            if (filtroOperatoreMatricole) {{
                data = data.filter(c => (c.operatore || '') === filtroOperatoreMatricole);
            }}
            if (ricercaEneltel) {{
                const q = ricercaEneltel.toLowerCase();
                data = data.filter(c => (c.eneltel || '').toLowerCase().includes(q));
            }}
            return data;
        }}
        
        function cambiaFiltroTipoSigilli(tipo) {{
            filtroTipoSigilli = tipo;
            updateTabUI();
            renderSigilliList();
        }}
        
        function cambiaFiltroOperatoreMatricole(operatore) {{
            filtroOperatoreMatricole = operatore;
            updateTabUI();
            renderMatricoleList();
        }}
        
        function renderCurrentTab() {{
            if (currentTab === 'sigilli') {{
                renderSigilliList();
            }} else {{
                renderMatricoleList();
            }}
        }}
        
        function handleSigilliSearch(inputEl) {{
            ricercaEneltel = inputEl.value;
            const cursorPos = inputEl.selectionStart;
            _sigilliSearchActive = true;
            renderCurrentTab();
            // Ri-focus sull'input di ricerca dopo il re-render
            setTimeout(() => {{
                const searchInput = document.querySelector('.sigilli-search-input');
                if (searchInput) {{
                    searchInput.focus();
                    searchInput.setSelectionRange(cursorPos, cursorPos);
                }}
                _sigilliSearchActive = false;
            }}, 10);
        }}
        
        function renderSigilliList() {{
            const dataFiltrata = getSigilliFiltrati();
            
            // Genera HTML filtro + ricerca
            let filterHtml = `
                <div class="sigilli-filter-row">
                    <span class="sigilli-filter-label">Filtra per tipo:</span>
                    <button class="sigilli-filter-btn ${{filtroTipoSigilli === 'tutti' ? 'active' : ''}}" onclick="cambiaFiltroTipoSigilli('tutti')">
                        📋 Tutti (${{sigilliData.length}})
                    </button>
                    <button class="sigilli-filter-btn ${{filtroTipoSigilli === 'semidiretto' ? 'active' : ''}}" onclick="cambiaFiltroTipoSigilli('semidiretto')">
                        ⚡ Semidiretto (${{sigilliData.filter(c => c.tipo && c.tipo.toLowerCase().includes('semidiretto')).length}})
                    </button>
                    <button class="sigilli-filter-btn ${{filtroTipoSigilli === 'fotovoltaico' ? 'active' : ''}}" onclick="cambiaFiltroTipoSigilli('fotovoltaico')">
                        ☀️ Fotovoltaico (${{sigilliData.filter(c => c.tipo && c.tipo.toLowerCase().includes('fotovoltaico')).length}})
                    </button>
                    <input type="text" class="sigilli-search-input" placeholder="🔍 Cerca ENELTEL..." value="${{ricercaEneltel}}" oninput="handleSigilliSearch(this)">
                </div>
            `;
            
            if (dataFiltrata.length === 0) {{
                document.getElementById('sigilliContent').innerHTML = filterHtml + `
                    <div class="sigilli-empty">
                        <div class="sigilli-empty-icon">✅</div>
                        <div>Nessun contatore ${{filtroTipoSigilli !== 'tutti' ? filtroTipoSigilli : ''}} senza sigillo!</div>
                        <div style="font-size: 0.85em; margin-top: 10px; color: var(--text-muted);">
                            Tutti i sigilli sono stati inseriti.
                        </div>
                    </div>
                `;
                return;
            }}
            
            let html = filterHtml + '<div class="sigilli-list">';
            
            dataFiltrata.forEach((cont, idx) => {{
                // Trova l'indice originale per riferimento
                const originalIdx = sigilliData.indexOf(cont);
                const dataInst = cont.data_installazione || 'N/D';
                const operatore = cont.operatore || 'N/D';
                const pod = cont.pod || '';
                const eneltel = cont.eneltel || '';
                const cliente = cont.cliente || 'N/D';
                const indirizzo = cont.indirizzo || '';
                const comune = cont.comune || '';
                const tipo = cont.tipo || 'Semidiretto';
                const tipoIcon = tipo === 'Fotovoltaico' ? '☀️' : '⚡';
                const tipoClass = tipo === 'Fotovoltaico' ? 'fotovoltaico' : '';
                
                html += `
                    <div class="sigilli-card" id="sigillo-card-${{originalIdx}}" data-eneltel="${{eneltel}}" data-pod="${{pod}}">
                        <div class="sigilli-card-header">
                            <div class="sigilli-card-cliente">${{cliente}}</div>
                            <div class="sigilli-card-badges">
                                <span class="sigilli-badge sigilli-badge-tipo ${{tipoClass}}">${{tipoIcon}} ${{tipo}}</span>
                                <span class="sigilli-badge sigilli-badge-data">📅 ${{dataInst}}</span>
                                <span class="sigilli-badge sigilli-badge-operatore">👤 ${{operatore}}</span>
                            </div>
                        </div>
                        <div class="sigilli-card-info">
                            <span>📍 ${{indirizzo}}, ${{comune}}</span>
                            <span>🔌 ${{eneltel}}</span>
                        </div>
                        <div class="sigilli-input-row">
                            <input type="text" 
                                   class="sigilli-input" 
                                   id="sigillo-input-${{originalIdx}}"
                                   placeholder="Inserisci codice sigillo..."
                                   autocomplete="off"
                                   onkeypress="handleSigilloKeypress(event, ${{originalIdx}})">
                            <button class="btn-salva-sigillo" 
                                    id="sigillo-btn-${{originalIdx}}"
                                    onclick="salvaSigillo(${{originalIdx}})">
                                💾 Salva
                            </button>
                        </div>
                        <div class="sigilli-card-status" id="sigillo-status-${{originalIdx}}"></div>
                    </div>
                `;
            }});
            
            html += '</div>';
            document.getElementById('sigilliContent').innerHTML = html;
            
            // Focus sul primo input visibile (solo se non stiamo cercando)
            if (!_sigilliSearchActive) {{
                setTimeout(() => {{
                    const firstInput = document.querySelector('.sigilli-input');
                    if (firstInput) firstInput.focus();
                }}, 100);
            }}
        }}
        
        function renderMatricoleList() {{
            const dataFiltrata = getMatricoleFiltrate();
            
            // Estrai lista operatori unici
            const operatoriSet = new Set();
            matricoleData.forEach(c => {{
                if (c.operatore) operatoriSet.add(c.operatore);
            }});
            const operatoriList = Array.from(operatoriSet).sort();
            
            // Genera HTML filtro operatore + ricerca
            let filterHtml = `
                <div class="sigilli-filter-row">
                    <span class="sigilli-filter-label">Filtra per operatore:</span>
                    <select class="sigilli-filter-select" onchange="cambiaFiltroOperatoreMatricole(this.value)">
                        <option value="">👥 Tutti (${{matricoleData.length}})</option>
                        ${{operatoriList.map(op => `
                            <option value="${{op}}" ${{filtroOperatoreMatricole === op ? 'selected' : ''}}>
                                👤 ${{op}} (${{matricoleData.filter(c => c.operatore === op).length}})
                            </option>
                        `).join('')}}
                    </select>
                    <input type="text" class="sigilli-search-input" placeholder="🔍 Cerca ENELTEL..." value="${{ricercaEneltel}}" oninput="handleSigilliSearch(this)">
                </div>
            `;
            
            if (dataFiltrata.length === 0) {{
                document.getElementById('sigilliContent').innerHTML = filterHtml + `
                    <div class="sigilli-empty">
                        <div class="sigilli-empty-icon">✅</div>
                        <div>Nessun contatore ${{filtroOperatoreMatricole ? 'per ' + filtroOperatoreMatricole : ''}} senza matricola!</div>
                        <div style="font-size: 0.85em; margin-top: 10px; color: var(--text-muted);">
                            Tutte le matricole sono state inserite.
                        </div>
                    </div>
                `;
                return;
            }}
            
            let html = filterHtml + '<div class="sigilli-list">';
            
            dataFiltrata.forEach((cont, idx) => {{
                // Trova l'indice originale per riferimento
                const originalIdx = matricoleData.indexOf(cont);
                const dataInst = cont.data_installazione || 'N/D';
                const operatore = cont.operatore || 'N/D';
                const pod = cont.pod || '';
                const eneltel = cont.eneltel || '';
                const cliente = cont.cliente || 'N/D';
                const indirizzo = cont.indirizzo || '';
                const comune = cont.comune || '';
                
                html += `
                    <div class="sigilli-card" id="matricola-card-${{originalIdx}}" data-eneltel="${{eneltel}}" data-pod="${{pod}}">
                        <div class="sigilli-card-header">
                            <div class="sigilli-card-cliente">${{cliente}}</div>
                            <div class="sigilli-card-badges">
                                <span class="sigilli-badge sigilli-badge-data">📅 ${{dataInst}}</span>
                                <span class="sigilli-badge sigilli-badge-operatore">👤 ${{operatore}}</span>
                            </div>
                        </div>
                        <div class="sigilli-card-info">
                            <span>📍 ${{indirizzo}}, ${{comune}}</span>
                            <span>🔌 ${{eneltel}}</span>
                        </div>
                        <div class="sigilli-input-row">
                            <input type="text"
                                   class="sigilli-input matricola"
                                   id="matricola-input-${{originalIdx}}"
                                   placeholder="Inserisci matricola..."
                                   autocomplete="off"
                                   onkeypress="handleMatricolaKeypress(event, ${{originalIdx}})">
                            <button class="btn-salva-sigillo"
                                    id="matricola-btn-${{originalIdx}}"
                                    onclick="salvaMatricola(${{originalIdx}})">
                                💾 Salva
                            </button>
                        </div>
                        <div class="smart-test-row">
                            <label class="smart-test-toggle" id="smart-test-toggle-${{originalIdx}}">
                                <input type="checkbox"
                                       id="smart-test-check-${{originalIdx}}"
                                       onchange="toggleSmartTest(${{originalIdx}})">
                                <span>⚠️ Smart Test negativo</span>
                            </label>
                            <input type="text"
                                   class="smart-test-spiegazione"
                                   id="smart-test-spieg-${{originalIdx}}"
                                   placeholder="Spiegazione (opzionale)..."
                                   autocomplete="off">
                        </div>
                        <div class="sigilli-card-status" id="matricola-status-${{originalIdx}}"></div>
                    </div>
                `;
            }});
            
            html += '</div>';
            document.getElementById('sigilliContent').innerHTML = html;
            
            // Focus sul primo input visibile (solo se non stiamo cercando)
            if (!_sigilliSearchActive) {{
                setTimeout(() => {{
                    const firstInput = document.querySelector('.sigilli-input.matricola');
                    if (firstInput) firstInput.focus();
                }}, 100);
            }}
        }}
        
        function handleSigilloKeypress(event, idx) {{
            if (event.key === 'Enter') {{
                salvaSigillo(idx);
            }}
        }}
        
        function handleMatricolaKeypress(event, idx) {{
            if (event.key === 'Enter') {{
                salvaMatricola(idx);
            }}
        }}
        
        async function salvaSigillo(idx) {{
            const card = document.getElementById(`sigillo-card-${{idx}}`);
            const input = document.getElementById(`sigillo-input-${{idx}}`);
            const btn = document.getElementById(`sigillo-btn-${{idx}}`);
            const status = document.getElementById(`sigillo-status-${{idx}}`);
            
            const sigillo = input.value.trim();
            if (!sigillo) {{
                status.className = 'sigilli-card-status visible error';
                status.textContent = '⚠️ Inserisci un codice sigillo';
                return;
            }}
            
            const eneltel = card.dataset.eneltel;
            
            // Loading state
            btn.disabled = true;
            input.disabled = true;
            status.className = 'sigilli-card-status visible loading';
            status.textContent = '⏳ Salvataggio...';
            
            // LCL dalla riga locale (ancora secondaria per duplicate_eneltel)
            let _sLcl = undefined;
            try {{
                const _c = sigilliData.find(cc => cc.eneltel === eneltel) ||
                           (typeof contatori !== 'undefined' && contatori.find(cc => cc.eneltel === eneltel));
                if (_c && _c.lcl !== undefined) _sLcl = String(_c.lcl || '');
            }} catch (e) {{ /* ignora */ }}

            try {{
                const _body = {{ sigillo: sigillo }};
                if (_sLcl !== undefined) _body._anchor_lcl = _sLcl;
                const response = await fetch(`${{API_BASE}}/api/sigillo/${{eneltel}}`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(_body)
                }});
                
                const result = await response.json();
                
                if (response.ok && result.success) {{
                    // RIMUOVI dalla lista dati PRIMA di toccare il DOM
                    sigilliData = sigilliData.filter(c => c.eneltel !== eneltel);
                    
                    // Aggiorna contatori (riacquisisci i nodi dal DOM, potrebbero essere cambiati)
                    const countEl = document.getElementById('sigilliCount');
                    if (countEl) countEl.textContent = `(${{sigilliData.length}} da completare)`;
                    const tabCountEl = document.getElementById('tabSigilliCount');
                    if (tabCountEl) tabCountEl.textContent = sigilliData.length;
                    
                    // Riacquisisci i nodi DOM (potrebbero essere stati ricreati da un re-render)
                    const card2 = document.getElementById(`sigillo-card-${{idx}}`);
                    const input2 = document.getElementById(`sigillo-input-${{idx}}`);
                    const btn2 = document.getElementById(`sigillo-btn-${{idx}}`);
                    const status2 = document.getElementById(`sigillo-status-${{idx}}`);
                    
                    if (card2) {{
                        status2.className = 'sigilli-card-status visible success';
                        status2.textContent = '✅ Sigillo salvato!';
                        card2.classList.add('completato');
                        
                        input2.value = sigillo;
                        input2.disabled = true;
                        btn2.style.display = 'none';
                        
                        let editBtn = document.getElementById(`sigillo-edit-${{idx}}`);
                        if (!editBtn) {{
                            editBtn = document.createElement('button');
                            editBtn.id = `sigillo-edit-${{idx}}`;
                            editBtn.className = 'btn-modifica-sigillo';
                            editBtn.innerHTML = '✏️ Modifica';
                            editBtn.onclick = function() {{ modificaSigillo(idx); }};
                            input2.parentNode.appendChild(editBtn);
                        }}
                        
                        focusNextInput(card2, 'sigillo-input');
                    }}
                    
                }} else {{
                    // Riacquisisci nodi DOM per mostrare errore
                    const btn2 = document.getElementById(`sigillo-btn-${{idx}}`);
                    const input2 = document.getElementById(`sigillo-input-${{idx}}`);
                    const status2 = document.getElementById(`sigillo-status-${{idx}}`);
                    if (btn2) btn2.disabled = false;
                    if (input2) input2.disabled = false;
                    if (status2) {{
                        status2.className = 'sigilli-card-status visible error';
                        status2.textContent = '❌ ' + (result.error || 'Errore sconosciuto');
                    }}
                }}
            }} catch (err) {{
                const btn2 = document.getElementById(`sigillo-btn-${{idx}}`);
                const input2 = document.getElementById(`sigillo-input-${{idx}}`);
                const status2 = document.getElementById(`sigillo-status-${{idx}}`);
                if (btn2) btn2.disabled = false;
                if (input2) input2.disabled = false;
                if (status2) {{
                    status2.className = 'sigilli-card-status visible error';
                    status2.textContent = '❌ Errore di connessione: ' + err.message;
                }}
            }}
        }}
        
        function toggleSmartTest(idx) {{
            const check = document.getElementById(`smart-test-check-${{idx}}`);
            const label = document.getElementById(`smart-test-toggle-${{idx}}`);
            const spieg = document.getElementById(`smart-test-spieg-${{idx}}`);
            if (!check || !spieg || !label) return;
            if (check.checked) {{
                label.classList.add('checked');
                spieg.classList.add('visible');
            }} else {{
                label.classList.remove('checked');
                spieg.classList.remove('visible');
            }}
        }}

        async function salvaMatricola(idx) {{
            const card = document.getElementById(`matricola-card-${{idx}}`);
            const input = document.getElementById(`matricola-input-${{idx}}`);
            const btn = document.getElementById(`matricola-btn-${{idx}}`);
            const status = document.getElementById(`matricola-status-${{idx}}`);
            const smartCheck = document.getElementById(`smart-test-check-${{idx}}`);
            const smartSpieg = document.getElementById(`smart-test-spieg-${{idx}}`);

            const matricola = input.value.trim();
            if (!matricola) {{
                status.className = 'sigilli-card-status visible error';
                status.textContent = '⚠️ Inserisci una matricola';
                return;
            }}

            const smartNegativo = !!(smartCheck && smartCheck.checked);
            const smartSpiegazione = smartSpieg ? smartSpieg.value.trim() : '';

            const eneltel = card.dataset.eneltel;

            // Loading state
            btn.disabled = true;
            input.disabled = true;
            if (smartCheck) smartCheck.disabled = true;
            if (smartSpieg) smartSpieg.disabled = true;
            status.className = 'sigilli-card-status visible loading';
            status.textContent = '⏳ Salvataggio...';

            let _mLcl = undefined;
            try {{
                const _c = matricoleData.find(cc => cc.eneltel === eneltel) ||
                           (typeof contatori !== 'undefined' && contatori.find(cc => cc.eneltel === eneltel));
                if (_c && _c.lcl !== undefined) _mLcl = String(_c.lcl || '');
            }} catch (e) {{ /* ignora */ }}

            try {{
                const _body = {{
                    matricola: matricola,
                    smart_test_negativo: smartNegativo,
                    smart_test_spiegazione: smartSpiegazione,
                }};
                if (_mLcl !== undefined) _body._anchor_lcl = _mLcl;
                const response = await fetch(`${{API_BASE}}/api/matricola/${{eneltel}}`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(_body)
                }});

                const result = await response.json();

                if (response.ok && result.success) {{
                    // RIMUOVI dalla lista dati PRIMA di toccare il DOM
                    matricoleData = matricoleData.filter(c => c.eneltel !== eneltel);

                    // Aggiorna contatori (riacquisisci nodi dal DOM)
                    const countEl = document.getElementById('sigilliCount');
                    if (countEl) countEl.textContent = `(${{matricoleData.length}} da completare)`;
                    const tabCountEl = document.getElementById('tabMatricoleCount');
                    if (tabCountEl) tabCountEl.textContent = matricoleData.length;

                    // Riacquisisci i nodi DOM (potrebbero essere stati ricreati)
                    const card2 = document.getElementById(`matricola-card-${{idx}}`);
                    const input2 = document.getElementById(`matricola-input-${{idx}}`);
                    const btn2 = document.getElementById(`matricola-btn-${{idx}}`);
                    const status2 = document.getElementById(`matricola-status-${{idx}}`);

                    if (card2) {{
                        if (status2) {{
                            status2.className = 'sigilli-card-status visible success';
                            const stMsg = result.smart_test ? ` (SMART TEST: ${{result.smart_test}})` : '';
                            status2.textContent = '✅ Matricola salvata!' + stMsg;
                        }}
                        card2.classList.add('completato');

                        if (input2) {{
                            input2.value = matricola;
                            input2.disabled = true;
                        }}
                        if (btn2) btn2.style.display = 'none';

                        let editBtn = document.getElementById(`matricola-edit-${{idx}}`);
                        if (!editBtn && input2) {{
                            editBtn = document.createElement('button');
                            editBtn.id = `matricola-edit-${{idx}}`;
                            editBtn.className = 'btn-modifica-sigillo';
                            editBtn.innerHTML = '✏️ Modifica';
                            editBtn.onclick = function() {{ modificaMatricola(idx); }};
                            input2.parentNode.appendChild(editBtn);
                        }}

                        focusNextInput(card2, 'matricola-input');
                    }}

                }} else {{
                    const btn2 = document.getElementById(`matricola-btn-${{idx}}`);
                    const input2 = document.getElementById(`matricola-input-${{idx}}`);
                    const status2 = document.getElementById(`matricola-status-${{idx}}`);
                    const smartCheck2 = document.getElementById(`smart-test-check-${{idx}}`);
                    const smartSpieg2 = document.getElementById(`smart-test-spieg-${{idx}}`);
                    if (btn2) btn2.disabled = false;
                    if (input2) input2.disabled = false;
                    if (smartCheck2) smartCheck2.disabled = false;
                    if (smartSpieg2) smartSpieg2.disabled = false;
                    if (status2) {{
                        status2.className = 'sigilli-card-status visible error';
                        status2.textContent = '❌ ' + (result.error || 'Errore sconosciuto');
                    }}
                }}
            }} catch (err) {{
                const btn2 = document.getElementById(`matricola-btn-${{idx}}`);
                const input2 = document.getElementById(`matricola-input-${{idx}}`);
                const status2 = document.getElementById(`matricola-status-${{idx}}`);
                const smartCheck2 = document.getElementById(`smart-test-check-${{idx}}`);
                const smartSpieg2 = document.getElementById(`smart-test-spieg-${{idx}}`);
                if (btn2) btn2.disabled = false;
                if (input2) input2.disabled = false;
                if (smartCheck2) smartCheck2.disabled = false;
                if (smartSpieg2) smartSpieg2.disabled = false;
                if (status2) {{
                    status2.className = 'sigilli-card-status visible error';
                    status2.textContent = '❌ Errore di connessione: ' + err.message;
                }}
            }}
        }}

        function modificaMatricola(idx) {{
            const card = document.getElementById(`matricola-card-${{idx}}`);
            const input = document.getElementById(`matricola-input-${{idx}}`);
            const btn = document.getElementById(`matricola-btn-${{idx}}`);
            const editBtn = document.getElementById(`matricola-edit-${{idx}}`);
            const status = document.getElementById(`matricola-status-${{idx}}`);
            const smartCheck = document.getElementById(`smart-test-check-${{idx}}`);
            const smartSpieg = document.getElementById(`smart-test-spieg-${{idx}}`);

            if (input) input.disabled = false;
            if (btn) {{ btn.style.display = ''; btn.disabled = false; }}
            if (card) card.classList.remove('completato');
            if (editBtn) editBtn.style.display = 'none';
            if (smartCheck) smartCheck.disabled = false;
            if (smartSpieg) smartSpieg.disabled = false;

            if (status) {{
                status.className = 'sigilli-card-status';
                status.textContent = '';
            }}

            if (input) {{ input.focus(); input.select(); }}
        }}
        
        function modificaSigillo(idx) {{
            const card = document.getElementById(`sigillo-card-${{idx}}`);
            const input = document.getElementById(`sigillo-input-${{idx}}`);
            const btn = document.getElementById(`sigillo-btn-${{idx}}`);
            const editBtn = document.getElementById(`sigillo-edit-${{idx}}`);
            const status = document.getElementById(`sigillo-status-${{idx}}`);
            
            // Riabilita input e pulsante salva
            input.disabled = false;
            btn.style.display = '';
            btn.disabled = false;
            card.classList.remove('completato');
            
            // Nascondi pulsante modifica
            if (editBtn) editBtn.style.display = 'none';
            
            // Reset status
            status.className = 'sigilli-card-status';
            status.textContent = '';
            
            // Focus sull'input
            input.focus();
            input.select();
        }}
        
        function focusNextInput(currentCard, inputPrefix) {{
            setTimeout(() => {{
                const nextCard = currentCard.nextElementSibling;
                if (nextCard && !nextCard.classList.contains('completato')) {{
                    const nextInput = nextCard.querySelector('.' + (inputPrefix.includes('matricola') ? 'sigilli-input.matricola' : 'sigilli-input:not(.matricola)'));
                    if (nextInput) nextInput.focus();
                }}
            }}, 300);
        }}
        
        function chiudiVistaSigilli() {{
            document.getElementById('sigilliModal').classList.remove('visible');
            sigilliData = [];
            matricoleData = [];
            currentTab = 'sigilli';
        }}
        
        // ========================================
        // BACKUP / RIPRISTINO FILONE
        // ========================================
        
        function apriModalBackup() {{
            document.getElementById('backupModal').classList.add('visible');
            caricaListaBackup();
        }}
        
        function chiudiModalBackup() {{
            document.getElementById('backupModal').classList.remove('visible');
        }}
        
        async function caricaListaBackup() {{
            const container = document.getElementById('backupLista');
            container.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">⏳ Caricamento...</div>';
            
            try {{
                const resp = await fetch('/api/backup/lista');
                const data = await resp.json();
                
                if (!data.success) {{
                    container.innerHTML = '<div class="backup-empty">❌ ' + (data.error || 'Errore') + '</div>';
                    return;
                }}
                
                const backups = data.backups || [];
                
                if (backups.length === 0) {{
                    container.innerHTML = '<div class="backup-empty">📭 Nessun backup disponibile.<br>Verrà creato automaticamente al prossimo sync.</div>';
                    return;
                }}
                
                let html = '';
                for (const b of backups) {{
                    const dataFormatted = b.data_backup ? new Date(b.data_backup + 'T00:00:00').toLocaleDateString('it-IT', {{
                        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
                    }}) : 'Data sconosciuta';
                    
                    const tsFormatted = b.timestamp ? new Date(b.timestamp).toLocaleString('it-IT') : '';
                    
                    html += `
                        <div class="backup-item">
                            <div class="backup-item-info">
                                <div class="backup-item-date">📅 ${{dataFormatted}}</div>
                                <div class="backup-item-meta">
                                    ${{b.totale_righe}} righe · ${{b.dimensione_mb}} MB · ${{tsFormatted}}
                                </div>
                            </div>
                            <div class="backup-item-actions">
                                <button class="backup-btn-excel" onclick="scaricaBackupExcel('${{b.filename}}')" title="Scarica come Excel">
                                    📥 Excel
                                </button>
                                <button class="backup-btn-ripristina" onclick="ripristinaBackup('${{b.filename}}', '${{dataFormatted}}')">
                                    🔄 Ripristina
                                </button>
                                <button class="backup-btn-elimina" onclick="eliminaBackup('${{b.filename}}')">
                                    🗑️
                                </button>
                            </div>
                        </div>
                    `;
                }}
                
                container.innerHTML = html;
                
            }} catch (err) {{
                container.innerHTML = '<div class="backup-empty">❌ Errore di connessione: ' + err.message + '</div>';
            }}
        }}
        
        function mostraBackupStatus(msg, tipo) {{
            const el = document.getElementById('backupStatus');
            el.style.display = 'block';
            el.textContent = msg;
            if (tipo === 'success') {{
                el.style.background = 'linear-gradient(135deg, #27ae60 0%, #2ecc71 100%)';
                el.style.color = 'white';
            }} else if (tipo === 'error') {{
                el.style.background = 'linear-gradient(135deg, #e74c3c 0%, #c0392b 100%)';
                el.style.color = 'white';
            }} else {{
                el.style.background = 'linear-gradient(135deg, #3498db 0%, #2980b9 100%)';
                el.style.color = 'white';
            }}
            // Nascondi dopo 5 secondi
            setTimeout(() => {{ el.style.display = 'none'; }}, 5000);
        }}
        
        async function eseguiBackupManuale(forza) {{
            mostraBackupStatus('⏳ Backup in corso...', 'loading');
            
            try {{
                const resp = await fetch('/api/backup/esegui', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ forza: forza }})
                }});
                const data = await resp.json();
                
                if (data.success) {{
                    mostraBackupStatus('✅ ' + data.message, 'success');
                    caricaListaBackup();
                }} else {{
                    mostraBackupStatus('❌ ' + (data.message || data.error), 'error');
                }}
            }} catch (err) {{
                mostraBackupStatus('❌ Errore: ' + err.message, 'error');
            }}
        }}
        
        async function ripristinaBackup(filename, dataLabel) {{
            const conferma = confirm(
                `⚠️ ATTENZIONE: Stai per ripristinare il backup del ${{dataLabel}}.\\n\\n` +
                `Questa operazione SOVRASCRIVERÀ tutti i dati attuali del FILONE su Google Drive!\\n\\n` +
                `Sei sicuro di voler procedere?`
            );
            
            if (!conferma) return;
            
            // Doppia conferma per sicurezza
            const conferma2 = confirm(
                `🔴 ULTIMA CONFERMA\\n\\n` +
                `Il FILONE attuale verrà COMPLETAMENTE sostituito con il backup del ${{dataLabel}}.\\n\\n` +
                `Confermi il ripristino?`
            );
            
            if (!conferma2) return;
            
            mostraBackupStatus('⏳ Ripristino in corso... (può richiedere qualche minuto)', 'loading');
            
            try {{
                const resp = await fetch('/api/backup/ripristina', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ filename: filename }})
                }});
                const data = await resp.json();
                
                if (data.success) {{
                    mostraBackupStatus('✅ ' + data.message, 'success');
                    // Notifica che ci sarà un sync
                    setTimeout(() => {{
                        mostraBackupStatus('🔄 Sync mappa in corso...', 'loading');
                    }}, 3000);
                }} else {{
                    mostraBackupStatus('❌ ' + (data.message || data.error), 'error');
                }}
            }} catch (err) {{
                mostraBackupStatus('❌ Errore: ' + err.message, 'error');
            }}
        }}
        
        async function eliminaBackup(filename) {{
            if (!confirm('Vuoi eliminare questo backup?')) return;
            
            try {{
                const resp = await fetch('/api/backup/elimina', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ filename: filename }})
                }});
                const data = await resp.json();
                
                if (data.success) {{
                    mostraBackupStatus('🗑️ ' + data.message, 'success');
                    caricaListaBackup();
                }} else {{
                    mostraBackupStatus('❌ ' + (data.message || data.error), 'error');
                }}
            }} catch (err) {{
                mostraBackupStatus('❌ Errore: ' + err.message, 'error');
            }}
        }}
        
        function scaricaBackupExcel(filename) {{
            // Download diretto tramite link
            window.location.href = '/api/backup/scarica_excel?filename=' + encodeURIComponent(filename);
        }}
    '''


def genera_html_gestionale():
    """Genera l'HTML per i modali e componenti del gestionale."""
    
    return '''
        <!-- MODAL FULLSCREEN MODIFICA -->
        <div id="fullscreenEditModal" class="fullscreen-edit-modal">
            <div class="fullscreen-edit-container">
                <div class="fullscreen-edit-header">
                    <div class="fullscreen-edit-title">
                        ✏️ Modifica Contatore
                        <span id="fullscreenEneltel" class="fullscreen-edit-eneltel"></span>
                    </div>
                    <button class="btn-close-fullscreen" onclick="chiudiVistaModifica()">
                        ✕ Chiudi
                    </button>
                </div>
                
                <!-- Info card (stile sidebar) -->
                <div class="fullscreen-info-card">
                    <div class="fullscreen-info-header">
                        <div id="fullscreenCliente" class="fullscreen-info-cliente"></div>
                        <div class="fullscreen-info-badges">
                            <span id="fullscreenTappaBadge" class="fullscreen-badge fullscreen-badge-tappa"></span>
                            <span id="fullscreenTecnicoBadge" class="fullscreen-badge fullscreen-badge-tecnico"></span>
                        </div>
                    </div>
                    <div id="fullscreenInfoRows"></div>
                </div>
                
                <!-- Status messaggi -->
                <div id="fullscreenStatus" class="fullscreen-status"></div>

                <!-- Form modifica -->
                <div class="fullscreen-edit-section">
                    <div class="fullscreen-section-title">📝 Dati modificabili</div>
                    <div id="fullscreenEditFields" class="fullscreen-edit-grid"></div>
                </div>
                
                <!-- Sezione Appuntamento inline -->
                <div class="fullscreen-appuntamento-section">
                    <div class="fullscreen-section-title">📞 Fissa Appuntamento</div>
                    <p style="color: var(--text-secondary); margin-bottom: 15px; font-size: 0.9em;">
                        Imposta un orario per l'appuntamento. Verrà aggiunto alle note esistenti.
                    </p>
                    <div class="appuntamento-inline-row">
                        <div class="appuntamento-time-group">
                            <input type="time" id="fullscreenAppOrarioInizio" class="appuntamento-time-input" value="09:00">
                            <span class="appuntamento-time-sep">-</span>
                            <input type="time" id="fullscreenAppOrarioFine" class="appuntamento-time-input" placeholder="fine">
                        </div>
                        <button class="btn-fissa-appuntamento" id="btnFissaAppuntamento" onclick="fissaAppuntamentoInline()">
                            ✅ Fissa Appuntamento
                        </button>
                        <button class="btn-fissa-appuntamento" id="btnRimuoviAppuntamento" onclick="rimuoviAppuntamentoGestionale(currentFullscreenEneltel)" style="background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%); display: none;">
                            🗑️ Rimuovi
                        </button>
                    </div>
                    <div id="appuntamentoInlineStatus" class="appuntamento-status"></div>
                </div>

                <!-- Sezione Access Point (UPS-style) -->
                <div class="fullscreen-edit-section access-point-section" id="accessPointSection">
                    <div class="fullscreen-section-title">📍 Punto di accesso (per il navigatore)</div>
                    <p style="color: var(--text-secondary); margin-bottom: 12px; font-size: 0.9em;">
                        Indica dove il tecnico deve fermarsi (es. lato strada corretto). Il marker blu è il fabbricato (dato grezzo), il marker rosso è l'<b>access point</b> usato dall'algoritmo del percorso.
                        Trascina il rosso o clicca sulla strada per cambiarlo.
                    </p>
                    <div id="accessPointStatusRow" class="access-point-status-row">
                        <span id="accessPointSourceBadge" class="access-point-badge"></span>
                        <span id="accessPointRoadLabel" class="access-point-road"></span>
                    </div>
                    <div id="accessPointMap" class="access-point-map"></div>
                    <div class="access-point-actions">
                        <button type="button" class="btn-access-save" onclick="salvaAccessPoint()">
                            💾 Salva access point
                        </button>
                        <button type="button" class="btn-access-snap" onclick="snapAccessPointAStrada()" title="Aggancia il marker rosso alla strada più vicina">
                            🛣️ Snap a strada
                        </button>
                        <button type="button" class="btn-access-undo" id="btnAccessPointUndo" onclick="undoAccessPoint()" disabled title="Annulla l'ultima modifica all'access point">
                            ↩️ Annulla ultima modifica
                        </button>
                        <button type="button" class="btn-access-reset" onclick="resetAccessPointAuto()" title="Rimuove l'override e torna allo snap automatico">
                            🔄 Auto (rimuovi override)
                        </button>
                    </div>
                    <div id="accessPointStatusMsg" class="access-point-status"></div>
                </div>

                <!-- Footer azioni -->
                <div class="fullscreen-edit-footer">
                    <button class="btn-fullscreen-save" onclick="salvaModificheFullscreen()">
                        💾 Salva modifiche
                    </button>
                    <button class="btn-fullscreen-cancel" onclick="chiudiVistaModifica()">
                        Annulla
                    </button>
                </div>
            </div>
        </div>
        
        <!-- MODAL APPUNTAMENTO -->
        <div id="appuntamentoModal" class="appuntamento-modal">
            <div class="appuntamento-content">
                <div class="BOXBEAT-header">
                    <span class="BOXBEAT-title">📞 Fissa Appuntamento</span>
                    <button class="btn-close-modal" onclick="chiudiModalAppuntamento()">×</button>
                </div>
                
                <p style="color: var(--text-secondary); margin-bottom: 15px;">
                    Inserisci l'orario dell'appuntamento. L'orario verrà aggiunto alle note.
                </p>
                
                <div class="time-picker-group">
                    <input type="time" id="appOrarioInizio" class="time-input" value="09:00">
                    <span class="time-separator">-</span>
                    <input type="time" id="appOrarioFine" class="time-input" placeholder="(opzionale)">
                </div>
                
                <div class="popup-edit-actions">
                    <button class="btn-save-edit" onclick="confermaAppuntamento()">✅ Conferma</button>
                    <button class="btn-cancel-edit" onclick="chiudiModalAppuntamento()">Annulla</button>
                </div>
            </div>
        </div>
        
        <!-- MODAL BOXBEAT -->
        <div id="BOXBEATModal" class="BOXBEAT-modal">
            <div class="BOXBEAT-content">
                <div class="BOXBEAT-header">
                    <span class="BOXBEAT-title">📦 Carica installato (Enel)</span>
                    <button class="btn-close-modal" onclick="chiudiModalBOXBEAT()">×</button>
                </div>
                
                <p style="color: var(--text-secondary); margin-bottom: 15px;">
                    Carica il file esportato da Enel. Verranno aggiornati solo i contatori con <strong>Esito OK</strong>.
                </p>
                
                <div id="BOXBEATDropzone" class="BOXBEAT-dropzone" 
                     onclick="selectBOXBEATFile()"
                     ondrop="handleBOXBEATDrop(event)"
                     ondragover="handleBOXBEATDragOver(event)"
                     ondragleave="handleBOXBEATDragLeave(event)">
                    <div class="BOXBEAT-dropzone-icon">📁</div>
                    <div class="BOXBEAT-dropzone-text">Trascina qui i file o clicca per selezionare</div>
                    <div class="BOXBEAT-dropzone-subtext">Puoi caricare più file insieme (.xlsx, .xls, .csv)</div>
                </div>
                
                <input type="file" id="BOXBEATFileInput" style="display: none;" 
                       accept=".xlsx,.xls,.csv" multiple onchange="handleBOXBEATFileSelect(event)">
                
                <div class="BOXBEAT-progress">
                    <div class="BOXBEAT-progress-bar">
                        <div class="BOXBEAT-progress-fill"></div>
                    </div>
                    <div class="BOXBEAT-progress-text">Elaborazione...</div>
                </div>
                
                <div class="BOXBEAT-result"></div>
            </div>
        </div>
        
        <!-- MODAL SIGILLI/MATRICOLE FULLSCREEN -->
        <div id="sigilliModal" class="sigilli-modal">
            <div class="sigilli-container">
                <!-- Tab per switch -->
                <div class="sigilli-tabs">
                    <button id="tabSigilli" class="sigilli-tab active" onclick="switchTab('sigilli')">
                        🔐 Sigilli
                        <span id="tabSigilliCount" class="sigilli-tab-count">0</span>
                    </button>
                    <button id="tabMatricole" class="sigilli-tab" onclick="switchTab('matricole')">
                        🔢 Matricole
                        <span id="tabMatricoleCount" class="sigilli-tab-count">0</span>
                    </button>
                </div>
                
                <div id="sigilliHeader" class="sigilli-header">
                    <div id="sigilliTitle" class="sigilli-title">
                        🔐 Inserisci Sigilli
                        <span id="sigilliCount" class="sigilli-count"></span>
                    </div>
                    <button class="btn-close-fullscreen" onclick="chiudiVistaSigilli()">
                        ✕ Chiudi
                    </button>
                </div>
                
                <div id="sigilliContent">
                    <div class="sigilli-loading">
                        <span class="sigilli-loading-spinner">⏳</span>
                        Caricamento contatori installati...
                    </div>
                </div>
            </div>
        </div>
        
        <!-- MODAL BACKUP / RIPRISTINO -->
        <div id="backupModal" class="BOXBEAT-modal">
            <div class="BOXBEAT-content" style="max-width: 650px;">
                <div class="BOXBEAT-header">
                    <span class="BOXBEAT-title">💾 Backup / Ripristino FILONE</span>
                    <button class="btn-close-modal" onclick="chiudiModalBackup()">×</button>
                </div>
                
                <p style="color: var(--text-secondary); margin-bottom: 15px;">
                    Il backup viene eseguito automaticamente ogni giorno. Puoi anche forzarlo manualmente o ripristinare una versione precedente.
                </p>
                
                <!-- Pulsante backup manuale -->
                <div style="margin-bottom: 20px;">
                    <button class="btn-save-edit" onclick="eseguiBackupManuale(true)" style="width: 100%;">
                        💾 Forza Backup
                    </button>
                </div>
                
                <div id="backupStatus" style="display: none; padding: 12px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em;"></div>
                
                <!-- Lista backup disponibili -->
                <div class="backup-section-title" style="font-weight: 600; color: var(--accent); margin-bottom: 10px; font-size: 0.95em;">
                    📋 Backup disponibili
                </div>
                <div id="backupLista" style="max-height: 400px; overflow-y: auto;">
                    <div style="text-align: center; padding: 20px; color: var(--text-secondary);">
                        ⏳ Caricamento...
                    </div>
                </div>
            </div>
        </div>
    '''


def genera_pulsanti_sidebar():
    """I pulsanti gestionale sono ora nel pannello flottante esterno alla sidebar."""
    return ''
