"""Web search through OpenRouter's web plugin, so it needs no extra API key.

Each search is one small model call with search results attached (about half a cent).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from openai import APIError, AsyncOpenAI
from pydantic import Field

from noah.config import Settings
from noah.tools.registry import Tool, ToolError, tool

Search = Callable[[str], Awaitable[str]]

INSTRUCTIONS = (
    "Answer the question in two or three plain sentences using the search results. "
    "Then list up to three source URLs, one per line."
)


def openrouter_search(settings: Settings, max_results: int = 5) -> Search:
    clients: list[AsyncOpenAI] = []  # made on first use, so tests never need a key

    async def search(query: str) -> str:
        if settings.openrouter_api_key is None:
            raise ToolError("Web search needs OPENROUTER_API_KEY.")
        if not clients:
            key = settings.openrouter_api_key.get_secret_value()
            clients.append(AsyncOpenAI(api_key=key, base_url=settings.llm.base_url))
        client = clients[0]
        extra: dict[str, Any] = {"plugins": [{"id": "web", "max_results": max_results}]}
        try:
            response = await client.chat.completions.create(
                model=settings.llm.model,
                messages=[
                    {"role": "system", "content": INSTRUCTIONS},
                    {"role": "user", "content": query},
                ],
                max_tokens=400,
                extra_body=extra,
            )
        except APIError as e:
            raise ToolError(f"The web search failed: {e.message}") from None
        content = response.choices[0].message.content if response.choices else None
        return content or "The search returned nothing."

    return search


def web_tools(search: Search) -> list[Tool]:
    @tool(risk="safe")
    async def web_search(
        query: Annotated[str, Field(description="What to look up, as a full question.")],
    ) -> str:
        """Search the web for current information: news, weather, prices, documentation."""
        return await search(query)

    return [web_search]
