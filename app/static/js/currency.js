/**
 * JPY to CNY currency conversion using free API.
 * https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/jpy.json
 */
const CurrencyService = {
    rate: null,
    _initPromise: null,

    async init() {
        if (this.rate !== null) return;
        if (this._initPromise) return this._initPromise;

        this._initPromise = (async () => {
            try {
                const res = await fetch('https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/jpy.json');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.rate = data.jpy?.cny;
                if (!this.rate) throw new Error('CNY rate not found in response');
                console.log(`Currency rate loaded: 1 JPY = ${this.rate} CNY`);
            } catch (e) {
                console.warn('Currency API failed, trying fallback:', e.message);
                try {
                    const res2 = await fetch('https://latest.currency-api.pages.dev/v1/currencies/jpy.json');
                    if (res2.ok) {
                        const data2 = await res2.json();
                        this.rate = data2.jpy?.cny;
                    }
                } catch (e2) {
                    console.warn('Fallback also failed:', e2.message);
                }
            }
        })();

        return this._initPromise;
    },

    /**
     * Parse Japanese yen text to a numeric value in yen.
     * Handles: "8.5万円", "132,000円", "13.2万円", "-" etc.
     */
    parseJPY(text) {
        if (!text || text === '-' || text === 'なし') return null;

        // Remove whitespace
        text = text.trim();

        // Pattern: X万円 (e.g., "8.5万円", "13.2万円")
        const manMatch = text.match(/([\d,.]+)\s*万\s*円?/);
        if (manMatch) {
            return parseFloat(manMatch[1].replace(/,/g, '')) * 10000;
        }

        // Pattern: plain number with 円 (e.g., "132000円", "15,000円")
        const yenMatch = text.match(/([\d,]+)\s*円/);
        if (yenMatch) {
            return parseInt(yenMatch[1].replace(/,/g, ''), 10);
        }

        // Just a number
        const numMatch = text.match(/^[\d,.]+$/);
        if (numMatch) {
            return parseFloat(text.replace(/,/g, ''));
        }

        return null;
    },

    convert(jpyAmount) {
        if (!this.rate || jpyAmount === null) return null;
        return Math.round(jpyAmount * this.rate);
    },

    /**
     * Format a JPY text string by appending CNY equivalent.
     * "8.5万円" -> "8.5万円 (约¥3,944元)"
     */
    format(jpyText) {
        if (!jpyText || !this.rate) return jpyText;

        const num = this.parseJPY(jpyText);
        if (num === null) return jpyText;

        const cny = this.convert(num);
        if (cny === null) return jpyText;

        return `${jpyText} (约¥${cny.toLocaleString()}元)`;
    }
};
