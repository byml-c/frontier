import { Box } from "@mui/material";
import katex from "katex";
import { memo, useMemo } from "react";
import "katex/dist/katex.min.css";
import { parsePaperTitleMath } from "./paperTitleMath";
import { HighlightText } from "./HighlightText";

export const PaperTitle = memo(function PaperTitle({ title, highlightTerms = [] }: { title: string; highlightTerms?: readonly string[] }) {
  const segments = useMemo(() => parsePaperTitleMath(title), [title]);
  return <>{segments.map((segment, index) => {
    if (segment.kind === "text") return <HighlightText key={index} text={segment.value} terms={highlightTerms} />;
    let html = "";
    try {
      html = katex.renderToString(segment.source, {
        displayMode: false,
        output: "htmlAndMathml",
        strict: "ignore",
        throwOnError: true,
        trust: false,
      });
    } catch {
      return segment.raw;
    }
    return (
      <Box
        component="span"
        key={`${segment.raw}:${index}`}
        aria-label={segment.source}
        sx={{ display: "inline", whiteSpace: "nowrap", "& .katex": { fontSize: "1em" } }}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  })}</>;
});
