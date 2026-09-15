"""服务器发送事件的编码规则。"""

from pydantic import BaseModel


def encode_sse(event_name: str, data: BaseModel) -> str:
    """把一个 Pydantic 对象编码成标准 SSE 文本块。"""

    json_data = data.model_dump_json()
    return f"event: {event_name}\ndata: {json_data}\n\n"
