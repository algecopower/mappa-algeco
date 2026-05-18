// theme-init.js — inizializzazione comune per tutte le pagine che usano
// theme.css. Converte i tag <i data-lucide="X"> in SVG quando il DOM
// è pronto, ed espone window.refreshLucideIcons() per chiamare la
// stessa logica dopo update dinamici via innerHTML.

(function () {
    function init() {
        if (typeof lucide !== 'undefined' && lucide.createIcons) {
            lucide.createIcons();
        }
    }
    window.refreshLucideIcons = init;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
