"""Provider-switchable AI extraction (same pattern as the expense app).

Providers: Anthropic / Claude Sonnet (default) and Google Gemini.
Select with AI_PROVIDER + the matching API key; AI_MODEL optionally overrides
the per-provider default. Runs without any key locally: NullProvider returns
an empty record and the reporter completes everything on the confirm screen.
"""
import base64
import json
import re

import config


class Provider:
    def extract(self, prompt: str, images: list[bytes]) -> dict:
        raise NotImplementedError


class NullProvider(Provider):
    """No API key configured — return nothing extracted; humans fill the form."""
    def extract(self, prompt, images):
        return {}


class AnthropicProvider(Provider):
    def __init__(self, model=None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = model or config.AI_MODEL or config.DEFAULT_MODELS["anthropic"]

    def extract(self, prompt, images):
        content = []
        for img in images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": base64.b64encode(img).decode()},
            })
        content.append({"type": "text", "text": prompt})
        msg = self.client.messages.create(
            model=self.model, max_tokens=4096,
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group()) if m else {}


class GeminiProvider(Provider):
    def __init__(self, model=None):
        from google import genai
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = model or config.AI_MODEL or config.DEFAULT_MODELS["gemini"]

    def extract(self, prompt, images):
        from google.genai import types
        parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
        parts.append(prompt)
        resp = self.client.models.generate_content(
            model=self.model, contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", max_output_tokens=4096))
        m = re.search(r"\{.*\}", resp.text or "", re.DOTALL)
        return json.loads(m.group()) if m else {}


def get_provider() -> Provider:
    try:
        if config.AI_PROVIDER == "gemini" and config.GEMINI_API_KEY:
            return GeminiProvider()
        if config.AI_PROVIDER == "anthropic" and config.ANTHROPIC_API_KEY:
            return AnthropicProvider()
    except Exception:
        pass
    # No key / unknown provider -> humans fill the confirm screen.
    return NullProvider()
