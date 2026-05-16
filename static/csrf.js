// CSRF token (double-cookie pattern).
// Monkey-patch globale di fetch: aggiunge l'header X-CSRF-Token su ogni
// POST/PUT/DELETE/PATCH, leggendolo dal cookie 'csrf_token' che il server
// imposta al login (vedi server.py:set_csrf_cookie).
(function () {
    function getCsrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    const UNSAFE = new Set(['POST', 'PUT', 'DELETE', 'PATCH']);
    const _originalFetch = window.fetch.bind(window);

    window.fetch = function (input, init) {
        init = init || {};
        const method = (init.method || (input && input.method) || 'GET').toUpperCase();
        if (UNSAFE.has(method)) {
            const token = getCsrfToken();
            if (token) {
                const headers = new Headers(init.headers || (input && input.headers) || {});
                if (!headers.has('X-CSRF-Token')) {
                    headers.set('X-CSRF-Token', token);
                }
                init.headers = headers;
            }
        }
        return _originalFetch(input, init);
    };
})();
