import ChatBubbleOutlineRounded from "@mui/icons-material/ChatBubbleOutlineRounded";
import CloseRounded from "@mui/icons-material/CloseRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import ReplyRounded from "@mui/icons-material/ReplyRounded";
import SendRounded from "@mui/icons-material/SendRounded";
import {
  Alert,
  Avatar,
  Badge,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Collapse,
  Drawer,
  Fab,
  IconButton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";
import { api, formatDate, jsonBody } from "../api";
import type { AnonymousProfile, DailyDiscussionPayload, DailyReview } from "../types";
import { linkifyDailyMentions, mentionDraft } from "./dailyMentions";
import { MarkdownMessage } from "./MarkdownMessage";
import { ErrorState, Loading } from "./States";

const RECOMMENDATIONS = [
  [1, "Strong Reject"],
  [2, "Reject"],
  [3, "Borderline Reject"],
  [4, "Borderline Accept"],
  [5, "Accept"],
  [6, "Strong Accept"],
] as const;
const CONFIDENCE = ["Very Low", "Low", "Medium", "High", "Very High"];
const choiceSx = {
  py: .75,
  textTransform: "none",
  bgcolor: "background.paper",
  "&&.Mui-selected": {
    bgcolor: "primary.main",
    borderColor: "primary.main!important",
    color: "primary.contrastText",
    fontWeight: 750,
    "&:hover": { bgcolor: "primary.dark" },
  },
};

function recommendationLabel(score: number) {
  return RECOMMENDATIONS.find(([value]) => value === score)?.[1] ?? String(score);
}

export function DailyDiscussion({ paperId }: { paperId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [composing, setComposing] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [replyingTo, setReplyingTo] = useState<string | null>(null);
  const [replyDrafts, setReplyDrafts] = useState<Record<string, string>>({});
  const discussion = useQuery({
    queryKey: ["daily-discussion", paperId],
    queryFn: () => api<DailyDiscussionPayload>(`/api/daily/papers/${paperId}/reviews`),
    enabled: open,
  });
  const mentionColors = useMemo(() => {
    const profiles = discussion.data?.reviews.flatMap((review) => [review.author, ...review.replies.map((reply) => reply.author)]) ?? [];
    return Object.fromEntries(profiles.map((profile) => [profile.name, profile.color]));
  }, [discussion.data]);
  const myReview = discussion.data?.reviews.find((review) => review.is_mine);
  function openReply(review: DailyReview, author: AnonymousProfile) {
    setExpanded((current) => new Set(current).add(review.id));
    setReplyingTo(review.id);
    setReplyDrafts((current) => ({ ...current, [review.id]: mentionDraft(current[review.id] || "", author.name) }));
  }
  function refresh() { return queryClient.invalidateQueries({ queryKey: ["daily-discussion", paperId] }); }
  return <>
    {!open && <Badge badgeContent={discussion.data?.review_count} color="primary" overlap="circular" sx={{ position: "fixed", zIndex: 20, left: "50%", bottom: "calc(20px + env(safe-area-inset-bottom))", transform: "translateX(-50%)" }}>
      <Fab color="primary" aria-label="打开论文评论" onClick={() => setOpen(true)}><ChatBubbleOutlineRounded /></Fab>
    </Badge>}
    <Drawer anchor="bottom" open={open} onClose={() => setOpen(false)} PaperProps={{ sx: { height: { xs: "88dvh", md: "min(820px, 86dvh)" }, borderRadius: "20px 20px 0 0", overflow: "hidden" } }}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: { xs: 2, md: 3 }, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
          <Box sx={{ flex: 1, minWidth: 0 }}><Typography variant="h3">论文评分与讨论</Typography>{discussion.data && <Typography variant="caption" color="text.secondary">{discussion.data.review_count} 份评价{discussion.data.average_score != null ? ` · 平均 ${discussion.data.average_score}/6` : ""}</Typography>}</Box>
          {discussion.data?.viewer && <Profile profile={discussion.data.viewer} mine />}
        </Stack>
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", p: { xs: 1.5, md: 3 }, pb: 12 }}>
          {discussion.isLoading ? <Loading label="正在加载讨论" /> : discussion.error ? <ErrorState error={discussion.error} /> : <Stack spacing={2} sx={{ maxWidth: 920, mx: "auto" }}>
            {!composing ? <Button variant="contained" onClick={() => setComposing(true)} sx={{ alignSelf: "flex-start" }}>{myReview ? "编辑我的评审" : "发表评分与评论"}</Button> : <ReviewComposer key={myReview?.id || "new"} paperId={paperId} review={myReview} onCancel={() => setComposing(false)} onSaved={async () => { setComposing(false); await refresh(); }} />}
            {!discussion.data?.reviews.length ? <Typography color="text.secondary" sx={{ py: 5, textAlign: "center" }}>还没有评价，欢迎留下第一份匿名评审。</Typography> : discussion.data.reviews.map((review) => <ReviewCard key={review.id} review={review} open={expanded.has(review.id)} replying={replyingTo === review.id} replyDraft={replyDrafts[review.id] || ""} mentionColors={mentionColors} onEdit={() => setComposing(true)} onToggle={() => setExpanded((current) => { const next = new Set(current); if (next.has(review.id)) next.delete(review.id); else next.add(review.id); return next; })} onReply={(author) => openReply(review, author)} onReplyDraft={(value) => setReplyDrafts((current) => ({ ...current, [review.id]: value }))} onReplyClose={() => setReplyingTo(null)} onReplySaved={async () => { setReplyingTo(null); setReplyDrafts((current) => ({ ...current, [review.id]: "" })); await refresh(); }} />)}
          </Stack>}
        </Box>
        <Fab color="primary" aria-label="关闭论文评论" onClick={() => setOpen(false)} sx={{ position: "fixed", zIndex: (theme) => theme.zIndex.drawer + 1, left: "50%", bottom: "calc(20px + env(safe-area-inset-bottom))", transform: "translateX(-50%)" }}><CloseRounded /></Fab>
      </Stack>
    </Drawer>
  </>;
}

function ReviewComposer({ paperId, review, onCancel, onSaved }: { paperId: string; review?: DailyReview; onCancel: () => void; onSaved: () => void }) {
  const [score, setScore] = useState<number | null>(review?.score ?? null); const [confidence, setConfidence] = useState<number | null>(review?.confidence ?? null); const [content, setContent] = useState(review?.content ?? "");
  const save = useMutation({ mutationFn: () => api(`/api/daily/papers/${paperId}/reviews${review ? "/mine" : ""}`, { ...jsonBody({ score, confidence, content }), method: review ? "PUT" : "POST" }), onSuccess: onSaved });
  function submit(event: FormEvent) { event.preventDefault(); if (score && confidence && content.trim()) save.mutate(); }
  return <Card variant="outlined"><CardContent component="form" onSubmit={submit}><Typography variant="h3">{review ? "编辑匿名评审" : "匿名评审"}</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>每篇论文保留一份主评审；内容支持 Markdown。</Typography>
    <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>Score</Typography><ToggleButtonGroup exclusive value={score} onChange={(_, value) => value && setScore(value)} sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(3, 1fr)" }, gap: .75, "& .MuiToggleButtonGroup-grouped": { border: "1px solid !important", borderColor: "divider!important", borderRadius: "8px!important" } }}>{RECOMMENDATIONS.map(([value, label]) => <ToggleButton value={value} key={value} sx={choiceSx}>{value} · {label}</ToggleButton>)}</ToggleButtonGroup>
    <Typography variant="subtitle2" sx={{ mt: 2, mb: 1 }}>Confidence</Typography><ToggleButtonGroup exclusive value={confidence} onChange={(_, value) => value && setConfidence(value)} sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, 1fr)", sm: "repeat(5, 1fr)" }, gap: .75, "& .MuiToggleButtonGroup-grouped": { border: "1px solid !important", borderColor: "divider!important", borderRadius: "8px!important" } }}>{CONFIDENCE.map((label, index) => <ToggleButton value={index + 1} key={label} sx={choiceSx}>{index + 1} · {label}</ToggleButton>)}</ToggleButtonGroup>
    <TextField fullWidth multiline minRows={5} value={content} onChange={(event) => setContent(event.target.value)} label="Comment (Markdown)" sx={{ mt: 2 }} />{save.error && <Alert severity="error" sx={{ mt: 1.5 }}>{save.error.message}</Alert>}<Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2 }}><Button onClick={onCancel}>取消</Button><Button variant="contained" type="submit" disabled={!score || !confidence || !content.trim() || save.isPending}>{save.isPending ? <CircularProgress size={18} color="inherit" /> : review ? "保存" : "发表"}</Button></Stack>
  </CardContent></Card>;
}

function ReviewCard({ review, open, replying, replyDraft, mentionColors, onEdit, onToggle, onReply, onReplyDraft, onReplyClose, onReplySaved }: { review: DailyReview; open: boolean; replying: boolean; replyDraft: string; mentionColors: Record<string, string>; onEdit: () => void; onToggle: () => void; onReply: (author: AnonymousProfile) => void; onReplyDraft: (value: string) => void; onReplyClose: () => void; onReplySaved: () => void }) {
  return <Card variant="outlined"><CardContent sx={{ p: { xs: 1.5, sm: 2.25 } }}><Stack direction="row" alignItems="center" spacing={1}><Profile profile={review.author} mine={review.is_mine} /><Box sx={{ flex: 1 }} /><Tooltip title="回复该作者"><IconButton size="small" onClick={() => onReply(review.author)}><ReplyRounded fontSize="small" /></IconButton></Tooltip></Stack>
    <Box sx={{ mt: 1.5 }}><Typography variant="body2"><Box component="span" fontWeight={750}>Score:</Box> {review.score} ({recommendationLabel(review.score)}){review.initial_score !== review.score && <Box component="span" color="text.secondary"> [Modified: {review.initial_score} → {review.score}]</Box>}</Typography><Typography variant="body2" sx={{ mt: .35 }}><Box component="span" fontWeight={750}>Confidence:</Box> {review.confidence} ({CONFIDENCE[review.confidence - 1]})</Typography><Stack direction="row" alignItems="center" spacing={.5} sx={{ mt: .5 }}><Typography variant="caption" color="text.secondary">{formatDate(review.updated_at || review.created_at, true)}</Typography>{review.is_mine && <Tooltip title="编辑我的评审"><IconButton size="small" onClick={onEdit}><EditRounded fontSize="small" /></IconButton></Tooltip>}</Stack></Box>
    <Box sx={{ mt: 1.5 }}><MarkdownMessage content={linkifyDailyMentions(review.content, Object.keys(mentionColors))} mentionColors={mentionColors} /></Box>
    <Button size="small" color="inherit" onClick={onToggle} endIcon={<ExpandMoreRounded sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .18s" }} />} sx={{ mt: 1, px: 0 }}>{review.replies.length ? `${open ? "收起" : "展开"}讨论（${review.replies.length}）` : "展开回复"}</Button>
    <Collapse in={open}><Stack spacing={1.5} sx={{ mt: 1.5, ml: { xs: 1, sm: 3 }, pl: 1.5, borderLeft: 2, borderColor: "divider" }}>{review.replies.map((reply) => <Box key={reply.id}><Stack direction="row" alignItems="center"><Profile profile={reply.author} mine={reply.is_mine} compact /><Tooltip title={`回复 @${reply.author.name}`}><IconButton size="small" onClick={() => onReply(reply.author)} sx={{ ml: "auto" }}><ReplyRounded fontSize="small" /></IconButton></Tooltip></Stack><Box sx={{ mt: .5 }}><MarkdownMessage content={linkifyDailyMentions(reply.content, Object.keys(mentionColors))} mentionColors={mentionColors} /></Box><Typography variant="caption" color="text.secondary">{formatDate(reply.created_at, true)}</Typography></Box>)}{replying && <ReplyComposer reviewId={review.id} value={replyDraft} onChange={onReplyDraft} onCancel={onReplyClose} onSaved={onReplySaved} />}</Stack></Collapse>
  </CardContent></Card>;
}

function ReplyComposer({ reviewId, value, onChange, onCancel, onSaved }: { reviewId: string; value: string; onChange: (value: string) => void; onCancel: () => void; onSaved: () => void }) {
  const create = useMutation({ mutationFn: () => api(`/api/daily/reviews/${reviewId}/replies`, jsonBody({ content: value })), onSuccess: onSaved });
  function submit(event: FormEvent) { event.preventDefault(); if (value.trim()) create.mutate(); }
  return <Box component="form" onSubmit={submit}><TextField autoFocus fullWidth multiline minRows={3} value={value} onChange={(event) => onChange(event.target.value)} placeholder="回复内容，支持 Markdown 和 @匿名名" />{create.error && <Alert severity="error" sx={{ mt: 1 }}>{create.error.message}</Alert>}<Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 1 }}><Button size="small" onClick={onCancel}>取消</Button><Button size="small" variant="contained" type="submit" endIcon={<SendRounded />} disabled={!value.trim() || create.isPending}>回复</Button></Stack></Box>;
}

function Profile({ profile, mine = false, compact = false }: { profile: AnonymousProfile; mine?: boolean; compact?: boolean }) {
  return <Stack direction="row" alignItems="center" spacing={.75}><Avatar sx={{ width: compact ? 24 : 30, height: compact ? 24 : 30, bgcolor: profile.color, fontSize: compact ? 10 : 11, fontWeight: 750 }}>{profile.name.slice(0, 2)}</Avatar><Typography variant={compact ? "caption" : "body2"} fontWeight={700} sx={{ color: profile.color }}>@{profile.name}</Typography>{mine && <Chip size="small" variant="outlined" label="你" sx={{ height: 20 }} />}</Stack>;
}
