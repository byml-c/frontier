import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import BlockRounded from "@mui/icons-material/BlockRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import MenuBookRounded from "@mui/icons-material/MenuBookRounded";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDate, jsonBody, statusLabel } from "../api";
import type { Paper } from "../types";
import { Modal } from "./Modal";
import { PaperMetadataDialog } from "./PaperMetadataDialog";

export function PaperCard({ paper, rank, featured = false }: { paper: Paper; rank?: number; featured?: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [ignoreOpen, setIgnoreOpen] = useState(false);
  const [ignoreReason, setIgnoreReason] = useState("");
  const [metadataOpen, setMetadataOpen] = useState(false);
  const ignore = useMutation({ mutationFn: () => api(`/api/papers/${paper.id}/ignore`, jsonBody({ reason: ignoreReason })), onSuccess: () => { setIgnoreOpen(false); queryClient.invalidateQueries({ queryKey: ["brief"] }); queryClient.invalidateQueries({ queryKey: ["papers"] }); } });
  const startReading = useMutation({ mutationFn: () => api(`/api/papers/${paper.id}/start-reading`, { method: "POST" }), onSuccess: () => navigate(`/paper/${paper.id}`) });
  function submitIgnore(event: FormEvent) { event.preventDefault(); if (ignoreReason.trim()) ignore.mutate(); }

  return <Card>
    <CardContent sx={{ p: { xs: .25, md: 1 } }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2} sx={{ mb: 1.5 }}>
        <Stack direction="row" spacing={1} alignItems="center"><Chip size="small" label={rank ? `#${rank}` : paper.source.toUpperCase()} color={featured ? "primary" : "default"} /><Typography variant="caption" color="text.secondary">{paper.source.toUpperCase()} · {formatDate(paper.published_at)}</Typography></Stack>
        <Chip size="small" variant="outlined" label={statusLabel(paper.status)} />
      </Stack>
      {paper.analysis_status && ["queued", "processing"].includes(paper.analysis_status) && <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mb: 2 }}>正在读取全文并整理作者、实验和推荐信息。</Alert>}
      {paper.analysis_status === "failed" && <Alert severity="error" sx={{ mb: 2 }}>处理失败：{paper.analysis_error || "请在任务页查看原因"}</Alert>}
      <Typography variant="h2" component="h2">{paper.title}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{paper.first_author || "作者信息待确认"}{paper.corresponding_author ? ` · 通讯作者 ${paper.corresponding_author}` : " · 通讯作者未标注"}{paper.primary_affiliation ? ` · ${paper.primary_affiliation}` : ""}</Typography>
      <Typography sx={{ mt: 2, lineHeight: 1.75 }}>{paper.summary || paper.abstract || "简介正在整理中。"}</Typography>
      <Box sx={{ mt: 2, p: 1.75, borderRadius: 2, bgcolor: "rgba(227, 242, 253, .48)", backgroundImage: "linear-gradient(135deg, rgba(255,255,255,.45), rgba(187,222,251,.2))", backdropFilter: "blur(14px)", border: "1px solid", borderColor: "rgba(144, 202, 249, .42)" }}><Stack direction="row" spacing={1.25} alignItems="flex-start"><AutoAwesomeRounded color="primary" fontSize="small" /><Box><Typography variant="caption" fontWeight={700} color="primary.dark">推荐理由</Typography><Typography variant="body2" sx={{ mt: .25, lineHeight: 1.65 }}>{paper.agent_comment || paper.recommendation_reason || "等待全文分析后给出判断。"}</Typography></Box></Stack></Box>
      {!!paper.nodes?.length && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 2 }}>{paper.nodes.map((node) => <Chip key={node.id} clickable onClick={() => navigate(`/graph/${node.id}`)} label={node.name} variant="outlined" color="primary" />)}</Stack>}
      <Accordion disableGutters elevation={0} sx={{ mt: 2, border: 0, bgcolor: "transparent", "&:before": { display: "none" } }}><AccordionSummary expandIcon={<ExpandMoreRounded />} sx={{ px: 0, minHeight: 42 }}><Typography variant="body2" fontWeight={650}>查看问题、方法与实验</Typography></AccordionSummary><AccordionDetails sx={{ px: 0, pt: 0 }}><Stack divider={<Divider flexItem />} spacing={2}>{[["研究问题", paper.problem], ["主要创新", paper.innovation], ["实验结果", paper.experiment], ["局限与后续问题", paper.limitations]].map(([label, value]) => <Box key={label}><Typography variant="subtitle2">{label}</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .5, lineHeight: 1.7 }}>{value || "暂未获得足够证据。"}</Typography></Box>)}</Stack></AccordionDetails></Accordion>
    </CardContent>
    <Divider />
    <CardActions sx={{ px: 0, pt: 1.5, pb: 0, flexWrap: "wrap", gap: 1 }}><Button variant="contained" startIcon={<MenuBookRounded />} onClick={() => startReading.mutate()} disabled={startReading.isPending}>开始精读</Button><Button color="inherit" startIcon={<BlockRounded />} onClick={() => setIgnoreOpen(true)}>忽略</Button><Button color="inherit" startIcon={<EditNoteRounded />} onClick={() => setMetadataOpen(true)}>论文信息</Button><Box sx={{ flex: 1 }} />{paper.source_url && <Button component={Link} href={paper.source_url} target="_blank" rel="noreferrer" endIcon={<ArrowOutwardRounded />}>查看原文</Button>}</CardActions>
    <Modal open={ignoreOpen} title="忽略这篇论文" onClose={() => setIgnoreOpen(false)}><Box component="form" onSubmit={submitIgnore}><Typography color="text.secondary" variant="body2" sx={{ mb: 2 }}>请填写原因，后续推荐会据此调整。</Typography><TextField fullWidth autoFocus multiline minRows={4} value={ignoreReason} onChange={(event) => setIgnoreReason(event.target.value)} placeholder="例如：方向相关，但实验不足以支持结论" />{ignore.error && <Alert severity="error" sx={{ mt: 2 }}>{ignore.error.message}</Alert>}<Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 3 }}><Button onClick={() => setIgnoreOpen(false)}>取消</Button><Button variant="contained" color="error" type="submit" disabled={!ignoreReason.trim() || ignore.isPending}>确认忽略</Button></Stack></Box></Modal>
    <PaperMetadataDialog paperId={paper.id} open={metadataOpen} onClose={() => setMetadataOpen(false)} />
  </Card>;
}
