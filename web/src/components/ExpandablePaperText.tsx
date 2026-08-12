import ExpandLessRounded from "@mui/icons-material/ExpandLessRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import { Box, Button } from "@mui/material";
import { useEffect, useRef, useState } from "react";
import { MarkdownMessage } from "./MarkdownMessage";

export function ExpandablePaperText({
  children,
  lines = 3,
  color = "text.secondary",
}: {
  children: string;
  lines?: number;
  color?: "text.primary" | "text.secondary";
}) {
  const textRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);

  useEffect(() => {
    const element = textRef.current;
    if (!element) return;
    const measure = () => setOverflowing(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [children, lines, expanded]);

  return (
    <Box>
      <Box
        ref={textRef}
        color={color}
        sx={{
          fontSize: "0.875rem",
          lineHeight: 1.7,
          ...(expanded ? {} : {
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: lines,
            overflow: "hidden",
          }),
        }}
      >
        <MarkdownMessage content={children} />
      </Box>
      {(overflowing || expanded) && (
        <Button
          size="small"
          color="inherit"
          endIcon={expanded ? <ExpandLessRounded /> : <ExpandMoreRounded />}
          onClick={(event) => {
            event.stopPropagation();
            setExpanded((value) => !value);
          }}
          sx={{ mt: 0.25, px: 0, minWidth: 0, color: "text.secondary" }}
        >
          {expanded ? "收起" : "展开"}
        </Button>
      )}
    </Box>
  );
}
