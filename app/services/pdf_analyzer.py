"""Blueprint PDF analyzer: extract property data from real estate documents using LLM Vision."""

import io
import os
import logging
import time
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, List, Optional

import fitz  # PyMuPDF

from app.services.llm_client import LLMClient, extract_json

logger = logging.getLogger(__name__)

VISION_PROMPT = """你是日本不动产图面（物件资料）分析专家。请分析这张图面图片，尽可能完整地提取页面上的所有物件信息。

规则：
1. 判断物件类型（6类之一）：
   - rental = 租房
   - mansion = 买卖-公寓塔楼
   - house = 买卖-一户建
   - investment = 买卖-投资物件
   - land = 买卖-土地
   - other = 其他物件
2. 输出字段名时，优先直接使用中文客户字段名，不要输出 basic.address 这类技术 key。
3. 字段值保留原文，可中日混合，不要擅自改写事实。
4. 尽可能覆盖以下字段：
   - 基本信息：物件名、所在城市、物件名称、物件类型、售价（日元）、户型、面积、土地面积、建筑面积、地址、建成日期
   - 基础详情信息：交通、总户数、类型、构造、朝向、专有面积、楼层、总楼层、现状、交易形式、停车场、合同期、备注
   - 土地/建筑信息：土地权利、建蔽率、容积率、用途地域、私道负担、限制事项、施工公司、管理方式、管理公司、抗震构造、翻新
   - 配套与说明：配套设施、物件介绍、学校、商圈、公园设施
   - 费用信息：房租、押金、礼金、共益费、管理费、修缮费、其他费用
5. 页面上的物件说明文、卖点、周边环境说明，全部尽量提取到“物件介绍”或“备注”中，不要遗漏。
6. 以下属于公司信息，不要混入物件字段：公司名、门店地址、电话/FAX、免许番号、担当者、印章、注册编号。
7. 判断页面是否包含户型图。
8. 如果页面只是户型图没有文字信息，type 设为 "floor_plan_only"。
9. 如果页面无物件数据，type 设为 "empty"。
10. 如果某字段不确定，可省略，不要捏造。

返回严格 JSON：
{
  "type": "rental|mansion|house|investment|land|other|floor_plan_only|empty",
  "has_floor_plan": true,
  "property_name": "物件名",
  "fields": {
    "地址": "...",
    "交通": "...",
    "面积": "...",
    "房租": "...",
    "配套设施": ["..."],
    "物件介绍": "..."
  }
}"""

PAGE_ZOOM = 2.0  # Render at 2x resolution (~144 DPI for most PDFs)
FLOOR_PLAN_ZOOM = 3.0  # Higher resolution for saved floor plan snapshots


@dataclass
class PageResult:
    page_num: int
    page_type: str  # rental/mansion/house/investment/land/floor_plan_only/empty
    has_floor_plan: bool = False
    property_name: str = ''
    fields: Dict[str, Any] = field(default_factory=dict)
    raw_response: str = ''


@dataclass
class PropertyResult:
    """A merged property extracted from one or more pages."""
    property_type: str
    property_name: str
    fields: Dict[str, Any] = field(default_factory=dict)
    floor_plan_paths: List[str] = field(default_factory=list)
    source_pages: List[int] = field(default_factory=list)
    source_text_pages: List[int] = field(default_factory=list)
    source_text: str = ''


class BlueprintAnalyzer:
    """Analyze real estate blueprint PDFs using LLM Vision."""

    def __init__(self, llm_client: LLMClient, upload_folder: str = 'uploads'):
        self.llm = llm_client
        self.upload_folder = upload_folder
        self.floor_plan_dir = os.path.join(upload_folder, 'floor_plans')
        os.makedirs(self.floor_plan_dir, exist_ok=True)

    def analyze_pdf(self, pdf_bytes: bytes, filename: str = 'document.pdf',
                    progress_cb: Optional[Callable] = None) -> List[PropertyResult]:
        """Full pipeline: PDF -> pages -> LLM Vision -> merged properties.

        Args:
            pdf_bytes: Raw PDF file content
            filename: Original filename (for logging)
            progress_cb: Optional callback(current_page, total_pages, message)

        Returns:
            List of PropertyResult objects
        """
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
        total_pages = len(doc)
        page_texts = [doc[i].get_text("text") or '' for i in range(total_pages)]
        logger.info(f"Analyzing PDF '{filename}': {total_pages} pages")

        if progress_cb:
            progress_cb(0, total_pages, f'PDF loaded: {total_pages} pages')

        page_results: List[PageResult] = []
        for i in range(total_pages):
            if progress_cb:
                progress_cb(i, total_pages, f'Analyzing page {i + 1}/{total_pages}')

            try:
                img_bytes = self._render_page(doc, i)
                result = self._analyze_page(img_bytes, i)
                page_results.append(result)
                logger.info(f"Page {i + 1}: type={result.page_type}, "
                            f"floor_plan={result.has_floor_plan}, "
                            f"name={result.property_name}")
            except Exception as e:
                logger.error(f"Error analyzing page {i + 1}: {e}")
                page_results.append(PageResult(
                    page_num=i, page_type='empty',
                    raw_response=f'Error: {e}'
                ))

            if i < total_pages - 1:
                time.sleep(0.5)

        if progress_cb:
            progress_cb(total_pages, total_pages, 'Merging results...')

        properties = self._merge_pages(page_results)
        self._attach_source_texts(properties, page_texts)
        self._fill_from_text(properties)

        self._save_page_images(doc, page_results, properties, filename)

        doc.close()

        if progress_cb:
            progress_cb(total_pages, total_pages,
                        f'Done: {len(properties)} properties extracted')

        return properties

    @staticmethod
    def _render_page(doc: fitz.Document, page_idx: int) -> bytes:
        """Render a single PDF page to PNG bytes using PyMuPDF (fast, no external deps)."""
        page = doc[page_idx]
        mat = fitz.Matrix(PAGE_ZOOM, PAGE_ZOOM)
        pix = page.get_pixmap(matrix=mat)

        # Cap oversized pages to avoid sending huge images to Vision API
        max_dim = 2048
        if pix.width > max_dim or pix.height > max_dim:
            scale = max_dim / max(pix.width, pix.height)
            new_mat = fitz.Matrix(PAGE_ZOOM * scale, PAGE_ZOOM * scale)
            pix = page.get_pixmap(matrix=new_mat)

        return pix.tobytes(output='png')

    def _analyze_page(self, image_bytes: bytes, page_num: int) -> PageResult:
        """Send a single page image to LLM Vision and parse the result."""
        raw = self.llm.chat_with_vision(
            prompt=VISION_PROMPT,
            images=[image_bytes],
            mime_type='image/png',
            temperature=0.1,
            max_tokens=4096,
        )

        parsed = extract_json(raw)
        if not parsed:
            logger.warning(f"Page {page_num + 1}: could not parse LLM response")
            return PageResult(page_num=page_num, page_type='empty', raw_response=raw)

        return PageResult(
            page_num=page_num,
            page_type=parsed.get('type', 'empty'),
            has_floor_plan=parsed.get('has_floor_plan', False),
            property_name=parsed.get('property_name', ''),
            fields=parsed.get('fields', {}),
            raw_response=raw,
        )

    def _merge_pages(self, page_results: List[PageResult]) -> List[PropertyResult]:
        """Merge consecutive pages belonging to the same property.

        Strategy:
        - Pages with same property_name -> merge
        - An info page followed by floor_plan_only -> merge into same property
        - Standalone pages -> separate property
        """
        if not page_results:
            return []

        properties: List[PropertyResult] = []
        current: Optional[PropertyResult] = None

        for pr in page_results:
            if pr.page_type == 'empty':
                continue

            if pr.page_type == 'floor_plan_only':
                if current:
                    current.source_pages.append(pr.page_num)
                continue

            if (current and pr.property_name and current.property_name
                    and pr.property_name == current.property_name):
                current.fields.update(pr.fields)
                current.source_pages.append(pr.page_num)
            else:
                if current:
                    properties.append(current)
                current = PropertyResult(
                    property_type=pr.page_type,
                    property_name=pr.property_name,
                    fields=dict(pr.fields),
                    source_pages=[pr.page_num],
                )

        if current:
            properties.append(current)

        return properties

    @classmethod
    def _page_relevance_score(cls, prop: PropertyResult, text: str) -> int:
        compact = cls._compact_text(text)
        if not compact:
            return -999

        score = 0
        generic_anchors = ['価格', '販売価格', '所在地', '所在', '交通', '徒歩', '面積', '建物面積', '土地面積']
        type_anchors = {
            'mansion': ['専有面積', '管理費', '修繕積立金', '総戸数', 'バルコニー面積'],
            'land': ['土地面積', '建ぺい率', '容積率', '用途地域', '私道負担'],
            'house': ['建物面積', '土地面積', '戸建', '間取り', '駐車場'],
            'investment': ['年間収入', '利回り', '一棟', '収益', '建物面積', '売ビル'],
            'rental': ['賃料', '共益費', '敷金', '礼金', '間取り'],
        }

        for anchor in generic_anchors:
            if anchor in compact:
                score += 2
        for anchor in type_anchors.get(prop.property_type, []):
            if anchor in compact:
                score += 4

        if re.search(r'(?:東京都|北海道|(?:大阪|京都)府|.{2,3}県).{0,24}(?:市|区|町|村)', compact):
            score += 5
        if re.search(r'\d+(?:,\d+)*(?:\.\d+)?万?円', compact):
            score += 4
        if re.search(r'\d+(?:\.\d+)?㎡', compact):
            score += 4
        if '駅' in compact and '徒歩' in compact:
            score += 4

        if prop.property_name:
            name = cls._compact_text(prop.property_name)
            if name and len(name) >= 3 and name in compact:
                score += 12

        noise_penalty = sum(1 for noise in ['TEL', 'FAX', 'E-mail', 'メール', '免許', '担当', '営業時間', '〒'] if noise in text)
        score -= noise_penalty * 2
        return score

    @classmethod
    def _select_relevant_page_indices(cls, prop: PropertyResult, page_texts: List[str]) -> List[int]:
        valid_indices = [idx for idx in prop.source_pages if 0 <= idx < len(page_texts)]
        if not valid_indices:
            return []
        if len(valid_indices) <= 2:
            return valid_indices

        scored = sorted(
            ((idx, cls._page_relevance_score(prop, page_texts[idx])) for idx in valid_indices),
            key=lambda item: item[1],
            reverse=True,
        )
        best_score = scored[0][1]
        keep = [idx for idx, score in scored if score >= max(best_score - 4, 4)]
        if not keep:
            keep = [scored[0][0]]
        keep = sorted(set(keep))[:3]
        return keep

    @classmethod
    def _attach_source_texts(cls, properties: List[PropertyResult], page_texts: List[str]) -> None:
        for prop in properties:
            selected_pages = cls._select_relevant_page_indices(prop, page_texts)
            prop.source_text_pages = selected_pages or list(prop.source_pages)
            prop.source_text = '\n'.join(
                page_texts[idx] for idx in prop.source_text_pages if 0 <= idx < len(page_texts)
            )

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r'\s+', '', text or '')

    @staticmethod
    def _non_empty_lines(text: str) -> List[str]:
        return [line.strip() for line in (text or '').splitlines() if line.strip()]

    @staticmethod
    def _find_money_token(text: str) -> Optional[str]:
        patterns = [
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円(?:（税込）)?)',
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)億\s*(?:\d+(?:,\d+)*(?:\.\d+)?)?\s*万\s*円(?:（税込）)?)',
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)万\s*円(?:（税込）)?)',
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)円)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return re.sub(r'\s+', '', match.group(1))
        return None

    @classmethod
    def _find_price_from_lines(cls, lines: List[str], compact: str) -> Optional[str]:
        direct_patterns = [
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円(?:（税込）)?)販売価格',
            r'販売価格((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円(?:（税込）)?)',
            r'売土地((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'中古((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'一棟マンション((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'収益マンション((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'売ビル((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'新築分譲住宅[^\d]{0,20}((?:\d+(?:,\d+)*(?:\.\d+)?)億?(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'名称価格\d*棟マンション((?:\d+(?:,\d+)*(?:\.\d+)?)億?(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円)',
            r'価格万円[^\d]{0,20}([\d,.]+)(?=(?:[^\d]{0,20}(?:所在|所在地|土地権利|所有権|駅|徒歩)))',
        ]
        for pattern in direct_patterns:
            match = re.search(pattern, compact)
            if match:
                return match.group(1)

        for line in lines[:120]:
            compact_line = cls._compact_text(line)
            if any(flag in compact_line for flag in ['販売価格', '売価', '価格']) and '月額' not in compact_line and '年間収入' not in compact_line:
                token = cls._find_money_token(compact_line)
                if token and ('万' in token or '億' in token):
                    return token

        for idx, line in enumerate(lines[:140]):
            compact_line = cls._compact_text(line)
            if any(flag in compact_line for flag in ['販売価格', '売土地', '中古', '新築', '売ビル', '収益マンション', '一棟マンション', '土地', '戸建']):
                window_lines = lines[idx: min(len(lines), idx + 50)]
                window = ''.join(cls._compact_text(v) for v in window_lines)
                for pattern in [
                    r'((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万?円(?:（税込）)?)',
                    r'((?:\d+(?:,\d+)*(?:\.\d+)?)万円(?:（税込）)?)',
                ]:
                    match = re.search(pattern, window)
                    if match and '年間収入' not in window[:match.start()]:
                        return match.group(1)
                for j in range(idx, min(len(lines), idx + 40)):
                    number_text = lines[j].replace(' ', '').strip()
                    if not re.fullmatch(r'[\d,.]+', number_text):
                        continue
                    nearby = ''.join(cls._compact_text(v) for v in lines[j: min(len(lines), j + 8)])
                    if '万円' in nearby and '年間収入' not in nearby and '月額' not in nearby:
                        return f"{number_text}万円"

        for pattern in [
            r'([\d,.]+万円(?:（税込）)?)',
            r'((?:\d+(?:,\d+)*(?:\.\d+)?)億(?:\d+(?:,\d+)*(?:\.\d+)?)?万円?)',
        ]:
            match = re.search(pattern, compact)
            if match:
                return match.group(1)
        return None

    @classmethod
    def _find_area_value(cls, compact: str, patterns: List[str]) -> Optional[str]:
        for pattern in patterns:
            match = re.search(pattern, compact)
            if match:
                return f"{match.group(1)}㎡"
        return None

    @classmethod
    def _collect_access_lines(cls, lines: List[str]) -> List[str]:
        results: List[str] = []
        for idx, line in enumerate(lines):
            compact_line = cls._compact_text(line)
            if '徒歩' not in compact_line:
                continue
            snippet = line.strip()
            if '駅' not in compact_line and idx > 0:
                prev = lines[idx - 1].strip()
                if '駅' in cls._compact_text(prev) or '線' in cls._compact_text(prev):
                    snippet = f"{prev} {snippet}"
            compact_snippet = cls._compact_text(snippet)
            if '駅' not in compact_snippet:
                continue
            if any(noise in compact_snippet for noise in ['小学校', '中学校', 'スーパー', 'コンビニ', '約']):
                continue
            snippet = re.sub(r'\s+', ' ', snippet).strip()
            if snippet not in results:
                results.append(snippet)
        return results[:3]

    @staticmethod
    def _normalize_access_snippet(snippet: str) -> str:
        text = re.sub(r'^[^A-Za-zＡ-Ｚａ-ｚ一-龥ァ-ヴー「｢]*', '', snippet or '')
        text = text.replace('､', '、').replace('･', '・')
        text = re.sub(r'線(?=[A-Za-zＡ-Ｚａ-ｚ一-龥ァ-ヴー]{2,8}線)', '線・', text)
        text = re.sub(r'([」｣])駅', r'\1駅', text)
        return text.strip(' ・、')

    @classmethod
    def _find_address_from_compact(cls, compact: str) -> Optional[str]:
        if not compact:
            return None
        patterns = [
            r'(?:住居表示|所在地|所在)[:：／/]*((?:東京都|北海道|(?:大阪|京都)府|.{2,3}県)?.{0,24}?(?:市|区|町|村).{0,30}?[0-9０-９\-－−ー丁目番地号]+?)(?=(?:土地権利|権利|借地期間|築年月|構造|販売価格|価格|交通|面積|所有権|鉄骨|木造|地下\d+階|地上\d+階|$))',
            r'((?:東京都|北海道|(?:大阪|京都)府|.{2,3}県).{0,24}?(?:市|区|町|村).{0,30}?[0-9０-９\-－−ー丁目番地号]+?)(?=(?:土地権利|権利|借地期間|築年月|構造|販売価格|価格|交通|面積|所有権|鉄骨|木造|地下\d+階|地上\d+階|$))',
        ]
        for pattern in patterns:
            match = re.search(pattern, compact)
            if match:
                return match.group(1).strip(' ／/：:')
        return None

    @classmethod
    def _collect_access_from_compact(cls, compact: str) -> List[str]:
        if not compact:
            return []
        pattern = r'((?:[A-Za-zＡ-Ｚａ-ｚ一-龥ァ-ヴー・、･]{0,40}?線(?:[A-Za-zＡ-Ｚａ-ｚ一-龥ァ-ヴー・、･]{0,20}?線)*)?[「｢]?[一-龥ぁ-んァ-ヴーA-Za-zＡ-Ｚａ-ｚ0-9]{1,16}[」｣]?駅徒歩\d+分)'
        results: List[str] = []
        for match in re.finditer(pattern, compact):
            snippet = cls._normalize_access_snippet(match.group(1))
            if any(noise in snippet for noise in ['小学校', '中学校', 'スーパー', 'コンビニ', '約']):
                continue
            if snippet not in results:
                results.append(snippet)
        return results[:3]

    @classmethod
    def _find_address(cls, lines: List[str], text: str) -> Optional[str]:
        labels = ('所在地', '所在', '住居表示')
        address_pattern = r'(?:東京都|北海道|(?:大阪|京都)府|.{2,3}県)?.{0,24}(?:市|区|町|村).*[0-9０-９\-－−ー]'
        bad_tokens = ['〒', 'TEL', 'FAX', '担当', 'E-mail', 'email', 'URL', '徒歩', '利用時', 'まで']

        for idx, line in enumerate(lines):
            if not any(label in line for label in labels):
                continue
            for cand in lines[idx: min(len(lines), idx + 6)]:
                compact_cand = cls._compact_text(cand)
                if any(bad in compact_cand for bad in bad_tokens):
                    continue
                if re.search(address_pattern, cand):
                    return cand.strip()

        for cand in lines:
            compact_cand = cls._compact_text(cand)
            if any(bad in compact_cand for bad in bad_tokens):
                continue
            if re.search(address_pattern, cand):
                return cand.strip()

        match = re.search(r'((?:東京都|北海道|(?:大阪|京都)府|.{2,3}県)[^\n]{0,40}[市区町村][^\n]{0,80})', text)
        if match:
            candidate = match.group(1).strip()
            compact_candidate = cls._compact_text(candidate)
            if not any(bad in compact_candidate for bad in ['徒歩', '分', '利用時', 'まで']):
                return candidate
        return None

    @classmethod
    def _find_layout(cls, lines: List[str], text: str) -> Optional[str]:
        patterns = [
            r'(?<!\d)(\d+\s*[SLDKＬＤＫＲR]+(?:\+[A-Za-z0-9]+)?)',
            r'(ワンルーム)',
        ]
        for line in lines[:80]:
            clean = line.strip()
            if len(clean) > 40:
                continue
            for pattern in patterns:
                match = re.search(pattern, clean)
                if match:
                    return match.group(1).replace('Ｒ', 'R').replace('Ｌ', 'L').replace('Ｄ', 'D').replace('Ｋ', 'K')
        match = re.search(r'(?<!\d)(\d+\s*[SLDKＬＤＫＲR]+)', text)
        if match:
            return match.group(1).replace('Ｒ', 'R').replace('Ｌ', 'L').replace('Ｄ', 'D').replace('Ｋ', 'K')
        return None

    @classmethod
    def _find_built_date(cls, lines: List[str], compact: str) -> Optional[str]:
        date_pattern = r'(昭和\d+年\d+月|平成\d+年\d+月|令和\d+年\d+月|\d{4}年\d{1,2}月|\d{4}年)'
        for idx, line in enumerate(lines):
            if '築年月' in line or '築年' in line:
                window = ' '.join(lines[idx: min(len(lines), idx + 4)])
                match = re.search(date_pattern, window)
                if match:
                    return match.group(1)
        match = re.search(date_pattern, compact)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _find_zoning(cls, compact: str) -> Optional[str]:
        zoning_pattern = (
            r'(第一種低層住居専用地域|第二種低層住居専用地域|第一種中高層住居専用地域|'
            r'第二種中高層住居専用地域|第一種住居地域|第二種住居地域|準住居地域|'
            r'田園住居地域|近隣商業地域|商業地域|準工業地域|工業地域|工業専用地域)'
        )
        match = re.search(zoning_pattern, compact)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _is_noise_line(line: str) -> bool:
        compact = re.sub(r'\s+', '', line or '')
        if not compact:
            return True
        if len(compact) <= 2 and not re.search(r'[\d㎡％円]', compact):
            return True
        noise_tokens = ['TEL', 'FAX', 'E-mail', 'email', 'URL', '免許', '担当', '営業時間', '定休日', '登録No', '取引態様', '手数料']
        return any(token in compact for token in noise_tokens)

    @classmethod
    def _collect_focus_lines(cls, prop: PropertyResult, lines: List[str]) -> List[str]:
        if not lines:
            return []

        common_tokens = ['価格', '販売価格', '所在地', '所在', '住居表示', '交通', '徒歩', '駅', '築年月', '土地権利', '所有権', '引渡', '現況']
        type_tokens = {
            'mansion': ['専有面積', '管理費', '修繕積立金', '総戸数', '駐車場', 'バルコニー面積'],
            'land': ['土地面積', '建ぺい率', '容積率', '用途地域', '私道負担', '売土地'],
            'house': ['土地面積', '建物面積', '構造', '駐車場', '戸建', '間取り'],
            'investment': ['年間収入', '利回り', '収益', '一棟', '建物面積', '総面積', '売ビル'],
            'rental': ['賃料', '共益費', '敷金', '礼金', '間取り'],
        }
        name_token = cls._compact_text(prop.property_name)
        selected_indices = set()

        for idx, line in enumerate(lines):
            compact_line = cls._compact_text(line)
            if not compact_line:
                continue
            if cls._is_noise_line(line):
                continue

            matched = any(token in compact_line for token in common_tokens)
            matched = matched or any(token in compact_line for token in type_tokens.get(prop.property_type, []))
            matched = matched or bool(re.search(r'\d+(?:,\d+)*(?:\.\d+)?万?円', compact_line))
            matched = matched or bool(re.search(r'\d+(?:\.\d+)?㎡', compact_line))
            matched = matched or ('駅' in compact_line and '徒歩' in compact_line)
            matched = matched or (bool(name_token) and len(name_token) >= 3 and name_token in compact_line)
            if not matched:
                continue

            start = max(0, idx - 1)
            end = min(len(lines), idx + 4)
            selected_indices.update(range(start, end))

        if not selected_indices:
            selected_indices = set(range(min(len(lines), 24)))

        focus_lines = []
        for idx in sorted(selected_indices):
            line = lines[idx].strip()
            if not line:
                continue
            if cls._is_noise_line(line):
                continue
            focus_lines.append(line)

        return focus_lines[:160]

    @classmethod
    def _fill_from_text(cls, properties: List[PropertyResult]) -> None:
        for prop in properties:
            text = prop.source_text or ''
            if not text:
                continue
            fields = prop.fields
            compact = cls._compact_text(text)
            lines = cls._non_empty_lines(text)
            focus_lines = cls._collect_focus_lines(prop, lines)
            if len(focus_lines) < 8:
                focus_lines = []
            focus_text = '\n'.join(focus_lines) if focus_lines else text
            focus_compact = cls._compact_text(focus_text)

            def set_if_missing(key: str, value: Any) -> None:
                if value in (None, '', [], {}):
                    return
                if key not in fields or fields.get(key) in (None, '', [], {}):
                    fields[key] = value

            price_token = cls._find_price_from_lines(focus_lines or lines, focus_compact or compact)
            if price_token:
                if price_token.isdigit() and prop.property_type in {'investment', 'house'}:
                    price_token = f'{price_token}万円'
                set_if_missing('售价（日元）', price_token)

            if '所有権' in compact:
                set_if_missing('土地权利', '所有権')

            address = cls._find_address(focus_lines or lines, focus_text or text)
            if not address:
                address = cls._find_address_from_compact(focus_compact or compact)
            if address:
                set_if_missing('地址', address)

            access_lines = cls._collect_access_lines(focus_lines or lines)
            if not access_lines:
                access_lines = cls._collect_access_from_compact(focus_compact or compact)
            if access_lines:
                if prop.property_type != 'house':
                    access_lines = access_lines[:1]
                access_lines = [cls._normalize_access_snippet(v) for v in access_lines]
                set_if_missing('交通', access_lines)

            layout = cls._find_layout(focus_lines or lines, focus_compact or compact)
            if layout:
                set_if_missing('户型', layout)

            exclusive_area = cls._find_area_value(focus_compact or compact, [
                r'専有面積\(壁芯\)／?([\d.]+)㎡',
                r'専有面積（壁芯）／?([\d.]+)㎡',
            ])
            if exclusive_area:
                set_if_missing('专有面积', exclusive_area)
                set_if_missing('面积', exclusive_area)

            land_area = cls._find_area_value(focus_compact or compact, [
                r'土地面積(?:公簿)?([\d.]+)㎡',
                r'公簿面積[:：]?([\d.]+)㎡',
                r'借地面積([\d.]+)㎡',
                r'面積(?:公簿)?([\d.]+)㎡',
            ])
            if land_area and prop.property_type in {'land', 'house', 'investment'}:
                set_if_missing('土地面积', land_area)

            building_area = cls._find_area_value(focus_compact or compact, [
                r'建物面積([\d.]+)㎡',
                r'延床面積([\d.]+)㎡',
                r'延べ\s*([\d.]+)',
                r'合計[:：]?([\d.]+)㎡',
                r'建物面積延([\d.]+)㎡',
            ])
            if building_area and prop.property_type in {'house', 'investment'}:
                set_if_missing('建筑面积', building_area)

            total_area = cls._find_area_value(focus_compact or compact, [
                r'合計[:：]?([\d.]+)㎡',
                r'延べ\s*([\d.]+)',
                r'総面積([\d.]+)㎡',
            ])
            if total_area and prop.property_type == 'investment':
                set_if_missing('总面积', total_area)

            balcony_match = re.search(r'バルコニー面積／?([\d.]+)㎡', focus_compact or compact)
            if balcony_match:
                set_if_missing('阳台面积', f'{balcony_match.group(1)}㎡')

            floor_match = re.search(r'地下\d+階付\d+階建\d+階部分|地上\d+階建|\d+階建\d+階部分|\d+階建', focus_compact or compact)
            if floor_match:
                floor_text = floor_match.group(0)
                if '階部分' in floor_text:
                    set_if_missing('所在楼层', floor_text)
                set_if_missing('总楼层', floor_text)

            built_date = cls._find_built_date(focus_lines or lines, focus_compact or compact)
            if built_date:
                set_if_missing('建成日期', built_date)

            structure_match = re.search(r'((?:鉄骨鉄筋コンクリート|鉄筋コンクリート|鉄骨造陸屋根|鉄骨造|木造)(?:[^\n]{0,24}))', focus_text or text)
            if structure_match:
                value = structure_match.group(1).strip()
                set_if_missing('构造', value)
            elif '地下1階付地上2階建' in (focus_text or text):
                set_if_missing('构造', '地下1階付地上2階建')

            mgmt_match = re.search(r'月額([\d,]+)円\s*月額([\d,]+)円', focus_compact or compact)
            if mgmt_match:
                set_if_missing('管理费', f'月額{mgmt_match.group(1)}円')
                set_if_missing('修缮费', f'月額{mgmt_match.group(2)}円')
            else:
                mgmt_single = re.search(r'管理費[^\d]{0,8}月額([\d,]+)円', focus_compact or compact)
                reserve_single = re.search(r'(?:修繕積立金|修缮费)[^\d]{0,8}月額([\d,]+)円', focus_compact or compact)
                if mgmt_single:
                    set_if_missing('管理费', f'月額{mgmt_single.group(1)}円')
                if reserve_single:
                    set_if_missing('修缮费', f'月額{reserve_single.group(1)}円')

            annual_income_match = re.search(r'(?:年間収入|想定年額賃料等|現況賃料収入)([\d,]+)円', focus_compact or compact)
            if annual_income_match:
                set_if_missing('年租金收入', annual_income_match.group(1))
            else:
                monthly_income_match = re.search(r'現況賃料収入([\d,]+)万円/月', focus_compact or compact)
                if monthly_income_match:
                    monthly = int(monthly_income_match.group(1).replace(',', ''))
                    set_if_missing('年租金收入', str(monthly * 12 * 10000))

            roi_match = re.search(r'(?:利回り|想定利回り)([\d.]+)％?', focus_compact or compact)
            if roi_match:
                set_if_missing('年回报率', roi_match.group(1))
                set_if_missing('回报率', roi_match.group(1))

            management_method_match = re.search(r'(全部委託（通勤）|全部委託|自主管理)', focus_text or text)
            if management_method_match:
                set_if_missing('管理方式（通勤）', management_method_match.group(1).strip())

            total_units_match = re.search(r'総戸数([\d,]+)戸', focus_compact or compact)
            if total_units_match:
                set_if_missing('总户数', total_units_match.group(1))

            parking_match = re.search(r'(空有/月額[\d,]+円[～~][\d,]+円|駐車場[:：]?有[^\n]{0,20}|駐車場[:：]?無[^\n]{0,20}|駐車場有|駐車場\s+有[^\n]{0,20})', focus_text or text)
            if parking_match:
                set_if_missing('停车场', parking_match.group(1).strip())
            elif prop.property_type in {'house', 'investment'}:
                parking_line = next((line for line in focus_lines if ('駐車場' in line or '駐 車 場' in line) and '【' not in line), None)
                if parking_line:
                    set_if_missing('停车场', parking_line.strip().replace('\n', ' '))

            current_match = re.search(r'(空室|賃貸中|居住中|建物有|更地)', focus_compact or compact)
            if current_match:
                set_if_missing('现状', current_match.group(1))

            delivery_match = re.search(r'(相談|応相談|即可（残代金決済後）|即可|引渡：相談)', focus_compact or compact)
            if delivery_match:
                set_if_missing('引渡时间', delivery_match.group(1))

            zoning = cls._find_zoning(focus_compact or compact)
            if zoning:
                set_if_missing('用途地域', zoning)

            city_plan_match = re.search(r'都市計画(市街化区域|市街化調整区域)', focus_compact or compact)
            if city_plan_match:
                set_if_missing('备注', f'都市計画: {city_plan_match.group(1)}')

            coverage_match = re.search(r'建ぺい率[:：]?([\d.]+)％', focus_compact or compact)
            if coverage_match:
                set_if_missing('建蔽率', coverage_match.group(1))

            far_match = re.search(r'容積率[:：]?([\d.]+)％', focus_compact or compact)
            if far_match:
                set_if_missing('容积率', far_match.group(1))

            private_road_match = re.search(r'私道負担([^\n]{1,30})', focus_text or text)
            if private_road_match:
                value = private_road_match.group(1).strip()
                if value and value != '-':
                    set_if_missing('私道负担', value)
            elif '私道負担無' in (focus_compact or compact):
                set_if_missing('私道负担', '无')

            company_match = re.search(r'(株式会社[^\n]{2,40})', focus_text or text)
            if company_match:
                if '工務店' in company_match.group(1):
                    set_if_missing('施工公司', company_match.group(1).strip())

            if 'ペット飼育可' in compact:
                amenities = fields.get('配套设施') or []
                if isinstance(amenities, list) and 'ペット飼育可' not in amenities:
                    amenities = amenities + ['ペット飼育可']
                    fields['配套设施'] = amenities

    def _save_page_images(self, doc: fitz.Document,
                          page_results: List[PageResult],
                          properties: List[PropertyResult],
                          filename: str) -> None:
        """Save page screenshots for all non-empty pages as property images.

        Every page belonging to a property gets a high-res screenshot saved,
        so users can always view the original PDF content alongside extracted data.
        """
        base_name = os.path.splitext(os.path.basename(filename))[0]

        for pr in page_results:
            if pr.page_type == 'empty':
                continue

            try:
                page = doc[pr.page_num]
                mat = fitz.Matrix(FLOOR_PLAN_ZOOM, FLOOR_PLAN_ZOOM)
                pix = page.get_pixmap(matrix=mat)
                fname = f"{base_name}_p{pr.page_num + 1}.png"
                path = os.path.join(self.floor_plan_dir, fname)
                pix.save(path)

                owner = self._find_property_for_page(pr.page_num, properties)
                if owner:
                    owner.floor_plan_paths.append(path)

                logger.info(f"Page image saved: {fname} ({pix.width}x{pix.height})")
            except Exception as e:
                logger.error(f"Failed to save page image {pr.page_num + 1}: {e}")

    @staticmethod
    def _find_property_for_page(page_num: int,
                                properties: List[PropertyResult]) -> Optional[PropertyResult]:
        """Find which property owns a given page number."""
        for prop in properties:
            if page_num in prop.source_pages:
                return prop
        if properties:
            min_dist = float('inf')
            closest = None
            for prop in properties:
                for sp in prop.source_pages:
                    dist = abs(page_num - sp)
                    if dist < min_dist:
                        min_dist = dist
                        closest = prop
            return closest
        return None

    @staticmethod
    def property_to_dict(prop: PropertyResult) -> Dict[str, Any]:
        """Convert a PropertyResult to a flat dict suitable for storage."""
        data = dict(prop.fields)
        if prop.property_name and '物件名' not in data and '物件名称' not in data:
            data['物件名'] = prop.property_name
        data['_property_type'] = prop.property_type
        data['_source'] = 'blueprint'
        if prop.floor_plan_paths:
            data['_floor_plan_paths'] = prop.floor_plan_paths
        if prop.source_text:
            data['_source_text'] = prop.source_text
        return data
