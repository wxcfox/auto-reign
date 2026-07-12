import re
from functools import lru_cache
from importlib.resources import files


class PlatformPromptService:
    BASE_MODULES = ("core", "context_budget")

    def build_platform_prompt(
        self,
        *,
        extra_modules: tuple[str, ...] = (),
    ) -> str:
        names = tuple(dict.fromkeys((*self.BASE_MODULES, *extra_modules)))
        modules = [self.load_module(name) for name in names]
        return "\n\n".join(modules)

    def load_module(self, name: str) -> str:
        return self._load_module(name)

    @staticmethod
    @lru_cache
    def _load_module(name: str) -> str:
        if not re.fullmatch(r"[a-z_]+", name):
            raise ValueError("invalid platform prompt module")
        value = (
            files("app.prompts.platform")
            .joinpath(f"{name}.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        if not value:
            raise ValueError(f"platform prompt module is empty: {name}")
        return value
