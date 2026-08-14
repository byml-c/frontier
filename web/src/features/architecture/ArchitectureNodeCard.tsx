import { Box, Card, InputBase, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";
import { MarkdownMessage } from "../../components/MarkdownMessage";
import type { ArchitectureNode } from "./types";
import { ArchitectureMath, ArchitectureRichText } from "./ArchitectureText";

export type ArchitectureNodeDraft = {
  title: string;
  symbol: string;
  architecture: string;
  keywords: string;
  description: string;
  annotation: string;
};

function surface(type: ArchitectureNode["type"], highlighted = false, dimmed = false) {
  return {
    width: "100%",
    minWidth: 0,
    position: "relative" as const,
    overflow: "hidden",
    border: 1.5,
    borderColor: highlighted ? "primary.main" : type === "module" ? "#657184" : "#9A7440",
    bgcolor: dimmed ? (type === "module" ? "rgba(247,248,250,.45)" : "rgba(251,248,241,.4)") : type === "module" ? "#fff" : "#FFFCF5",
    opacity: dimmed ? .18 : 1,
    borderRadius: 1,
    boxShadow: "none",
    backgroundImage: "none",
    transition: "opacity .18s, border-color .18s",
  };
}

export function ArchitectureNodeCard({ node, compact = false, fill = false, highlighted = false, dimmed = false, assetUrl, overlay, reserveActionSpace = false, interactiveTitle = false, annotation, onJumpToPage }: { node: ArchitectureNode; compact?: boolean; fill?: boolean; highlighted?: boolean; dimmed?: boolean; assetUrl?: string; overlay?: ReactNode; reserveActionSpace?: boolean; interactiveTitle?: boolean; annotation?: string; onJumpToPage?: (page: number) => void }) {
  const details = node.type === "module" ? node.keywords ?? [] : node.preprocessing ?? [];
  return <Card sx={{ ...surface(node.type, highlighted, dimmed), height: fill ? "100%" : "auto", p: compact ? (node.type === "module" ? "10.8px 14px" : "11.2px") : { xs: 1.5, sm: 2.25 }, cursor: interactiveTitle ? "default" : undefined }}>
    {overlay}
    <Stack spacing={compact ? .45 : 1.25} sx={{ height: fill ? "100%" : "auto", minWidth: 0 }}>
      <Box className={interactiveTitle ? "architecture-node-title" : undefined} sx={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", columnGap: .8, rowGap: .15, width: "100%", minWidth: 0, pr: reserveActionSpace ? 3.5 : 0, cursor: interactiveTitle ? "grab" : undefined, userSelect: interactiveTitle ? "none" : undefined, "&:active": interactiveTitle ? { cursor: "grabbing" } : undefined }}>
        <Typography sx={{ minWidth: 0, flex: "0 1 auto", maxWidth: "100%", overflowWrap: "anywhere", whiteSpace: "normal", fontWeight: compact ? 750 : 720, fontSize: compact ? (node.type === "module" ? ".93rem" : ".9rem") : "1.08rem", lineHeight: 1.25 }}>{node.title}</Typography>
        {node.symbol && <ArchitectureMath value={node.symbol} sx={{ flex: "0 0 auto", color: "primary.dark", fontSize: compact ? ".98rem" : "1.1rem", lineHeight: 1 }} />}
      </Box>
      {node.type === "module" && node.architecture && <Box sx={{ px: compact ? .7 : 1.1, py: compact ? .35 : .75, borderRadius: .5, bgcolor: "rgba(0,111,230,.06)" }}>{!compact && <Typography variant="caption" color="primary.dark" sx={{ display: "block", mb: .2, fontWeight: 700, letterSpacing: ".03em" }}>Architecture</Typography>}<ArchitectureRichText content={node.architecture} sx={{ color: "text.primary", fontWeight: 650, fontSize: compact ? ".79rem" : ".96rem" }} /></Box>}
      {assetUrl && <Box component="img" src={assetUrl} alt="" sx={{ display: "block", width: "100%", height: compact ? 66 : 180, objectFit: "contain", bgcolor: "#fff", borderRadius: .5, border: 1, borderColor: "divider" }} />}
      {details.length > 0 && <ArchitectureRichText content={details.join(node.type === "module" ? " · " : " → ")} sx={{ color: "text.secondary", fontSize: compact ? ".72rem" : ".88rem" }} />}
      {!compact && node.description && <Box sx={{ p: 1.1, borderRadius: .75, bgcolor: "rgba(15,23,42,.028)" }}><Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: .35, fontWeight: 650 }}>简介</Typography><ArchitectureRichText content={node.description} sx={{ color: "text.secondary", fontSize: ".94rem" }} /></Box>}
      {!compact && annotation && <Box sx={{ p: 1.1, borderRadius: .75, bgcolor: "#FFF9E8", border: 1, borderColor: "#E8D7A5" }}><Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: .35, fontWeight: 650 }}>批注</Typography><MarkdownMessage content={annotation} onJump={onJumpToPage} /></Box>}
    </Stack>
  </Card>;
}

const editInputSx = {
  width: "100%",
  minWidth: 0,
  px: .5,
  mx: -.5,
  borderRadius: .5,
  cursor: "text",
  "& input, & textarea": { p: 0 },
} as const;

function FieldLabel({ children }: { children: ReactNode }) {
  return <Typography variant="caption" color="text.secondary" sx={{ display: "block", fontSize: ".68rem", lineHeight: 1.2, mb: .25 }}>{children}</Typography>;
}

export function ArchitectureNodeCardEditor({ node, draft, disabled = false, onDraft }: { node: ArchitectureNode; draft: ArchitectureNodeDraft; disabled?: boolean; onDraft: (value: ArchitectureNodeDraft) => void }) {
  return <Card sx={{ ...surface(node.type), p: { xs: 1.5, sm: 2.25 }, overflow: "visible" }}>
    <Stack spacing={1.4}>
      <Box sx={{ minWidth: 0 }}>
        <FieldLabel>标题 · 名称 + 符号（TeX）</FieldLabel>
        <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ minWidth: 0 }}>
          <InputBase required disabled={disabled} value={draft.title} aria-label="节点名称" onChange={(event) => onDraft({ ...draft, title: event.target.value })} multiline maxRows={3} sx={{ ...editInputSx, flex: "1 1 160px", minWidth: 0, alignItems: "flex-start", fontWeight: 720, fontSize: "1.08rem", lineHeight: 1.3 }} />
          <Box sx={{ flex: "0 1 160px", minWidth: 92 }}><InputBase disabled={disabled} value={draft.symbol} placeholder={node.type === "module" ? "g_\\psi" : "o_t"} aria-label="数学符号" onChange={(event) => onDraft({ ...draft, symbol: event.target.value })} sx={{ ...editInputSx, fontSize: "1rem" }} />{draft.symbol.trim() && <ArchitectureMath value={draft.symbol} sx={{ mt: .25, color: "primary.dark", fontSize: "1.05rem" }} />}</Box>
        </Stack>
      </Box>
      {node.type === "module" && <Box sx={{ px: 1.1, py: .8, borderRadius: .5, bgcolor: "rgba(0,111,230,.06)" }}>
        <FieldLabel>Architecture · 模型结构 / 骨干 / 运算</FieldLabel>
        <InputBase disabled={disabled} value={draft.architecture} placeholder="例如 DINOv2-B/14" aria-label="Architecture" onChange={(event) => onDraft({ ...draft, architecture: event.target.value })} sx={editInputSx} />
      </Box>}
      <Box>
        <FieldLabel>{node.type === "module" ? "关键词 · 仅属性与配置，用 · 分隔" : "预处理 · 用 → 分隔"}</FieldLabel>
        <InputBase disabled={disabled} value={draft.keywords} placeholder={node.type === "module" ? "例如 shared weights · fine-tuned" : "例如 resize → normalize"} aria-label={node.type === "module" ? "关键词" : "预处理"} onChange={(event) => onDraft({ ...draft, keywords: event.target.value })} sx={editInputSx} />
      </Box>
      <Box sx={{ p: 1.1, borderRadius: .75, bgcolor: "rgba(15,23,42,.028)" }}>
        <FieldLabel>简介 · 支持 Markdown 与 $TeX$</FieldLabel>
        <InputBase disabled={disabled} value={draft.description} placeholder="说明这个节点在架构中的作用" aria-label="详细说明" onChange={(event) => onDraft({ ...draft, description: event.target.value })} multiline minRows={4} sx={{ ...editInputSx, alignItems: "flex-start", lineHeight: 1.65 }} />
      </Box>
      <Box sx={{ p: 1.1, borderRadius: .75, bgcolor: "#FFF9E8", border: 1, borderColor: "#E8D7A5" }}>
        <FieldLabel>批注 · 支持 Markdown 与 $TeX$</FieldLabel>
        <InputBase disabled={disabled} value={draft.annotation} placeholder="补充阅读批注" aria-label="架构批注" onChange={(event) => onDraft({ ...draft, annotation: event.target.value })} multiline minRows={3} sx={{ ...editInputSx, alignItems: "flex-start", lineHeight: 1.65 }} />
      </Box>
    </Stack>
  </Card>;
}
