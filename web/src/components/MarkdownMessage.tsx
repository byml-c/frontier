import { Box, Link as MuiLink } from "@mui/material";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import remarkCjkFriendlyGfmStrikethrough from "remark-cjk-friendly-gfm-strikethrough/parseOnly";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { prepareMarkdown } from "./markdownNormalization";

export const MarkdownMessage = memo(function MarkdownMessage({
  content,
  onJump,
  inverted = false,
  mentionColors,
}: {
  content: string;
  onJump?: (page: number) => void;
  inverted?: boolean;
  mentionColors?: Record<string, string>;
}) {
  return (
    <Box
      className="markdown-message"
      sx={{
        minWidth: 0,
        overflowWrap: "anywhere",
        "& > :first-child": { mt: 0 },
        "& > :last-child": { mb: 0 },
        "& p": { my: 0.75, lineHeight: 1.72 },
        "& h1, & h2, & h3, & h4": { mt: 2, mb: 0.75, lineHeight: 1.35 },
        "& h1": { fontSize: "1.3rem" },
        "& h2": { fontSize: "1.15rem" },
        "& h3, & h4": { fontSize: "1rem" },
        "& ul, & ol": { my: 1, pl: 3 },
        "& li": { my: 0.35, lineHeight: 1.65 },
        "& blockquote": {
          my: 1.25,
          mx: 0,
          pl: 1.5,
          borderLeft: "3px solid",
          borderColor: inverted ? "rgba(255,255,255,.55)" : "primary.light",
          opacity: 0.9,
        },
        "& pre": {
          my: 1.25,
          p: 1.5,
          overflowX: "auto",
          borderRadius: 1.5,
          bgcolor: inverted ? "rgba(0,0,0,.18)" : "rgba(15,23,42,.06)",
          fontSize: "0.84rem",
        },
        "& code:not(pre code)": {
          px: 0.55,
          py: 0.15,
          borderRadius: 0.75,
          bgcolor: inverted ? "rgba(0,0,0,.18)" : "rgba(15,23,42,.07)",
          fontSize: "0.88em",
        },
        "& strong": { fontWeight: 750 },
        "& table": { display: "block", maxWidth: "100%", overflowX: "auto", borderCollapse: "collapse", my: 1.25 },
        "& th, & td": { border: "1px solid", borderColor: inverted ? "rgba(255,255,255,.35)" : "divider", px: 1.25, py: 0.75, textAlign: "left" },
        "& th": { bgcolor: inverted ? "rgba(0,0,0,.12)" : "action.hover" },
        "& .katex": { lineHeight: 1.2 },
        "& .katex-display": { my: 1.25, overflowX: "auto", overflowY: "visible", py: 0.5 },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          remarkCjkFriendly,
          remarkCjkFriendlyGfmStrikethrough,
          remarkMath,
        ]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href = "", children }) => {
            const mentionMatch = href.match(/^#frontier-mention-([A-Za-z0-9]{4})$/);
            if (mentionMatch) {
              const color = mentionColors?.[mentionMatch[1]] || "#1976D2";
              return <Box component="span" sx={{ display: "inline", color, fontWeight: 750, bgcolor: `${color}12`, borderRadius: .75, px: .35 }}>{children}</Box>;
            }
            const pageMatch = href.match(/^#frontier-page-(\d+)(?:-(\d+))?$/);
            if (pageMatch && onJump) {
            return (
              <MuiLink
                component="button"
                type="button"
                onClick={() => onJump(Number(pageMatch[1]))}
                sx={{
                  appearance: "none",
                  WebkitAppearance: "none",
                  display: "inline",
                  verticalAlign: "baseline",
                  m: 0,
                  p: 0,
                  minWidth: 0,
                  border: 0,
                  bgcolor: "transparent",
                  color: inverted ? "inherit" : "primary.main",
                  cursor: "pointer",
                  font: "inherit",
                  fontWeight: 650,
                  lineHeight: "inherit",
                }}
              >
                {children}
              </MuiLink>
            );
            }
            return <MuiLink href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined} sx={{ color: inverted ? "inherit" : "primary.main" }}>{children}</MuiLink>;
          },
        }}
      >
        {prepareMarkdown(content)}
      </ReactMarkdown>
    </Box>
  );
});
