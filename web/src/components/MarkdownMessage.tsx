import { Box, Link as MuiLink } from "@mui/material";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

function prepareMarkdown(content: string): string {
  return content
    // Model output occasionally leaves whitespace inside strong delimiters
    // (`** important**`). Move that whitespace outside so CommonMark can parse
    // the emphasis without joining adjacent Latin words.
    .replace(/\*\*([^*\n]+?)\*\*/g, (match, body: string) => {
      const leading = body.match(/^[\s\u200B\u3000]+/)?.[0] ?? "";
      const trailing = body.match(/[\s\u200B\u3000]+$/)?.[0] ?? "";
      const normalized = body.trim();
      return normalized ? `${leading.replace(/\u200B/g, "")}**${normalized.replace(/^\u200B+|\u200B+$/g, "")}**${trailing.replace(/\u200B/g, "")}` : match;
    })
    // CommonMark treats a closing emphasis marker followed directly by a CJK/Latin
    // character as ambiguous. Add a visual-neutral word boundary so streamed model
    // output such as `**重点。**下一句` is parsed as strong text.
    .replace(/(\*\*[^*\n]+?\*\*)(?=[\p{L}\p{N}])/gu, "$1 ")
    .replace(/\\\[((?:.|\n)*?)\\\]/g, (_match, body: string) => `$$\n${body}\n$$`)
    .replace(/\\\(((?:.|\n)*?)\\\)/g, (_match, body: string) => `$${body}$`)
    .replace(
      /\[\[(?:page|p)[:. ]?(\d+)(?:\s*[-–—~～]\s*(\d+))?(?:\|([^\]]+))?\]\]/gi,
      (_match, startPage: string, endPage?: string, label?: string) => {
        const range = endPage ? `${startPage}–${endPage}` : startPage;
        return `[${label || `第 ${range} 页`}](#frontier-page-${startPage}${endPage ? `-${endPage}` : ""})`;
      },
    );
}

export function MarkdownMessage({
  content,
  onJump,
  inverted = false,
}: {
  content: string;
  onJump?: (page: number) => void;
  inverted?: boolean;
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
        "& .katex-display": { my: 1.25, overflowX: "auto", overflowY: "hidden", py: 0.25 },
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href = "", children }) => {
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
}
