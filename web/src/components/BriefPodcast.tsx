import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import GraphicEqRounded from "@mui/icons-material/GraphicEqRounded";
import RefreshRounded from "@mui/icons-material/RefreshRounded";
import { Alert, Box, Button, Card, Chip, CircularProgress, Collapse, Skeleton, Stack, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { apiAssetUrl, generateBriefPodcast, getBriefPodcast } from "../api";
import type { PodcastEpisode } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";

const GENERATING_STATUSES = new Set(["queued", "running", "generating", "processing", "rendering"]);

function isGenerating(episode?: PodcastEpisode | null): boolean {
  return Boolean(episode && GENERATING_STATUSES.has(episode.status));
}

function formatDuration(durationMs?: number | null): string | null {
  if (!durationMs || durationMs < 0) return null;
  const totalSeconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function BriefPodcast({ briefId }: { briefId: string }) {
  const queryClient = useQueryClient();
  const [generationRequested, setGenerationRequested] = useState(false);
  const podcastKey = ["brief-podcast", briefId] as const;
  const podcast = useQuery({
    queryKey: podcastKey,
    queryFn: () => getBriefPodcast(briefId),
    retry: false,
    refetchInterval: (query) => generationRequested || isGenerating(query.state.data) ? 3000 : false,
  });
  const generate = useMutation({
    mutationFn: () => generateBriefPodcast(briefId),
    onSuccess: async () => {
      setGenerationRequested(true);
      await queryClient.invalidateQueries({ queryKey: podcastKey });
    },
  });

  useEffect(() => {
    if (podcast.data && podcast.data.status !== "missing") setGenerationRequested(false);
  }, [podcast.data]);

  if (podcast.isLoading) {
    return <Card component="section" aria-label="每日播客" sx={{ mb: 4 }}><Stack spacing={1.5}><Skeleton width={120} height={28} /><Skeleton height={54} /></Stack></Card>;
  }

  if (podcast.error && !podcast.data) {
    return <Alert severity="error" sx={{ mb: 4 }}>暂时无法加载每日播客，请稍后重试。</Alert>;
  }

  const episode = podcast.data;
  const generating = generate.isPending || generationRequested || isGenerating(episode);
  const missing = !episode || episode.status === "missing";
  const failed = episode?.status === "failed";
  const ready = episode?.status === "ready" && Boolean(episode.audio_url);

  return (
    <Card component="section" aria-labelledby="brief-podcast-title" sx={{ mb: 4, p: { xs: 2, sm: 2.5 } }}>
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "flex-start" }}>
          <Box sx={{ display: "grid", placeItems: "center", width: 42, height: 42, borderRadius: 2, bgcolor: "primary.main", color: "primary.contrastText", flexShrink: 0 }}>
            <GraphicEqRounded aria-hidden="true" />
          </Box>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Typography id="brief-podcast-title" variant="h2">{episode?.title || "每日播客"}</Typography>
              <Chip label="单人播报" size="small" variant="outlined" />
              {ready && formatDuration(episode.duration_ms) && <Typography variant="caption" color="text.secondary">{formatDuration(episode.duration_ms)}</Typography>}
            </Stack>
            <Typography variant="body2" color="text.secondary" aria-live="polite" sx={{ mt: .5 }}>
              {ready ? "收听今天入选论文的重点、方法与启发。" : failed ? "这期播客未能生成，可以重新尝试。" : generating ? "正在整理文字稿并生成音频，完成后会自动显示。" : "把本期研究简报转换为一段随时可听的中文播报。"}
            </Typography>
          </Box>
          <Button
            variant={missing && !failed ? "contained" : "outlined"}
            startIcon={generating ? <CircularProgress size={16} color="inherit" /> : <RefreshRounded />}
            onClick={() => generate.mutate()}
            disabled={generating}
            sx={{ flexShrink: 0, alignSelf: { xs: "stretch", sm: "center" } }}
          >
            {generating ? (ready ? "正在重新生成" : "正在生成") : ready ? "重新生成并替换音频" : failed ? "重新生成" : "生成播客"}
          </Button>
        </Stack>

        {generate.error && <Alert severity="error" role="alert">请求生成失败，请稍后重试。</Alert>}
        {failed && <Alert severity="error" icon={<ErrorOutlineRounded />} role="alert">{episode.error || "生成过程中出现问题，请重新尝试。"}</Alert>}
        {ready && episode.audio_url && (
          <Box
            component="audio"
            controls
            preload="metadata"
            src={apiAssetUrl(episode.audio_url)}
            aria-label={`${episode.title || "每日播客"}音频`}
            sx={{ display: "block", width: "100%", height: 48 }}
          >
            你的浏览器暂不支持音频播放。
          </Box>
        )}
        {ready && <Typography variant="caption" color="text.secondary">重新生成完成后，将替换当前音频。</Typography>}
        {episode?.script_text && <Transcript content={episode.script_text} />}
      </Stack>
    </Card>
  );
}

function Transcript({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const transcriptId = useId();
  return (
    <Box sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}>
      <Button
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls={transcriptId}
        endIcon={<ExpandMoreRounded sx={{ transition: "transform .2s ease", transform: expanded ? "rotate(180deg)" : "none" }} />}
        sx={{ px: 0 }}
      >
        {expanded ? "收起文字稿" : "查看文字稿"}
      </Button>
      <Collapse in={expanded} unmountOnExit>
        <Box id={transcriptId} sx={{ mt: 1, p: { xs: 1.5, sm: 2 }, bgcolor: "action.hover", borderRadius: 2, maxHeight: { xs: 420, sm: 560 }, overflowY: "auto" }}>
          <MarkdownMessage content={content} />
        </Box>
      </Collapse>
    </Box>
  );
}
