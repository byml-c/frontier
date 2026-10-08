import { Box, Typography, type SxProps, type Theme } from "@mui/material";
import katex from "katex";
import { useMemo } from "react";
import { MarkdownMessage } from "../../components/MarkdownMessage";
import { normalizeArchitectureInlineMath, normalizeArchitectureMathSource } from "./architectureText";

export function ArchitectureMath({ value, sx }: { value: string; sx?: SxProps<Theme> }) {
  const source = normalizeArchitectureMathSource(value);
  const html = useMemo(() => {
    if (!source) return "";
    try {
      return katex.renderToString(source, {
        displayMode: false,
        output: "htmlAndMathml",
        strict: "ignore",
        throwOnError: true,
        trust: false,
      });
    } catch {
      return "";
    }
  }, [source]);

  if (!html) return <Typography component="span" sx={sx}>{value}</Typography>;
  return <Box component="span" aria-label={source} sx={{ display: "inline-flex", alignItems: "center", minWidth: 0, "& .katex": { fontSize: "1em" }, ...sx }} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function ArchitectureRichText({ content, noWrap = false, lines, sx }: { content: string; noWrap?: boolean; lines?: number; sx?: SxProps<Theme> }) {
  return <Box sx={{ minWidth: 0, ...sx, "& .markdown-message": { minWidth: 0 }, "& .markdown-message p": { m: 0, lineHeight: 1.35, ...(noWrap ? { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } : {}), ...(lines ? { display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: lines, overflow: "hidden" } : {}) }, "& .markdown-message .katex": { fontSize: "1em" } }}><MarkdownMessage content={normalizeArchitectureInlineMath(content)} /></Box>;
}
