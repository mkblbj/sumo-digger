/**
 * Bilingual display utilities for SUUMO property data.
 * - Japanese → Chinese field name mapping
 * - Dot-key → Chinese label mapping (for normalized data)
 * - Bilingual field name rendering
 * - Compatibility getter for old/new data formats
 * - Click-to-copy for table cells
 */

// ── Japanese → Chinese field name mapping (old format) ──
const FIELD_NAME_ZH = {
    '物件名': '物件名',
    '賃料': '租金',
    '管理費・共益費': '管理费/公益费',
    '敷金': '押金',
    '礼金': '礼金',
    '保証金': '保证金',
    '敷引・償却': '退租扣除/折旧',
    '間取り': '户型',
    '専有面積': '专有面积',
    '向き': '朝向',
    '建物種別': '建筑类型',
    '築年数': '房龄',
    '築年月': '建筑年月',
    '階': '楼层',
    '階建': '总层数',
    'アクセス1': '交通1',
    'アクセス2': '交通2',
    'アクセス3': '交通3',
    '所在地': '地址',
    '住所': '地址',
    '部屋の特徴・設備': '房间特征/设备',
    '駅徒歩': '车站步行',
    '駐車場': '停车场',
    '構造': '结构',
    '建物構造': '建筑结构',
    '取引態様': '交易形式',
    '現況': '现况',
    '引渡し時期': '交房时间',
    '入居': '入住',
    '契約期間': '合同期限',
    '条件': '条件',
    '損害保険': '损害保险',
    '仲介手数料': '中介费',
    '総戸数': '总户数',
    '備考': '备注',
    '設備・条件': '设备/条件',
    'SUUMO物件コード': 'SUUMO物件编号',
    'ほか初期費用': '其他初期费用',
    'ほか諸費用': '其他费用',
    '情報公開日': '信息公开日',
    '次回更新日': '下次更新日',
    '物件番号': '物件编号',
    '管理形態': '管理形式',
    '管理会社': '管理公司',
    '施工会社': '施工公司',
    '土地権利': '土地权利',
    '国土法届出': '国土法申报',
    'リフォーム': '翻新',
    '用途地域': '用途地域',
    '建ぺい率': '建蔽率',
    '容積率': '容积率',
    'バイク置場': '摩托车停车处',
    '駐輪場': '自行车停车处',
    '物件概要': '物件概要',
    '周辺環境': '周边环境',
    '入居条件': '入住条件',
    '契約条件': '合同条件',
    'その他': '其他',
    '販売価格': '售价',
    '価格': '价格',
    '土地面積': '土地面积',
    '建物面積': '建筑面积',
    '特徴': '特征',
    '保険等': '保险等',
    '更新料': '续约费',
    '保証会社': '担保公司',
    '即入居可': '可立即入住',
    '間取り詳細': '户型详情',
};

// ── Dot-key → Chinese label mapping (normalized data format) ──
const DOT_KEY_LABELS = {
    'media.images': '图片', 'media.videos': '视频', 'media.vr_links': 'VR链接',
    'basic.city_ward': '所在城市', 'basic.property_name': '物件名称',
    'basic.property_type': '物件类型', 'basic.tags': '物件标签',
    'basic.price_jpy': '售价（日元）', 'basic.price_cny': '售价（人民币）',
    'basic.layout_cn': '户型', 'basic.area': '面积',
    'basic.land_area': '土地面积', 'basic.building_area': '建筑面积',
    'basic.unit_price': '单价', 'basic.estimated_rent': '预估租金',
    'basic.address': '地址', 'basic.longitude': '经度', 'basic.latitude': '纬度',
    'basic.nearby_landmark': '附近标志', 'basic.built_month': '建成日期',
    'basic.monthly_rent': '月租金', 'basic.estimated_roi_pct': '回报率',
    'detail.building_name': '物件名', 'detail.access': '交通',
    'detail.total_units': '总户数', 'detail.sub_type': '类型',
    'detail.structure': '构造', 'detail.orientation': '朝向',
    'detail.exclusive_area': '专有面积', 'detail.other_areas': '其他面积',
    'detail.floor': '楼层', 'detail.total_floors': '总楼层',
    'detail.land_or_invest_status': '现状', 'detail.total_lots': '总区划数',
    'detail.property_status': '物件现状', 'detail.parking': '停车场',
    'detail.transaction_form': '交易形式', 'detail.remark': '备注',
    'rent.rent': '房租', 'rent.deposit': '押金', 'rent.key_money': '礼金',
    'rent.common_service_fee': '共益费', 'rent.contract_term': '合同期',
    'rent.initial_rent': '租金', 'rent.initial_total_fee': '总费用',
    'analysis.property_price': '物件价格', 'analysis.acquisition_tax': '不动产取得税',
    'analysis.registration_tax': '登录免许税', 'analysis.stamp_tax': '印花税',
    'analysis.scrivener_fee': '司法书士费', 'analysis.brokerage_fee': '中介费',
    'analysis.total_spend': '实际总支出', 'analysis.fixed_asset_tax': '固定资产税',
    'analysis.city_planning_tax': '都市计划税', 'analysis.trust_fee': '托管费',
    'analysis.annual_total_cost': '一年总成本',
    'analysis.monthly_rent_income': '月租金收入', 'analysis.annual_rent_income': '年租金收入',
    'analysis.annual_roi_pct': '年回报率',
    'analysis_or_detail.management_fee': '管理费', 'analysis_or_detail.repair_fee': '修缮费',
    'analysis_or_detail.other_fees': '其他费用',
    'land.rights': '土地权利', 'land.building_coverage_pct': '建蔽率',
    'land.far_pct': '容积率', 'land.zoning': '用途地域',
    'land.private_road_burden': '私道负担', 'land.restrictions': '限制事项',
    'management.method': '管理方式', 'management.company': '管理公司',
    'building.constructor': '施工公司', 'building.built_month': '建成时间',
    'building.total_area': '总面积', 'building.total_units': '总套数',
    'building.seismic_type': '抗震构造', 'building.location': '所在地',
    'building.renovation': '翻新',
    'deal.delivery_text': '引渡时间文本', 'deal.delivery_time': '引渡时间',
    'amenities.facilities': '配套设施',
    'ai.description_candidates': '物件介绍',
    'poi.schools': '学校', 'poi.business_districts': '商圈', 'poi.parks': '公园设施',
};

/**
 * Get a property value trying multiple possible keys (old JP + new dot-key format).
 * Returns the first non-null/non-undefined/non-empty match, or defaultVal.
 * @param {object} p - property data object
 * @param {string[]} keys - list of keys to try, in priority order
 * @param {*} defaultVal - fallback value (default '-')
 */
function pGet(p, keys, defaultVal) {
    if (defaultVal === undefined) defaultVal = '-';
    for (const k of keys) {
        const v = p[k];
        if (v !== undefined && v !== null && v !== '') return v;
    }
    return defaultVal;
}

/**
 * Check if a property uses the new normalized dot-key format.
 */
function isNormalizedData(p) {
    return p.hasOwnProperty('_property_type') || p.hasOwnProperty('basic.address') || p.hasOwnProperty('basic.price_jpy');
}

// ── Helper: escape HTML ──
function _escHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
}

/**
 * Render a customer-facing field name.
 * Only show the Chinese label and never expose internal keys.
 */
function bilingualFieldName(fieldKey) {
    const dotLabel = DOT_KEY_LABELS[fieldKey];
    if (dotLabel) {
        return _escHtml(dotLabel);
    }

    const zhName = FIELD_NAME_ZH[fieldKey];
    if (zhName) {
        return _escHtml(zhName);
    }

    return _escHtml(fieldKey);
}

/**
 * Get the main text of a cell (excluding translation small elements).
 */
function getCellMainText(cell) {
    const clone = cell.cloneNode(true);
    clone.querySelectorAll('small').forEach(el => el.remove());
    clone.querySelectorAll('br').forEach(el => el.remove());
    return clone.textContent.trim();
}

/**
 * Copy text to clipboard with HTTP fallback.
 * navigator.clipboard requires HTTPS; on HTTP we use execCommand fallback.
 * Bootstrap modal focus-trap workaround: append textarea inside the open modal.
 */
function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }
    // Fallback for HTTP (non-secure context)
    const el = document.createElement('textarea');
    el.value = text;
    el.setAttribute('readonly', '');
    el.style.position = 'fixed';
    el.style.top = '0';
    el.style.left = '0';
    el.style.width = '2em';
    el.style.height = '2em';
    el.style.padding = '0';
    el.style.border = 'none';
    el.style.outline = 'none';
    el.style.boxShadow = 'none';
    el.style.background = 'transparent';
    el.style.zIndex = '99999';
    // Append inside open Bootstrap modal to bypass focus-trap, else body
    const openModal = document.querySelector('.modal.show .modal-body');
    (openModal || document.body).appendChild(el);
    el.focus();
    el.select();
    el.setSelectionRange(0, el.value.length); // For mobile compatibility
    let success = false;
    try {
        success = document.execCommand('copy');
    } catch (err) {
        console.error('[copy] execCommand failed:', err);
    }
    el.parentNode.removeChild(el);
    if (!success) {
        console.warn('[copy] execCommand returned false for:', text.substring(0, 50));
    }
    return Promise.resolve(success);
}

/**
 * Show a brief copy confirmation toast.
 */
function showCopyToast(text) {
    let toast = document.getElementById('copyToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'copyToast';
        toast.style.cssText = 'position:fixed;top:20px;right:20px;background:#198754;color:#fff;padding:8px 16px;border-radius:6px;font-size:0.85rem;z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none;max-width:400px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
        document.body.appendChild(toast);
    }
    const display = text.length > 50 ? text.substring(0, 50) + '...' : text;
    toast.textContent = '✓ 已复制: ' + display;
    toast.style.opacity = '1';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 1500);
}

/**
 * Render a bilingual value: Japanese original on top, Chinese translation below.
 * @param {string} jaValue - Original Japanese value
 * @param {string|null} cnValue - Chinese translation (null if not available or same as original)
 * @param {object} opts - Options: { escape: true, cssClass: 'text-success' }
 * @returns {string} HTML string
 */
function bilingualValue(jaValue, cnValue, opts = {}) {
    const escape = opts.escape !== false;
    const cls = opts.cssClass || 'text-success';
    const ja = escape ? _escHtml(String(jaValue || '-')) : String(jaValue || '-');
    if (!cnValue || cnValue === jaValue) return ja;
    const cn = escape ? _escHtml(String(cnValue)) : String(cnValue);
    return `${ja}<br><small class="${cls}">${cn}</small>`;
}

/**
 * Build a bilingual table row: field name (JP+CN) | value (JP+CN translation if available).
 * @param {string} key - Japanese field name
 * @param {string} jaValue - Japanese value
 * @param {string|null} cnValue - Chinese translated value (or null)
 * @param {function|null} valueFmt - Optional formatter for the JP value (e.g. currency, area)
 * @returns {string} HTML <tr> string
 */
function bilingualTableRow(key, jaValue, cnValue, valueFmt) {
    const thHtml = bilingualFieldName(key);
    let formattedJa = valueFmt ? valueFmt(String(jaValue || '-')) : _escHtml(String(jaValue || '-'));
    let cnHtml = '';
    if (cnValue && cnValue !== jaValue) {
        const formattedCn = valueFmt ? valueFmt(String(cnValue)) : _escHtml(String(cnValue));
        cnHtml = `<br><small class="text-success">${formattedCn}</small>`;
    }
    return `<tr><th class="text-nowrap bg-light copy-cell" style="width:160px;">${thHtml}</th><td class="copy-cell">${formattedJa}${cnHtml}</td></tr>`;
}

// ── Inject CSS for copy-cell styling ──
(function() {
    const style = document.createElement('style');
    style.textContent = `
        .copy-cell { cursor: pointer; transition: background-color .15s; }
        .copy-cell:hover { background-color: rgba(var(--bs-primary-rgb), 0.06) !important; }
        .copy-cell small { cursor: pointer; }
        .copy-cell small:hover { text-decoration: underline; }
    `;
    document.head.appendChild(style);
})();

// ── Global click-to-copy handler (event delegation) ──
document.addEventListener('click', (e) => {
    // Priority 1: Click on translation text (small.text-success or small.text-info)
    const small = e.target.closest('.copy-cell small.text-success, .copy-cell small.text-info');
    if (small) {
        e.stopPropagation();
        copyToClipboard(small.textContent).then(() => showCopyToast(small.textContent));
        return;
    }
    // Priority 2: Click on cell itself → copy main text (excluding translations)
    const cell = e.target.closest('.copy-cell');
    if (cell) {
        const text = getCellMainText(cell);
        if (text) {
            copyToClipboard(text).then(() => showCopyToast(text));
        }
    }
});
