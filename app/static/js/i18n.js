/**
 * Lightweight i18n engine for Chinese/Japanese UI switching.
 * Uses data-i18n attributes on HTML elements.
 *
 * Usage:
 *   <span data-i18n="nav.home">首页</span>
 *   await i18n.init();
 */
const i18n = {
    locale: localStorage.getItem('locale') || 'zh',
    translations: {},
    _loaded: false,
    _changeCallbacks: [],

    async init() {
        await this.load(this.locale);
    },

    async load(locale) {
        try {
            const res = await fetch(`/static/i18n/${locale}.json`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            this.translations = await res.json();
            this.locale = locale;
            localStorage.setItem('locale', locale);
            this._loaded = true;
            this.apply();
            this._updateToggleLabel();
            this._changeCallbacks.forEach(fn => fn(this.locale));
        } catch (e) {
            console.error(`Failed to load i18n/${locale}.json:`, e);
        }
    },

    apply() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.dataset.i18n;
            const text = this.translations[key];
            if (text) {
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    el.placeholder = text;
                } else {
                    // Use innerText instead of textContent to avoid DOM restructuring
                    el.innerText = text;
                }
            }
        });
    },

    t(key, params) {
        let text = this.translations[key] || key;
        if (params) {
            Object.entries(params).forEach(([k, v]) => {
                text = text.replace(`{${k}}`, v);
            });
        }
        return text;
    },

    toggle() {
        const next = this.locale === 'zh' ? 'ja' : 'zh';
        this.load(next);
    },

    onChange(fn) {
        this._changeCallbacks.push(fn);
    },

    _updateToggleLabel() {
        const btn = document.getElementById('langToggle');
        if (btn) {
            const label = btn.querySelector('.lang-label');
            if (label) {
                label.textContent = this.translations['nav.lang'] || (this.locale === 'zh' ? '日本語' : '中文');
            }
        }
    }
};
