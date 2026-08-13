import VerticalAlignBottomRounded from "@mui/icons-material/VerticalAlignBottomRounded";
import { Box, IconButton, Stack, Tooltip } from "@mui/material";
import type { ReactNode } from "react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

type ScrollMetrics = Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">;

// eslint-disable-next-line react-refresh/only-export-components
export function isNearConversationBottom(metrics: ScrollMetrics, threshold = 72): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight < threshold;
}

// eslint-disable-next-line react-refresh/only-export-components
export function shouldPositionForNewMessage(previousChildCount: number | null, nextChildCount: number, wasAtBottom: boolean): boolean {
  return previousChildCount === null || (nextChildCount > previousChildCount && wasAtBottom);
}

export function ConversationScroller({ children, contentVersion, padding = 2.5 }: { children: ReactNode; contentVersion: string; padding?: number }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const childCountRef = useRef<number | null>(null);
  const atBottomRef = useRef(true);
  const followAfterSettled = useRef(false);
  const programmaticScroll = useRef(false);
  const settleTimer = useRef<number | undefined>(undefined);
  const programmaticTimer = useRef<number | undefined>(undefined);
  const [atBottom, setAtBottom] = useState(true);

  const measurePosition = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return true;
    const nextAtBottom = isNearConversationBottom(element);
    atBottomRef.current = nextAtBottom;
    setAtBottom(nextAtBottom);
    return nextAtBottom;
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior, keepFollowing: boolean) => {
    const element = scrollRef.current;
    if (!element) return;
    window.clearTimeout(programmaticTimer.current);
    programmaticScroll.current = true;
    followAfterSettled.current = keepFollowing;
    element.scrollTo({ top: element.scrollHeight, behavior });
    atBottomRef.current = true;
    setAtBottom(true);
    programmaticTimer.current = window.setTimeout(() => {
      programmaticScroll.current = false;
      measurePosition();
    }, behavior === "smooth" ? 450 : 50);
  }, [measurePosition]);

  function updateScrollState() {
    const nextAtBottom = measurePosition();
    if (programmaticScroll.current) return;
    followAfterSettled.current = nextAtBottom;
    if (!nextAtBottom) window.clearTimeout(settleTimer.current);
  }

  useLayoutEffect(() => {
    const nextChildCount = contentRef.current?.childElementCount ?? 0;
    const shouldPosition = shouldPositionForNewMessage(childCountRef.current, nextChildCount, atBottomRef.current);
    childCountRef.current = nextChildCount;
    if (shouldPosition) scrollToBottom("auto", followAfterSettled.current);
  }, [contentVersion, scrollToBottom]);

  useEffect(() => {
    if (!followAfterSettled.current) return;
    window.clearTimeout(settleTimer.current);
    // Following only after a pause keeps the current text stable while tokens
    // arrive, but still lets an explicit "go to bottom" action follow progress.
    settleTimer.current = window.setTimeout(() => scrollToBottom("auto", true), 900);
    return () => window.clearTimeout(settleTimer.current);
  }, [contentVersion, scrollToBottom]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const observer = new ResizeObserver(() => measurePosition());
    observer.observe(content);
    return () => observer.disconnect();
  }, [measurePosition]);

  useEffect(() => () => {
    window.clearTimeout(settleTimer.current);
    window.clearTimeout(programmaticTimer.current);
  }, []);

  return <Box sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
    <Box ref={scrollRef} onScroll={updateScrollState} sx={{ height: "100%", minHeight: 0, overflowY: "auto", overflowX: "hidden", WebkitOverflowScrolling: "touch", touchAction: "pan-y" }}>
      <Stack ref={contentRef} spacing={1.5} sx={{ minHeight: "100%", p: padding, "& > *": { flexShrink: 0 } }}>
        {children}
      </Stack>
    </Box>
    {!atBottom && <Tooltip title="直达最底">
      <IconButton aria-label="直达对话最底" onClick={() => scrollToBottom("smooth", true)} sx={{ position: "absolute", right: 12, bottom: 12, width: 38, height: 38, bgcolor: "background.paper", color: "primary.main", border: 1, borderColor: "divider", boxShadow: 2, "&:hover": { bgcolor: "background.paper" } }}>
        <VerticalAlignBottomRounded fontSize="small" />
      </IconButton>
    </Tooltip>}
  </Box>;
}
