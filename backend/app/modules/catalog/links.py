"""根据商品工具的真实结果生成可跳转的店内链接。"""

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


def product_url(product_id: str) -> str:
    return f"#shop?product={product_id}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def append_product_links(answer: str, tool_results: Iterable[dict[str, Any]]) -> str:
    """仅为回复中提到且工具实际返回的商品补链接，已有链接不重复追加。"""
    products: dict[str, str] = {}
    for result in tool_results:
        if result.get("name") not in {"search_products", "get_product"}:
            continue
        output = result.get("output")
        if not isinstance(output, dict) or output.get("success") is False:
            continue
        candidates = (
            output.get("items", [])
            if result["name"] == "search_products"
            else [output.get("product")]
        )
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            product_id, name = item.get("id"), item.get("name")
            if (
                isinstance(product_id, str)
                and re.fullmatch(r"p\d{2}", product_id)
                and isinstance(name, str)
                and name.strip()
            ):
                products[product_id] = name.strip()
    links = []
    normalized_answer = _normalize(answer)
    for product_id, name in products.items():
        url = product_url(product_id)
        if url in answer:
            continue
        prefix = re.match(r"[A-Za-z0-9][A-Za-z0-9\s+./_-]*", name)
        mentioned = _normalize(name) in normalized_answer or re.search(
            rf"\b{product_id}\b", answer, re.I
        )
        if prefix and len(_normalize(prefix.group())) >= 4:
            mentioned = mentioned or _normalize(prefix.group()) in normalized_answer
        if not mentioned:
            continue
        label = (
            name.replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        links.append(f"- [{label}]({url})")
    return answer + "\n\n商品详情：\n" + "\n".join(links) if links else answer
