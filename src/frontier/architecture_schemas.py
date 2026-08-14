from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ArchitectureModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class XYPosition(ArchitectureModel):
    x: float
    y: float

    @model_validator(mode="after")
    def finite(self) -> XYPosition:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("position coordinates must be finite")
        return self


class Size(ArchitectureModel):
    width: float = Field(gt=0, le=5000)
    height: float = Field(gt=0, le=5000)


class DataExample(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["image", "text"]
    asset_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str | None = Field(default=None, max_length=20_000)
    caption: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_content(self) -> DataExample:
        if self.type == "image" and not self.asset_id:
            raise ValueError("image examples require assetId")
        if self.type == "text" and not self.text:
            raise ValueError("text examples require text")
        return self


class NodeBase(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=50_000)
    position: XYPosition
    size: Size | None = None
    parent_module_id: str | None = Field(default=None, max_length=128)
    group_id: str | None = Field(default=None, max_length=128)
    annotation_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ModuleNode(NodeBase):
    type: Literal["module"]
    symbol: str | None = Field(
        default=None,
        max_length=200,
        description="TeX identifier for the module, without Markdown math delimiters",
    )
    architecture: str | None = Field(
        default=None,
        max_length=300,
        description="Concrete model family, backbone, layer stack, or computational operator",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Behavioral properties and configurations; never model architectures or symbols",
    )


class DataNode(NodeBase):
    type: Literal["data"]
    symbol: str | None = Field(
        default=None,
        max_length=200,
        description="TeX identifier for the data, without Markdown math delimiters",
    )
    preprocessing: list[str] = Field(default_factory=list, max_length=20)
    examples: list[DataExample] = Field(default_factory=list, max_length=50)
    primary_example_id: str | None = Field(default=None, max_length=128)


ArchitectureNode = Annotated[ModuleNode | DataNode, Field(discriminator="type")]


class TensorEntity(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=300)
    symbol: str | None = Field(
        default=None,
        max_length=200,
        description="TeX identifier for the tensor, without Markdown math delimiters",
    )
    shape: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    source_ids: list[str] = Field(default_factory=list)


class ArchitectureEdge(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    tensor_id: str | None = Field(default=None, max_length=128)
    kind: Literal["flow", "conditioning"] = "flow"
    label: str | None = Field(default=None, max_length=500)
    source_anchor: Literal["left", "right", "top", "bottom"] | None = None
    target_anchor: Literal["left", "right", "top", "bottom"] | None = None
    source_ids: list[str] = Field(default_factory=list)


class AnnotationTarget(ArchitectureModel):
    type: Literal["node", "edge", "tensor"]
    id: str = Field(min_length=1, max_length=128)


class AnnotationEntity(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    target: AnnotationTarget | None = None
    title: str | None = Field(default=None, max_length=300)
    markdown: str = Field(max_length=50_000)
    created_by: Literal["user", "agent"] | None = None
    source_ids: list[str] = Field(default_factory=list)


class ArchitectureGroup(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    position: XYPosition
    size: Size


class SourceReference(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["paper-page", "paper-figure", "code", "web"]
    page_number: int | None = Field(default=None, ge=1)
    block_id: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=4000)
    label: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def required_locator(self) -> SourceReference:
        if self.kind.startswith("paper-") and self.page_number is None and not self.block_id:
            raise ValueError("paper sources require pageNumber or blockId")
        if self.kind in {"code", "web"} and not self.url:
            raise ValueError("code and web sources require url")
        return self


class AssetReference(ArchitectureModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["image"] = "image"
    src: str = Field(min_length=1, max_length=1000)
    caption: str | None = Field(default=None, max_length=1000)


class ArchitectureScene(ArchitectureModel):
    schema_version: Literal[1] = 1
    layout_epoch: int = Field(default=1, ge=1)
    nodes: list[ArchitectureNode] = Field(default_factory=list, max_length=1000)
    tensors: list[TensorEntity] = Field(default_factory=list, max_length=2000)
    edges: list[ArchitectureEdge] = Field(default_factory=list, max_length=3000)
    annotations: list[AnnotationEntity] = Field(default_factory=list, max_length=3000)
    groups: list[ArchitectureGroup] = Field(default_factory=list, max_length=200)
    sources: list[SourceReference] = Field(default_factory=list, max_length=3000)
    assets: list[AssetReference] = Field(default_factory=list, max_length=1000)


class ArchitectureCommitRequest(ArchitectureModel):
    expected_revision: int = Field(ge=0)
    owner_id: str = Field(min_length=1, max_length=128)
    scene: dict


class ArchitectureAgentCommitRequest(ArchitectureModel):
    expected_revision: int = Field(ge=0)
    task_id: str = Field(min_length=1, max_length=36)
    scene: dict


class ArchitectureLockRequest(ArchitectureModel):
    owner_id: str = Field(min_length=1, max_length=128)


class ArchitectureGenerateRequest(ArchitectureModel):
    rebuild: bool = False


class ArchitectureCommand(ArchitectureModel):
    type: Literal[
        "focus",
        "expand",
        "collapse",
        "highlight",
        "highlight-path",
        "dim-others",
        "show-tensor",
        "clear-transient",
    ]
    target: str | None = Field(default=None, max_length=128)
    targets: list[str] = Field(default_factory=list, max_length=100)
    except_ids: list[str] = Field(default_factory=list, alias="except", max_length=100)
    tensor_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def required_arguments(self) -> ArchitectureCommand:
        if self.type in {"focus", "expand", "collapse"} and not self.target:
            raise ValueError(f"{self.type} requires target")
        if self.type in {"highlight", "highlight-path"} and not self.targets:
            raise ValueError(f"{self.type} requires targets")
        if self.type == "dim-others" and not self.except_ids:
            raise ValueError("dim-others requires except")
        if self.type == "show-tensor" and not self.tensor_id:
            raise ValueError("show-tensor requires tensorId")
        return self


class ArchitectureCommandRequest(ArchitectureModel):
    commands: list[ArchitectureCommand] = Field(min_length=1, max_length=50)
