import VerticalAlignBottomRounded from "@mui/icons-material/VerticalAlignBottomRounded";
import { Box, IconButton, Stack, Tooltip } from "@mui/material";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

export function ConversationScroller({ children, contentVersion, padding = 2.5 }: { children: ReactNode; contentVersion: string; padding?: number }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  function updateScrollState() {
    const element = scrollRef.current;
    if (!element) return;
    const nextAtBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 72;
    pinnedToBottom.current = nextAtBottom;
    setAtBottom(nextAtBottom);
  }

  function scrollToBottom(behavior: ScrollBehavior = "smooth") {
    const element = scrollRef.current;
    if (!element) return;
    pinnedToBottom.current = true;
    setAtBottom(true);
    element.scrollTo({ top: element.scrollHeight, behavior });
  }

  useEffect(() => {
    if (!pinnedToBottom.current) return;
    const frame = window.requestAnimationFrame(() => scrollToBottom("auto"));
    return () => window.cancelAnimationFrame(frame);
  }, [contentVersion]);

  return <Box sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
    <Stack ref={scrollRef} onScroll={updateScrollState} spacing={1.5} sx={{ height: "100%", minHeight: 0, overflowY: "auto", overflowX: "hidden", p: padding, WebkitOverflowScrolling: "touch", touchAction: "pan-y", "& > *": { flexShrink: 0 } }}>
      {children}
    </Stack>
    {!atBottom && <Tooltip title="直达最底">
      <IconButton aria-label="直达对话最底" onClick={() => scrollToBottom()} sx={{ position: "absolute", right: 12, bottom: 12, width: 38, height: 38, bgcolor: "background.paper", color: "primary.main", border: 1, borderColor: "divider", boxShadow: 2, "&:hover": { bgcolor: "background.paper" } }}>
        <VerticalAlignBottomRounded fontSize="small" />
      </IconButton>
    </Tooltip>}
  </Box>;
}
