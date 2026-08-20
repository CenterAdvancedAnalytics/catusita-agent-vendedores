import os
import anthropic
from dotenv import load_dotenv

load_dotenv()

_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-6"


async def create_message(
    system: str,
    messages: list,
    tools: list = None,
    max_tokens: int = 1024,
    model: str = None,
) -> anthropic.types.Message:
    kwargs = {
        "model": model or MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    return await _client.messages.create(**kwargs)


async def extraer_texto_de_imagen(
    imagen_base64: str,
    instruccion: str,
    media_type: str = "image/png",
    max_tokens: int = 1024,
) -> str:
    """Lee una imagen con la visión de Claude y devuelve el texto extraído.

    Se usa para volcar a texto datos que solo llegan dentro de una foto.
    """
    response = await _client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": imagen_base64,
                    },
                },
                {"type": "text", "text": instruccion},
            ],
        }],
    )
    return "".join(b.text for b in response.content if hasattr(b, "text")).strip()
