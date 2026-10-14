"""Tool definitions and the registry the agent calls them through.

A tool is a typed Python function wrapped with `@tool(risk=...)`. Its parameters become a
pydantic model, which gives both the JSON Schema sent to the model and validation of the
arguments the model sends back. Parameter descriptions go in `Annotated[..., Field(...)]`.
"""

import asyncio
import inspect
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from noah.agent.llm import ToolSchema
from noah.tools.safety import Assessment, AuditLog, CannotAsk, Confirm, Risk

Args = dict[str, Any]
RiskFn = Callable[[Args], Assessment]

RESULT_LIMIT = 4000
AUDIT_PREVIEW = 200


class ToolError(Exception):
    """An expected failure, reported to the model as-is (no traceback, no exception type)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    args_model: type[BaseModel]
    risk: Risk | RiskFn
    describe: Callable[[Args], str] | None = None

    def schema(self) -> ToolSchema:
        parameters = _strip_titles(self.args_model.model_json_schema())
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def assess(self, args: Args) -> Assessment:
        return self.risk(args) if callable(self.risk) else Assessment(self.risk)

    def summary(self, args: Args) -> str:
        if self.describe:
            return self.describe(args)
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{self.name}({rendered})"

    async def invoke(self, args: Args) -> str:
        if inspect.iscoroutinefunction(self.fn):
            result = await self.fn(**args)
        else:
            result = await asyncio.to_thread(self.fn, **args)
        return result if isinstance(result, str) else json.dumps(result, default=str)


def tool(
    risk: Risk | RiskFn, describe: Callable[[Args], str] | None = None
) -> Callable[[Callable[..., Any]], Tool]:
    def wrap(fn: Callable[..., Any]) -> Tool:
        fields: dict[str, Any] = {}
        for name, param in inspect.signature(fn).parameters.items():
            if param.annotation is inspect.Parameter.empty:
                raise TypeError(f"Tool {fn.__name__}: parameter {name!r} needs a type hint")
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[name] = (param.annotation, default)
        args_model = create_model(
            f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields
        )
        return Tool(
            name=fn.__name__,
            description=inspect.cleandoc(fn.__doc__ or ""),
            fn=fn,
            args_model=args_model,
            risk=risk,
            describe=describe,
        )

    return wrap


def _strip_titles(schema: Any) -> Any:
    """Drop pydantic's auto-generated "title" keys; they cost tokens and tell the model nothing.
    Keys inside "properties" are parameter names and stay, even one called "title"."""
    if isinstance(schema, dict):
        return {
            key: (
                {name: _strip_titles(prop) for name, prop in value.items()}
                if key == "properties"
                else _strip_titles(value)
            )
            for key, value in schema.items()
            if key != "title"
        }
    if isinstance(schema, list):
        return [_strip_titles(item) for item in schema]
    return schema


async def _cannot_ask(_: str) -> bool:
    raise CannotAsk


class ToolRegistry:
    """Validates, safety-checks, runs and audits tool calls. Implements the agent's ToolExecutor."""

    def __init__(
        self,
        tools: Iterable[Tool],
        confirm: Confirm = _cannot_ask,
        audit: AuditLog | None = None,
    ) -> None:
        self.tools = {t.name: t for t in tools}
        self.confirm = confirm
        self.audit = audit

    def schemas(self) -> list[ToolSchema]:
        return [t.schema() for t in self.tools.values()]

    async def execute(self, name: str, arguments: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: there is no tool called {name!r}."
        try:
            args = tool.args_model.model_validate_json(arguments or "{}").model_dump()
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}"
                for err in e.errors()
            )
            return f"Error: invalid arguments for {name}: {problems}"

        assessment = tool.assess(args)
        if assessment.risk == "deny":
            result = f"Refused: {assessment.reason or 'this action is not allowed.'}"
            self._record(tool, args, assessment, "refused", result)
            return result
        if assessment.risk == "confirm":
            try:
                allowed = await self.confirm(tool.summary(args))
            except CannotAsk:
                result = (
                    "Not run: this action needs the user's OK and there's no way to ask them "
                    "right now. Tell them what you would have done so they can run it from "
                    "`noah chat` or by voice."
                )
                self._record(tool, args, assessment, "unconfirmed", result)
                return result
            if not allowed:
                result = "The user declined this action. Don't retry it unless they ask again."
                self._record(tool, args, assessment, "declined", result)
                return result

        try:
            result = await tool.invoke(args)
        except ToolError as e:
            result = f"Error: {e}"
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e}"
        result = _truncate(result)
        self._record(tool, args, assessment, "ran", result)
        return result

    def _record(
        self, tool: Tool, args: Args, assessment: Assessment, decision: str, result: str
    ) -> None:
        if self.audit:
            self.audit.record(
                tool=tool.name,
                args=args,
                risk=assessment.risk,
                decision=decision,
                result=result[:AUDIT_PREVIEW],
            )


def _truncate(text: str) -> str:
    if len(text) <= RESULT_LIMIT:
        return text
    return text[:RESULT_LIMIT] + f"\n[truncated, {len(text) - RESULT_LIMIT} more characters]"
