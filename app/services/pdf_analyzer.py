"""Blueprint PDF analyzer: extract property data from real estate documents using LLM Vision."""

import io
import os
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import fitz  # PyMuPDF

from app.services.llm_client import LLMClient, extract_json

logger = logging.getLogger(__name__)

VISION_PROMPT = """你是日本不动产図面（物件資料）分析专家。请分析这张図面图片，尽可能完整地提取页面上的所有文字信息。

规则：
1. 判断物件类型: rental(賃貸)/mansion(中古マンション)/house(一戸建)/investment(投資)/land(土地)
2. 提取页面上所有可见的物件信息字段，包括但不限于：
   - 基本：物件名、所在地、交通（最寄り駅/徒歩分）、間取り、面積
   - 費用：価格/賃料、管理費、敷金、礼金、保証金、修繕積立金
   - 土地：土地面積、建ぺい率、容積率、用途地域、地目、セットバック、私道負担
   - 建物：構造、築年月、階建/階、向き
   - 権利：土地権利/所有権、借地期間、借地料
   - 設備：ガス、上下水道、駐車場
   - 条件：契約期間、更新料、引渡し時期、現況
   - 周辺：ライフインフォメーション（周辺施設と距離）
   - その他：取引形態、備考、注意事項
3. 重要：页面上的物件说明文、セールスポイント、周辺環境の説明（例如"静かな道路"、"お子様に安心"等描述）
   全部提取到「物件説明」字段中，原文保留不要省略。这些是物件的重要卖点，不是会社信息。
4. 只有以下内容属于不动产会社信息，不要混入物件字段：
   会社名、会社住所、電話/FAX番号、免許番号、担当者名、検印、登録No
5. 判断页面是否包含間取り図（户型图）
6. 如果页面只是户型图没有文字信息，type 设为 "floor_plan_only"
7. 如果页面无物件数据，type 设为 "empty"
8. 字段值请保持原文不要省略，周辺施設可以合并为一个字段

返回严格 JSON：
{
  "type": "rental|mansion|house|investment|land|floor_plan_only|empty",
  "has_floor_plan": true/false,
  "property_name": "物件名",
  "fields": { "所在地": "...", "物件説明": "...(完整描述文)", ... }
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
        data['物件名'] = prop.property_name
        data['_property_type'] = prop.property_type
        data['_source'] = 'blueprint'
        if prop.floor_plan_paths:
            data['_floor_plan_paths'] = prop.floor_plan_paths
        return data
