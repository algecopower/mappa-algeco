// theme-init.js — inizializzazione comune per tutte le pagine che usano
// theme.css. Converte i tag <i data-lucide="X"> in SVG quando il DOM
// è pronto, ed espone window.refreshLucideIcons() per chiamare la
// stessa logica dopo update dinamici via innerHTML.

(function () {
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
        return m ? decodeURIComponent(m[1]) : null;
    }

    function getStored(name) {
        try {
            return localStorage.getItem(name);
        } catch (_) {
            return null;
        }
    }

    var root = document.documentElement;
    var theme = getCookie('algeco_theme') || getStored('algeco_theme') || getStored('admin_theme') || root.dataset.theme || 'dark';
    root.setAttribute('data-theme', theme);

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
