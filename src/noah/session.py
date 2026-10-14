"""Builds a running Noah: the agent, its tools, the task manager and the announcer.

`noah chat`, `noah say` and the voice daemon all start from `start()`, and differ only in how
they confirm actions and where announcements go.
"""

from dataclasses import dataclass

from noah.agent.llm import LLM, OpenRouterLLM
from noah.agent.loop import Agent
from noah.agent.prompts import make_system_prompt
from noah.config import Settings
from noah.tasks.announcer import Announcer, Say
from noah.tasks.manager import TaskManager
from noah.tasks.notifier import GdbusNotifier, Notifier
from noah.tasks.store import TaskStore
from noah.tools import default_tools
from noah.tools.checklist import checklist_tools, render_task
from noah.tools.registry import ToolRegistry
from noah.tools.safety import AuditLog, Confirm


class MissingAPIKey(Exception):
    pass


@dataclass
class Noah:
    settings: Settings
    agent: Agent
    tasks: TaskManager
    announcer: Announcer
    store: TaskStore

    async def close(self) -> None:
        self.announcer.flush()
        await self.store.close()


def make_llm(settings: Settings) -> LLM:
    if settings.openrouter_api_key is None:
        raise MissingAPIKey
    return OpenRouterLLM(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.llm.model,
        base_url=settings.llm.base_url,
    )


async def start(
    settings: Settings,
    confirm: Confirm,
    say: Say,
    llm: LLM | None = None,
    notifier: Notifier | None = None,
) -> Noah:
    """Build Noah and load unfinished checklists. Raises MissingAPIKey without an LLM or key."""
    llm = llm or make_llm(settings)
    store = TaskStore(settings.data_dir / "noah.db")
    tasks = TaskManager(store)
    await tasks.load()

    if notifier is None and settings.tasks.notifications:
        notifier = GdbusNotifier()
    announcer = Announcer(say, notifier, verbosity=settings.tasks.verbosity)
    tasks.subscribers.append(announcer)

    registry = ToolRegistry(
        [*default_tools(), *checklist_tools(tasks)],
        confirm=tasks.gate(confirm),
        audit=AuditLog(settings.state_dir / "actions.log"),
    )

    def checklists() -> str:
        return "\n".join(render_task(t) for t in tasks.open_tasks())

    def step_limit() -> int:
        has_open = bool(tasks.open_tasks())
        return settings.llm.max_steps_with_checklist if has_open else settings.llm.max_steps

    agent = Agent(
        llm=llm,
        tools=registry,
        system_prompt=make_system_prompt(checklists),
        max_steps=step_limit,
    )
    return Noah(settings, agent, tasks, announcer, store)
