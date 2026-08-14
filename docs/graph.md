# Architecture Reader Component

**Status:** Design v0.1
**Target stack:** React + Material UI + React Flow
**Primary consumer:** Existing paper recommendation / reading application
**Agent model:** Codex-like coding/research Agent with web, multimodal, file-editing and execution capabilities

---

## 1. Overview

Architecture Reader 是一种与 PDF Reader 并列的论文阅读模式。

它的目标不是显示模型的底层 computational graph，也不是提供通用 draw.io / Excalidraw 式绘图能力，而是：

> 将论文中的模型架构重构为一张统一视觉语言下的、可展开、可批注、可由 Agent 操作的“论文主图”。

典型工作流：

```text
Paper PDF / Main Figure / Web / Code
                  │
                  ▼
            Codex-like Agent
                  │
          research + understand
                  │
                  ▼
        *.architecture.json
                  │
                  ▼
       Architecture Reader
                  │
        canonical main figure
                  │
          ┌───────┴───────┐
          │               │
        expand           ask
          │               │
          └───────┬───────┘
                  ▼
                Agent
```

Architecture Reader 本身不包含聊天系统。

Agent Chat 属于宿主论文阅读应用，Architecture Reader 只负责：

1. 展示架构；
2. 用户阅读和轻量编辑；
3. 暴露 Agent 控图接口；
4. 响应外部 scene 更新。

---

# 2. Product Positioning

宿主论文阅读器中的一级导航可以类似：

```text
[ PDF ] [ Architecture ] [ ... ]
```

PDF Reader：

> 展示作者原始论文叙事。

Architecture Reader：

> 展示 Agent 对论文模型结构的统一重构和解释。

二者对应同一篇论文，但没有父子关系。

Architecture Reader 不应被设计成 PDF Reader 内的一张附属 Card。

---

# 3. Design Goals

## G1. Architecture-first

首先回答：

> 这个模型到底由哪些模块组成，它们如何交互？

而不是：

> PyTorch graph 中有哪些 operator？

默认视图应尽可能接近论文中 Fig. 2 / Fig. 3 的认知粒度。

---

## G2. Canonical visual language

不同论文不继承原论文的 graphic style。

Agent参考原论文：

* composition；
* 模块关系；
* relative emphasis；
* information hierarchy。

Renderer统一决定：

* typography；
* shape；
* border；
* spacing；
* color semantics；
* arrows；
* annotation style。

因此：

```text
Paper A ─┐
Paper B ─┼─→ Canonical Architecture Visual Language
Paper C ─┘
```

用户阅读大量论文后，可以快速形成稳定的视觉认知。

---

## G3. Semantic expansion

模型细节逐级出现。

例如：

```text
Action Expert
```

展开后：

```text
Action Projection
      ↓
   DiT × 18
      ↓
 Output Head
```

继续展开：

```text
DiT Block × 18

Normalization
Attention
FFN
Conditioning
Residual
```

展开必须发生在原画布上下文中，而不是跳转到独立详情页面。

---

## G4. Architecture + knowledge

节点不仅表达“这里有一个模块”，还应允许表达：

* 模块采用什么 architecture；
* 有什么关键机制；
* 输入数据经过什么 preprocessing；
* tensor shape；
* 为什么这样设计；
* 用户自己的理解；
* Agent补充解释。

---

## G5. Agent-native

系统默认执行者是 Codex-like Agent，而不是只能调用固定 function API 的普通 LLM。

因此优先采用：

```text
read file
edit file
validate
render
inspect
iterate
```

而不是设计大量：

```text
createModule()
connectNodes()
addTensor()
...
```

类型的专用 Agent API。

---

# 4. Non-goals

Architecture Reader v1 不追求：

* 通用白板；
* draw.io 替代品；
* 任意 SVG 编辑；
* 自由绘制几何形状；
* neural computational graph debugging；
* ONNX / Torch graph 可视化；
* 自动证明论文架构继承关系；
* 完整模型 ontology；
* 将 Attention / DiT / Transformer 等固化为特殊 schema type；
* 在组件内部实现 Agent Chat；
* 在组件内部负责后端文件存储。

---

# 5. Core Semantic Objects

Architecture Reader 只保留少量基础语义。

```text
Module
Data
Tensor
Edge
Annotation
Group
```

复杂模型全部由这些基础对象组合表达。

---

# 6. Module

Module 表示模型中的一个语义计算模块。

例如：

* Vision Encoder
* VL Expert
* Action Expert
* DiT
* Transformer
* Attention
* Projection
* Tokenizer
* World Model
* RSSM
* Decoder

Module 使用弱 schema。

不存在：

```text
isTransformer
attentionType
isDiffusionModel
isVLM
```

等领域专用字段。

---

## 6.1 Module card

默认约三行信息：

```text
┌────────────────────────────┐
│ Action Expert              │
│ DiT × 18                   │
│ flow matching · causal     │
└────────────────────────────┘
```

语义：

```text
Line 1 → module name
Line 2 → architecture / implementation
Line 3 → keywords / defining characteristics
```

Module 还可以保存独立的 `symbol`。`symbol` 只表示 TeX 数学标识符；
`architecture` 回答“该模块采用什么具体模型、骨干、层堆栈或运算”；
`keywords` 只描述训练状态、行为属性与配置。模型名不得放入 `keywords`，数学标识符也
不得与 architecture 文本混写。

例如：

```text
Vision Encoder
SigLIP ViT-B/16
pretrained
```

或者：

```text
Action Expert
Transformer × N
flow matching · causal attention
```

详细解释不得继续堆入主卡片。

详细内容进入节点 Popout 和 Annotation。

---

## 6.2 Module expansion

可展开 Module 在右上角提供明确的 expand/collapse 控件。

```text
┌────────────────────────────┐
│ Action Expert           ▾  │
│ DiT × 18                   │
│ flow matching · causal     │
└────────────────────────────┘
```

交互：

```text
点击 Card body
→ 打开 Detail Popout

点击右上角 expand
→ 原地展开 / 收起
```

禁止让单击、双击承担多个模糊语义。

---

# 7. Data

Data 是具有独立认知意义的数据对象。

典型例子：

* Current RGB Observation (I_t)
* Language Instruction
* Robot Proprioception
* Noisy Action
* Action Chunk
* Tactile Image
* Depth Observation

Data 与 Tensor 必须区分。

---

## 7.1 Data card

Data 可以包含比 Module 更丰富的数据处理信息。

例如：

```text
┌────────────────────────┐
│ RGB Observation I_t    │
│ Current camera frame   │
│                        │
│    [ thumbnail ]       │
│                        │
│ Resize → Normalize     │
└────────────────────────┘
```

Data 可用于记录：

* 数据含义；
* 数据来源；
* preprocessing；
* normalization；
* frame stacking；
* temporal range；
* crop；
* sensor；
* tokenization；
* 其他实际阅读中的 know-how。

---

## 7.2 Data examples

Data 支持多个 example。

```ts
examples: DataExample[]
```

例如：

* RGB 图片；
* tactile image；
* segmentation；
* 文本 instruction；
* action sequence 示例。

Canvas 上只显示一个 primary thumbnail。

点击 Data 卡片进入 Popout，可以查看全部 examples。

底层应支持：

```text
image example
text example
```

未来可以扩展其他媒体类型。

---

# 8. Tensor

Tensor 是数据流中的辅助理解实体。

例如：

```text
h_vl
[B, N, D]
```

Tensor 与 Data 不同：

> Data 是一个需要被理解的数据对象；Tensor 主要帮助理解模块之间传递了什么。

Tensor 在语义模型中是一等实体，但默认不渲染成完整 Card。

---

## 8.1 Default tensor rendering

默认作为 Edge label：

```text
VL Expert
    │
    │ h_vl
    │ [B,N,D]
    ▼
Action Expert
```

Tensor 可以拥有：

```ts
id
name
symbol
shape
description
```

同一个 Tensor 可以被多条 Edge 引用。

Agent回答问题时，也可以将 Tensor 临时提升为更明显的视觉元素。

---

# 9. Edge

Edge 表示实体之间的关系或数据流。

默认主要表达：

```text
Module → Module
Data → Module
Module → Data
```

Edge 可以引用：

```ts
tensorId
```

而不是重复存储 Tensor 信息。

---

## 9.1 Edge visual semantics

Canonical visual grammar 应至少支持有限的关系样式。

例如：

```text
────────→  primary data flow

┄┄┄┄┄┄→  conditioning / auxiliary input
```

同一起点、终点和关系类型上的处理说明与 Tensor 信息属于同一条 Edge：处理写入
`label`，Tensor 通过 `tensorId` 引用，并在同一个连线标签中展示，不应拆成两条重叠边。
只有 `flow` 与 `conditioning` 等语义确实不同的关系才保留为独立连线。

一个语义算子只建一个 Module。对于前馈架构，主数据流默认保持无环；Q/K/V 投影、
attention 输出拆分、residual、normalization 等同一算子内部步骤应放入该 Module 的
`architecture`、`keywords` 或 `description`，不能用同一节点对之间的互反 Edge 表达
“进入算子”和“返回结果”。只有论文明确存在 recurrence、iteration 或 feedback 时，
才允许互反的 `flow` Edge，并需要在标签或描述中说明循环语义。

是否增加其他 edge semantics 可以在实现中逐步扩展，但禁止让 Agent 自由选择 arbitrary CSS。

---

# 10. Annotation

Annotation 是一等知识对象。

它不是 Module 卡片中的附属 description。

典型用途：

* 用户笔记；
* Agent解释；
* 机制说明；
* 实现细节；
* 问题；
* 阅读心得。

Annotation 支持 Markdown。

并允许 Markdown 中嵌入图片。

---

## 10.1 Default rendering

默认只显示 marker：

```text
Action Expert     💬 3
```

主图不会自动展开全部批注。

---

## 10.2 Pinned annotation

用户可以将某条 Annotation Pin 到 Canvas。

Pin 后它变成一个独立的便利贴式节点：

```text
            ┌────────────────────────┐
            │ Why causal attention?  │
            │                        │
            │ Action tokens ...      │
            └────────────────────────┘
                     │
                     │ annotation link
                     ▼
              Action Expert
```

Pinned Annotation 的视觉语言偏便利贴，而不是论文 footnote。

---

# 11. Group

Group 是 Canvas 中的一等布局和语义区域。

Group 不参与模型数据流。

典型例子：

```text
Training
Inference
Architecture
Pretraining
Policy Learning
```

例如同一个 Canvas：

```text
┌ Training ─────────────────────────────┐
│                                       │
│ clean action → noise → model → loss   │
│                                       │
└───────────────────────────────────────┘


┌ Inference ────────────────────────────┐
│                                       │
│ obs → model → denoise → action        │
│                                       │
└───────────────────────────────────────┘
```

这些区域可以完全不连通。

因此一篇论文原则上只有一个主 Architecture Canvas，而不是：

```text
Architecture tab
Training tab
Inference tab
```

模型训练和推理可以同时作为非连通图存在。

---

# 12. Detail Popout

Canvas 负责：

> 浏览。

Popout 负责：

> 深读和编辑。

Module 和 Data 均使用相同的交互原则：

```text
点击卡片
→ 打开 Popout
```

而不是用小型 Tooltip 或窄 Inspector。

原因：

* Desktop 有足够阅读空间；
* Mobile 也可以直接全屏；
* Markdown、图片和 metadata 有足够空间；
* 避免长期占用 Canvas 横向空间。

---

## 12.1 Module Popout

应至少包含：

```text
Module name
Architecture
Keywords
Description
Annotations
Images inside Markdown
```

允许用户直接修改内容。

---

## 12.2 Data Popout

除普通 metadata 外，还包含：

```text
Examples gallery
Preprocessing / know-how
Description
Annotations
```

Gallery 可以有多张图，但 Canvas 上只展示 primary example。

---

# 13. User Editing Boundary

用户主要负责内容级和阅读级修改。

用户可以直接：

* 移动节点；
* 展开 / 收起；
* 修改 Module 内容；
* 修改 Data 内容；
* 创建 / 编辑 Annotation；
* Pin / Unpin Annotation；
* 查看和修改 Data examples。

主 UI 不重点提供：

* 创建新 Module；
* 删除 Module；
* 修改节点 semantic type；
* 创建 Edge；
* 删除 Edge；
* 大规模 graph restructuring。

这些工作主要通过 Agent 完成。

设计原则：

> Human edits content and presentation.
> Agent edits structure.

这不是安全限制，而是 UI scope 的选择。

---

# 14. Canonical Layout vs Local View

布局分两层。

## 14.1 Canonical layout

由 Agent 生成并保存在 architecture JSON 中：

```text
position
size
group membership
relative composition
```

这是标准论文主图的一部分。

---

## 14.2 Local view override

用户阅读过程中的操作保存在 localStorage：

```text
viewport
expanded modules
local node displacement
selection-related view state
pinned annotation layout
temporary visibility preference
```

用户移动一个节点，不直接破坏 canonical layout。

提供：

```text
Reset layout
```

恢复 Agent 生成的标准主图。

---

# 15. Expansion Layout

Module 原地展开。

展开后不得直接覆盖其他节点。

采用：

> local displacement / local push layout

流程：

```text
expand node
    ↓
calculate new bounds
    ↓
detect overlaps
    ↓
push affected neighbors outward
    ↓
preserve global composition
```

原则：

* 不重新执行完整 DAG layout；
* 不显著改变原论文 composition；
* 只对附近节点让位；
* collapse 后应尽量恢复之前位置。

React Flow负责 interaction primitives。

局部布局逻辑由 Architecture Reader 实现。

---

# 16. Rendering Technology

底层采用：

```text
React Flow
```

但 React Flow 仅作为 interaction engine：

```text
pan
zoom
drag
selection
viewport
edge infrastructure
node coordinates
```

所有视觉由 Architecture Reader 自定义。

最终用户不应该感受到 React Flow 默认的“流程图 UI”。

---

# 17. Material UI Integration

Architecture Reader 的 application chrome 使用 MUI。

例如：

* Popout；
* Dialog；
* menus；
* buttons；
* tooltip；
* text fields；
* Markdown editor wrapper；
* upload control；
* dark/light theme；
* typography；
* palette；
* spacing。

Canvas 内容自身则采用：

> flat / academic / restrained

避免：

* Material Card 堆叠感；
* elevation；
* heavy shadow；
* gradient；
* dashboard aesthetic；
* 大量不同颜色。

---

## 17.1 Theme integration

Canvas visual grammar 应从 MUI Theme 派生基础 token：

```ts
theme.palette
theme.typography
theme.spacing
theme.shape
```

但应该在 Architecture Reader 内映射成自己的 semantic tokens，例如：

```ts
architectureTheme.module
architectureTheme.data
architectureTheme.tensor
architectureTheme.annotation
architectureTheme.edge
architectureTheme.group
```

Agent 不得直接决定颜色和 CSS。

---

# 18. Agent-facing Data vs Frontend Data

外部 Agent 与前端采用不同数据边界。

```text
Agent
  │
  ▼
JSON
  │ parse / validate
  ▼
TypeScript ArchitectureScene
  │
  ▼
React Store
```

---

## 18.1 Agent-facing format

Agent 的工作交换格式为：

```text
*.architecture.json
```

JSON 应：

* 易阅读；
* 易 diff；
* 易编辑；
* 有稳定 ID；
* 不包含前端实现细节。

在 Frontier 宿主应用中，这不表示 Agent 可以直接修改 Server 的应用数据文件。
Server 仍然是唯一数据写入口：Agent 通过 CLI 将当前 Scene checkout 到
`data/tasks/<task_id>/`，编辑临时 `*.architecture.json`，完成校验后再通过 CLI
提交。Server 原子地校验 revision 并替换 canonical Scene。

---

## 18.2 React-facing model

React 内部转换成：

```ts
ArchitectureScene
```

使用严格 TypeScript 类型。

React component 不应在各处直接操作 raw JSON。

---

# 19. Suggested TypeScript Model

初始模型可以保持简单。

```ts
interface ArchitectureScene {
  schemaVersion: 1
  layoutEpoch: number

  nodes: ArchitectureNode[]
  tensors: TensorEntity[]
  edges: ArchitectureEdge[]
  annotations: AnnotationEntity[]
  groups: ArchitectureGroup[]

  sources?: SourceReference[]
  assets?: AssetReference[]
}
```

`schemaVersion` 表示数据契约版本。`layoutEpoch` 只在 Agent 有意重做 canonical
layout 时递增；普通文案、批注和知识更新不得使用户的 local view override 失效。

Server 在 Scene 之外维护：

```ts
interface ArchitectureDocument {
  paperId: string
  status: "missing" | "generating" | "ready" | "failed"
  revision: number
  scene?: ArchitectureScene
  taskId?: string
  error?: string
}
```

`revision` 由 Server 管理，不属于 Agent 可编辑的 Scene。

---

## 19.1 Module

```ts
interface ModuleNode {
  id: string
  type: "module"

  title: string
  symbol?: string
  architecture?: string
  keywords?: string[]

  description?: string

  position: XYPosition
  size?: Size

  parentModuleId?: string
  groupId?: string

  annotationIds?: string[]
  sourceIds?: string[]
}
```

根节点 position 使用 Canvas 绝对坐标；子节点 position 相对 `parentModuleId`。
v1 中一个节点最多有一个父 Module、最多属于一个 Group，Group 不嵌套，Module
层级必须无环。children 和 Group membership 均由上述单向引用派生，禁止同时保存
`children` / `childIds` 形成双重 source of truth。

---

## 19.2 Data

```ts
interface DataNode {
  id: string
  type: "data"

  title: string
  symbol?: string
  description?: string

  preprocessing?: string[]

  examples?: DataExample[]
  primaryExampleId?: string

  position: XYPosition
  size?: Size

  parentModuleId?: string
  groupId?: string

  annotationIds?: string[]
  sourceIds?: string[]
}
```

---

## 19.3 Tensor

```ts
interface TensorEntity {
  id: string

  name?: string
  symbol?: string
  shape?: string
  description?: string
}
```

---

## 19.4 Edge

```ts
interface ArchitectureEdge {
  id: string

  source: string
  target: string

  tensorId?: string

  kind?: "flow" | "conditioning"
  label?: string

  sourceAnchor?: "left" | "right" | "top" | "bottom"
  targetAnchor?: "left" | "right" | "top" | "bottom"
  sourceIds?: string[]
}
```

Renderer 默认根据节点相对位置推断 anchor；只有复杂 composition 需要有限枚举覆盖，
Agent 不能提交任意路径或 CSS。

---

## 19.5 Annotation

```ts
interface AnnotationEntity {
  id: string

  target?: {
    type: "node" | "edge" | "tensor"
    id: string
  }

  title?: string
  markdown: string

  createdBy?: "user" | "agent"
}
```

Pin 状态属于 local view，而非 Annotation 本体。

---

## 19.6 Group

```ts
interface ArchitectureGroup {
  id: string

  title: string

  position: XYPosition
  size: Size
}
```

Group membership 从 node 的 `groupId` 派生。

---

## 19.7 Source reference

Architecture 是 Agent 对论文和代码的重构，MVP 必须保留最小可核验来源：

```ts
interface SourceReference {
  id: string
  kind: "paper-page" | "paper-figure" | "code" | "web"
  pageNumber?: number
  blockId?: string
  url?: string
  label?: string
}
```

Module、Data、Tensor 和 Edge 可以通过 `sourceIds` 引用来源。高级 provenance 和
自动 lineage 仍然不属于 MVP，但关键架构判断不能成为无法追溯的 Agent 断言。

---

# 20. Scene Source of Truth

Architecture JSON 是组件和 Agent 工作流中的 canonical logical document。在 Frontier
宿主应用中，Server 持有的数据库 JSON 是持久化 source of truth；`*.architecture.json`
是 CLI checkout / validate / commit 的工作格式，而不是 Agent 可绕过 Server 直接写入的
应用数据文件。

Agent可以：

```text
checkout architecture.json
edit task-local architecture.json
validate
render
inspect
edit again
commit through CLI
```

不要求 Agent 手工构造 JSON Patch。

Server 保存前必须验证 Scene，并使用 expected revision 做 optimistic concurrency check。

---

# 21. Runtime Scene Update

宿主应用收到新的 architecture JSON 后：

```text
old scene
    ↓
new scene
    ↓
semantic diff
    ↓
incremental store update
```

节点 ID 稳定时，应保留：

* viewport；
* selection；
* local layout override；
* expand state；
* transient interaction state。

禁止为了 JSON 更新直接 reload 整个 React 页面。

因此 Agent 修改一个节点：

```text
action_expert.keywords
```

不应该造成整个 Canvas 闪烁或重新初始化。

---

# 22. Component Persistence Boundary

Architecture Reader 是 controlled component。

建议类似：

```tsx
<ArchitectureReader
  scene={scene}
  onSceneChange={handleSceneChange}
  resolveAsset={resolveAsset}
  onUploadAsset={handleUploadAsset}
/>
```

组件本身不知道：

* 数据库；
* 文件路径；
* Git；
* object storage；
* backend API。

宿主应用负责持久化。

Frontier 宿主使用 SQLite JSON 保存 Scene、revision 和生成状态，使用
`data/architectures/<paper_id>/assets/` 保存图片资产。这样 Scene 能进入现有可读 JSON
备份，而图片沿用应用文件资产边界。

---

# 22.1 Single-writer editing lock

Canonical Scene 任意时刻只允许一个写入者。锁是双向的，而不是只在 Agent 保存时做
最后一次冲突检测：

* 用户进入显式编辑模式时获取锁，Agent 此时不能修改 Scene；
* Agent 确定需要修改时获取同一把锁，然后重新 checkout 最新 Scene；
* 初次生成和重建任务在完整任务期间持有 Agent 锁；
* 普通问答及 focus / highlight 等 view command 不获取编辑锁；
* Agent 持锁时，用户仍可浏览、缩放、展开、使用 local displacement 和对话，只禁用
  Module / Data / Annotation 等 canonical 内容编辑；
* 用户锁通过浏览器心跳续期，退出编辑模式时释放；
* Agent 锁绑定 TaskRun，在任务完成、失败或取消时释放；Server 启动时清理不再处于
  queued / running 状态的遗留 Agent 锁；
* 第二个写入者获取锁失败时必须得到明确的 locked response，禁止排队后静默覆盖。

revision 校验仍作为进程异常、过期页面和多设备访问的最终安全边界。正常流程不提供
复杂三方合并 UI；遇到异常 revision mismatch 时拒绝保存并重新加载。

---

# 23. Three-state Model

系统明确区分三种状态：

```text
JSON Scene
=
What the architecture IS

Agent Command State
=
What the Agent is TALKING ABOUT

Local View State
=
How the user is VIEWING it
```

这是 Architecture Reader 最重要的状态边界之一。

---

# 24. Agent Canvas Command API

Agent Chat 属于宿主应用。

Architecture Reader 暴露统一 command interface。

例如：

```ts
architectureRef.current.execute(command)
```

建议初始命令：

```ts
type ArchitectureCommand =
  | {
      type: "focus"
      target: string
    }
  | {
      type: "expand"
      target: string
    }
  | {
      type: "collapse"
      target: string
    }
  | {
      type: "highlight"
      targets: string[]
    }
  | {
      type: "highlight-path"
      targets: string[]
    }
  | {
      type: "dim-others"
      except: string[]
    }
  | {
      type: "show-tensor"
      tensorId: string
    }
  | {
      type: "clear-transient"
    }
```

这个接口只用于 Agent讲解。

---

# 25. Agent Interaction States

Agent操作分三层。

## 25.1 Transient

回答过程中存在，回答结束可恢复。

例如：

```text
highlight
dim
pulse edge
temporary tensor
```

---

## 25.2 View state

可以保留：

```text
expand
collapse
focus
viewport
```

例如用户问：

> Action Expert内部是什么？

Agent展开 Action Expert 后，回答结束不必自动收回。

---

## 25.3 Persistent architecture changes

例如：

> 发现原图遗漏了 timestep input。

Agent修改：

```text
architecture.json
```

而不是通过 transient command 创建永久节点。

---

# 26. Q&A Example

用户问：

> VL Expert 和 Action Expert 如何通信？

宿主 Agent：

1. 阅读当前 scene；
2. 必要时搜索论文 / 代码；
3. 调用：

```text
expand VL Expert
expand Action Expert
dim unrelated modules
highlight communication path
show relevant tensor
```

4. 用文字回答；
5. transient highlight结束后恢复；
6. expand state保留。

如果发现架构确实缺少重要 Data：

```text
Agent edits architecture.json
```

组件随后增量更新。

---

# 27. Asset Model

Architecture JSON 不保存：

```text
base64
raw image bytes
upload implementation
```

只保存 asset reference。

例如：

```json
{
  "id": "rgb-example-1",
  "type": "image",
  "src": "asset://architecture/rgb-example-1",
  "caption": "Current RGB observation"
}
```

Architecture Reader 从 host 获取：

```ts
resolveAsset(src): string
```

上传则调用：

```ts
onUploadAsset(file)
```

宿主负责实际存储并返回 asset reference。

---

# 28. Responsive / Mobile

Architecture Reader 必须支持 Desktop 与 Mobile。

核心原则：

> Canvas interaction 保持一致，详情阅读使用响应式 Popout。

Desktop：

```text
Popout → centered large dialog / modal
```

Mobile：

```text
Popout → near-fullscreen / fullscreen dialog
```

不依赖固定右侧 Inspector，因此不会因为屏幕变窄失去编辑空间。

---

# 29. Local Storage

localStorage 保存纯阅读状态。

建议 key 至少包含：

```text
paperId
schemaVersion
layoutEpoch
```

例如：

```text
architecture-reader:<paperId>:view
```

内容可以包括：

```ts
interface ArchitectureViewState {
  viewport: Viewport

  expandedNodeIds: string[]

  positionOverrides: Record<string, XYPosition>

  pinnedAnnotationIds: string[]

  pinnedAnnotationPositions: Record<string, XYPosition>
}
```

普通 Scene revision 变化时保留 local view。`layoutEpoch` 变化时清除 canonical position
相关 override，但稳定 ID 对应的 expand、pin 和 selection 状态仍可保留；schemaVersion
不兼容时才整体失效。

---

# 30. Validation

JSON 应通过 schema validation 再进入 React。

至少校验：

* ID 唯一；
* Edge source/target 存在；
* tensorId 存在；
* parentModuleId 引用存在且父对象是 Module；
* annotation target 存在；
* groupId 引用存在；
* primary example 存在；
* Position 数值有效；
* hierarchy 不存在明显非法循环。

Server 对 error 级问题拒绝保存。warning 可以保存，并在 Reader 中显示 diagnostics。
由于 MVP 不引入自动浏览器截图，CLI validation 还应提供确定性的几何诊断：

* canonical 节点明显重叠；
* 子节点超出展开区域；
* Group 无法容纳成员；
* 非法或异常尺寸；
* 缺失关键来源；
* 无效 asset reference。

用户在真实浏览器中检查最终视觉效果，并通过选中节点后的论文对话反馈问题。Agent
根据 selected node IDs 和当前 revision 重新 checkout、修改和提交 Scene，不执行自动
截图迭代。

生成等待页只展示真实、文字形式的活动进度，包括论文读取、工具调用、Scene 校验与
提交状态，以及任务目录中 `scene.architecture.json` 当前实际行数。不使用推测百分比，
也不渲染尚未提交的中间 Scene。活动快照写入 TaskRun，因此刷新页面后仍可恢复。

同一篇论文的架构生成、架构修改和论文对话绑定同一个 paper Conversation，并复用其
`codex_session_id`。它们共享逻辑 Agent 上下文，但同一 Conversation 的 turn 必须串行；
Server 在已有 queued / running task 时拒绝并发启动。底层执行进程按 turn 创建，不将
“共享 Agent”误解为永久占用资源的常驻 OS 进程。

Validation error 不应造成整个 Reader crash。

应该尽量：

```text
render valid subset
+
show diagnostics
```

---

# 31. Visual Design Principles

Canonical Visual Grammar 应遵循：

### Flat

避免明显 elevation。

### Restrained

颜色用于语义，而不是装饰。

### Information dense but readable

一个 Module 默认最多约三行主信息。

### Structure first

主图第一眼应该能看懂：

> 哪些东西属于哪些东西，以及数据从哪里到哪里。

### Details on demand

细节通过：

```text
expand
Popout
annotation
tensor label
```

逐渐出现。

### Stable

同一种语义对象在所有论文中视觉一致。

---

# 32. Recommended Visual Hierarchy

从强到弱大致：

```text
Group title

Primary Module

Secondary Module

Data

Tensor / Edge label

Annotation marker

Background / auxiliary structure
```

不要通过几十种颜色表达 hierarchy。

主要依靠：

* size；
* typography；
* border hierarchy；
* spacing；
* opacity；
* line style。

---

# 33. Original Paper Figure Usage

原论文主图主要作为 Agent 的：

> layout prior + semantic reference

而不是 renderer 的直接输入格式。

Agent应该尽量保留：

* 模块的大致相对位置；
* 主数据流方向；
* 作者强调的主结构；
* Training / Inference 等区域关系。

但不复制：

* 字体；
* 原论文配色；
* arbitrary icon style；
* arbitrary shape style。

---

# 34. Agent Research Responsibility

系统不需要形式化实现论文 inheritance graph。

如果论文 A：

> based on model B

但 A 对结构描述不足，Agent可以自行：

* 搜索 B；
* 阅读 B；
* 阅读 appendix；
* 查 official code；
* 查 config。

最终只需要把：

> A 的完整架构讲清楚。

必要时 Scene 中可以保存轻量 source metadata，但不要求构建 architecture lineage ontology。

---

# 35. Codex Skill

Codex Skill 更偏行为规范，而不是大量工具包装。

至少包括：

```text
how to research architecture
how to choose main-figure granularity
how to reference original figure composition
how to edit architecture JSON
how to validate
how to validate geometry
how to use human visual feedback
when to search cited papers
when not to guess
```

Frontier 的 MVP 不要求 Agent 自动截图。`render / inspect` 在当前阶段指结构校验和用户
反馈后的定向检查；未来可以再加入可选的视觉自动化。

---

# 36. Public Component API

初始 API 可以朝以下方向设计：

```tsx
<ArchitectureReader
  scene={scene}

  onSceneChange={onSceneChange}

  resolveAsset={resolveAsset}
  onUploadAsset={onUploadAsset}

  ref={architectureReaderRef}

  paperId={paperId}
/>
```

Ref：

```ts
interface ArchitectureReaderHandle {
  execute(command: ArchitectureCommand): void

  resetView(): void

  getViewState(): ArchitectureViewState
}
```

后续根据宿主项目的状态管理方式调整。

---

# 37. Suggested Internal Architecture

```text
ArchitectureReader
│
├── SceneProvider
│
├── ViewStateProvider
│
├── CommandController
│
├── Canvas
│   │
│   ├── ReactFlow
│   ├── ModuleNode
│   ├── DataNode
│   ├── TensorLabel
│   ├── AnnotationNode
│   ├── GroupRegion
│   └── ArchitectureEdge
│
├── ExpansionController
│   └── LocalDisplacementLayout
│
├── DetailPopout
│   ├── ModuleEditor
│   ├── DataEditor
│   ├── AnnotationEditor
│   └── ExampleGallery
│
└── PersistenceAdapter
    └── LocalViewStorage
```

---

# 38. MVP Scope

第一版只需要完成一个非常克制的闭环：

```text
architecture JSON
        ↓
canonical canvas
        ↓
Module / Data / Edge
        ↓
expand module
        ↓
local neighbor displacement
        ↓
click node
        ↓
Popout
        ↓
edit content / annotation
        ↓
onSceneChange
        ↓
Server revision + single-writer lock
```

同时加入：

```text
Tensor edge label
Group
Data thumbnail
localStorage view state
highlight command
focus command
on-demand Agent generation
CLI checkout / validate / commit
minimal source references
```

做到这里已经可以实际用于论文阅读。

---

# 39. Features Explicitly Deferred

以下内容不应阻塞 MVP：

* sophisticated automatic layout；
* collaborative editing；
* architecture history；
* visual diff between papers；
* timeline / lineage；
* advanced source provenance；
* equation rendering beyond Markdown；
* arbitrary user-created graph structure；
* custom edge editor；
* multiplayer；
* complete Codex Skill；
* automatic paper-to-architecture pipeline UI；
* automatic screenshot / visual iteration；
* animation-heavy data flow；
* executable model graph。

---

# 40. Future Directions

后续自然扩展方向包括：

### Architecture comparison

```text
π0
vs
π0.5
```

在统一 visual grammar 下直接对比。

### Temporary explanation overlays

Agent回答问题时生成：

```text
mask diagram
attention pattern
token flow
denoising steps
```

作为临时解释层。

### Training / inference playback

允许 Agent按步骤突出：

```text
1 → observation
2 → encode
3 → condition
4 → denoise
5 → action
```

### Paper figure synchronization

点击 Architecture Module：

```text
→ PDF Reader jump to relevant section / figure
```

反向也可以：

```text
PDF selection
→ focus architecture node
```

### Architecture library

同一个基础模块可以拥有稳定视觉表达，例如：

```text
Transformer
DiT
VLM
RSSM
```

但这些应保持为 renderer preset，而不是强制 schema ontology。

---

# 41. Core Architectural Principle

整个组件最终应维持以下边界：

```text
             Architecture JSON
          what the model IS
                    │
                    ▼
             React / TS Scene
                    │
                    ▼
       Canonical Architecture Canvas
              │             │
              │             │
      Local View State   Agent Commands
      how I view it      how AI explains it
```

三者必须尽量解耦。

---

# 42. One-sentence Product Definition

> **Architecture Reader is a canonical, semantically expandable paper architecture canvas that lets users and Agents jointly understand, annotate, and discuss a model while preserving the readability of a paper's main figure.**

中文可以表达为：

> **Architecture Reader 是一种与 PDF 阅读器并列的 Agent 论文阅读视图：Agent 将论文模型重绘为统一视觉语言下的可展开主图，用户围绕模块、数据流、Tensor 与批注逐层理解并继续提问。**
