from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from app.core.limits import (
    MAX_KNOWLEDGE_SCOPES,
    MAX_PROMPT_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
)
from app.schemas.agents import KnowledgeScope
from app.schemas.modeling import ModelRef


MAX_SEED_FILE_BYTES = 1024 * 1024
MAX_SEED_RESOURCES = 100
MAX_YAML_DEPTH = 50


class _SeedYamlError(yaml.YAMLError):
    pass


class StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader with seed-specific structure and expansion budgets."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._node_depth = 0

    def compose_node(self, parent: Any, index: Any) -> Node:
        if self.check_event(AliasEvent):
            raise _SeedYamlError("seed YAML aliases are not allowed")

        self._node_depth += 1
        if self._node_depth > MAX_YAML_DEPTH:
            self._node_depth -= 1
            raise _SeedYamlError("seed YAML depth limit exceeded")
        try:
            return super().compose_node(parent, index)
        finally:
            self._node_depth -= 1

    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[object, object]:
        if not isinstance(node, MappingNode):
            raise _SeedYamlError("seed YAML mapping is invalid")
        self.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError:
                raise _SeedYamlError("seed YAML mapping key is invalid") from None
            if duplicate:
                raise _SeedYamlError("seed YAML contains a duplicate mapping key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class SeedWorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_type: Literal["agent_home"]
    initial_agents_md: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)

    @field_validator("workspace_type", mode="before")
    @classmethod
    def strip_workspace_type(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SeedWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)
    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)
    config: SeedWorkspaceConfig


class SeedAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    system_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    default_model: ModelRef | None = None
    home_workspace_key: str | None = Field(
        default=None,
        max_length=MAX_RESOURCE_NAME_LENGTH,
    )
    knowledge_scopes: list[KnowledgeScope] = Field(
        default_factory=list,
        max_length=MAX_KNOWLEDGE_SCOPES,
    )


class SeedAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)
    config: SeedAgentConfig


class SeedWorkspaceFile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resources: list[SeedWorkspace] = Field(
        min_length=1,
        max_length=MAX_SEED_RESOURCES,
    )


class SeedAgentFile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resources: list[SeedAgent] = Field(
        min_length=1,
        max_length=MAX_SEED_RESOURCES,
    )


def _read_seed_file(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SEED_FILE_BYTES:
            raise ValueError(f"seed file {path.name} exceeds the size limit")
        with path.open("rb") as seed_file:
            contents = seed_file.read(MAX_SEED_FILE_BYTES + 1)
    except ValueError:
        raise
    except OSError:
        raise ValueError(f"seed file {path.name} could not be read") from None

    if len(contents) > MAX_SEED_FILE_BYTES:
        raise ValueError(f"seed file {path.name} exceeds the size limit")
    try:
        return contents.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"seed file {path.name} is not valid UTF-8") from None


def _load_seed_yaml(path: Path) -> object:
    contents = _read_seed_file(path)
    try:
        data = yaml.load(contents, Loader=StrictSafeLoader)
    except yaml.YAMLError:
        raise ValueError(f"seed file {path.name} contains invalid YAML") from None
    _validate_resource_count(data, path=path)
    return data


def _validate_resource_count(data: object, *, path: Path) -> None:
    if not isinstance(data, dict):
        return
    resources = data.get("resources")
    if isinstance(resources, list) and not 1 <= len(resources) <= MAX_SEED_RESOURCES:
        raise ValueError(f"seed file {path.name} has an invalid resource count")


def load_seed_resources(init_data_dir: Path) -> tuple[list[SeedWorkspace], list[SeedAgent]]:
    workspace_data = _load_seed_yaml(init_data_dir / "workspaces.yaml")
    agent_data = _load_seed_yaml(init_data_dir / "agents.yaml")
    workspaces = SeedWorkspaceFile.model_validate(workspace_data).resources
    agents = SeedAgentFile.model_validate(agent_data).resources
    workspace_keys = {item.key for item in workspaces}
    if len(workspace_keys) != len(workspaces):
        raise ValueError("workspace seed keys must be unique")
    missing = {
        item.config.home_workspace_key
        for item in agents
        if item.config.home_workspace_key is not None
        and item.config.home_workspace_key not in workspace_keys
    }
    if missing:
        raise ValueError(f"agent seed references unknown workspace keys: {sorted(missing)}")
    return workspaces, agents
