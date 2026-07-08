"""Thin async client for the Ollama chat API.

Both entry points return None on any failure (server down, timeout, non-200)
so callers can degrade gracefully instead of handling exceptions.
"""

import asyncio
import re

import aiohttp

from config import OLLAMA_MODEL, OLLAMA_URL

REQUEST_TIMEOUT_SECONDS = 60


async def query_ollama_chat(messages: list, model: str = None) -> str | None:
    """Send a multi-turn conversation to Ollama."""
    try:
        payload = {"model": model or OLLAMA_MODEL, "messages": messages, "stream": False}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as session:
            async with session.post(f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("message", {}).get("content", None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def query_ollama(system: str, prompt: str, model: str = None) -> str | None:
    """Send a single system+user exchange to Ollama."""
    return await query_ollama_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        model=model,
    )


def clean_reasoning(text: str) -> str:
    """Strip <think> reasoning blocks and wrapping quotes from model output."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Remove wrapping double quotes
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned
