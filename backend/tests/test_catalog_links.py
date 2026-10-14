"""推荐详情链接必须对应工具查到且回复中提到的商品。"""

from app.modules.catalog.links import append_product_links


def test_only_mentioned_products_are_linked_and_duplicates_are_removed() -> None:
    monitor = {"id": "p13", "name": "ROG SWIFT OLED PG259QWS Ace"}
    results = [
        {"name": "search_products", "output": {"items": [monitor, {"id": "p01", "name": "AirBeat Pro 降噪耳机"}]}},
        {"name": "get_product", "output": {"product": monitor}},
    ]
    answer = append_product_links("推荐 ROG SWIFT OLED PG259QWS Ace。", results)
    assert answer.count("#shop?product=p13") == 1
    assert "#shop?product=p01" not in answer


def test_short_model_name_and_existing_markdown_link_are_supported() -> None:
    results = [{"name": "get_product", "output": {"product": {"id": "p01", "name": "AirBeat Pro 降噪耳机"}}}]
    answer = append_product_links("推荐 AirBeat Pro。", results)
    assert "[AirBeat Pro 降噪耳机](#shop?product=p01)" in answer
    assert append_product_links(answer, results) == answer


def test_failed_unknown_or_unmentioned_products_do_not_create_links() -> None:
    answer = "建议您先确认预算。"
    results = [
        {"name": "get_product", "output": {"success": False, "product": {"id": "p01", "name": "预算"}}},
        {"name": "search_products", "output": {"items": [{"id": "bad", "name": "预算"}, {"id": "p13", "name": "显示器"}]}},
        {"name": "search_knowledge", "output": {"items": [{"id": "p01", "name": "预算"}]}},
    ]
    assert append_product_links(answer, results) == answer


def test_product_names_are_escaped_for_markdown() -> None:
    results = [{"name": "get_product", "output": {"product": {"id": "p13", "name": "显示器 [新品]"}}}]
    answer = append_product_links("推荐显示器 [新品]", results)
    assert "[显示器 \\[新品\\]](#shop?product=p13)" in answer
