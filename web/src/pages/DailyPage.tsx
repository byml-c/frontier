import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import CalendarMonthRounded from "@mui/icons-material/CalendarMonthRounded";
import NotesRounded from "@mui/icons-material/NotesRounded";
import { Box, Button, Card, CardContent, Chip, Container, Divider, IconButton, Menu, MenuItem, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatDate } from "../api";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { Empty, ErrorState, Loading } from "../components/States";
import type { DailyPayload } from "../types";

export function DailyPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedDate = searchParams.get("date") || "";
  const [calendarAnchor, setCalendarAnchor] = useState<HTMLElement | null>(null);
  const daily = useQuery({ queryKey: ["daily", selectedDate], queryFn: () => api<DailyPayload>(`/api/daily${selectedDate ? `?target_date=${encodeURIComponent(selectedDate)}` : ""}`) });
  function chooseDate(value: string) { setSearchParams({ date: value }); setCalendarAnchor(null); }
  return <Box sx={{ minHeight: "100%", bgcolor: "background.default" }}><Container maxWidth="md" sx={{ py: { xs: 3, md: 5 } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2} sx={{ mb: 3 }}><Box><Typography variant="overline" color="primary.main" fontWeight={750}>Frontier Daily</Typography><Typography variant="h2" sx={{ mt: .25 }}>{formatDate(daily.data?.date || selectedDate)}</Typography></Box><IconButton aria-label="选择精选日期" aria-haspopup="menu" onClick={(event) => setCalendarAnchor(event.currentTarget)} sx={{ width: 42, height: 42, border: 1, borderColor: "divider", bgcolor: "background.paper" }}><CalendarMonthRounded /></IconButton></Stack>
    <Menu anchorEl={calendarAnchor} open={Boolean(calendarAnchor)} onClose={() => setCalendarAnchor(null)} PaperProps={{ sx: { maxHeight: 360, minWidth: 180 } }}>{!daily.data?.available_dates.length && <MenuItem disabled>暂无历史精选</MenuItem>}{daily.data?.available_dates.map((value) => <MenuItem key={value} selected={value === daily.data?.date} onClick={() => chooseDate(value)}>{formatDate(value)}</MenuItem>)}</Menu>
    {daily.isLoading ? <Loading label="正在加载每日精选" /> : daily.error ? <ErrorState error={daily.error} /> : !daily.data?.selection ? <Empty title="这一天还没有精选论文" body="有新的每日精选后，可以从右上角日历查看。" /> : <DailyPaperCard selection={daily.data.selection} />}
  </Container></Box>;
}

function DailyPaperCard({ selection }: { selection: NonNullable<DailyPayload["selection"]> }) {
  const { paper, annotations } = selection;
  const sourceUrl = paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}` : paper.source_url;
  const authors = paper.authors?.map((author) => author.name).filter(Boolean).join(", ");
  const analysis = [["研究问题", paper.problem], ["主要创新", paper.innovation], ["实验结果", paper.experiment], ["局限与后续问题", paper.limitations], ["精读总结", paper.completion_summary]].filter(([, value]) => Boolean(value));
  return <Card><CardContent sx={{ p: { xs: 2.25, sm: 3.5 } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={2}><Stack direction="row" flexWrap="wrap" gap={.75}><Chip size="small" color="primary" label="每日精选" />{paper.tags.map((tag) => <Chip size="small" variant="outlined" label={tag.name} key={tag.id} sx={{ color: tag.color || undefined }} />)}</Stack>{sourceUrl && <Button component="a" href={sourceUrl} target="_blank" rel="noreferrer" endIcon={<ArrowOutwardRounded />} sx={{ flexShrink: 0 }}>{paper.arxiv_id ? "arXiv" : "原文"}</Button>}</Stack>
    <Typography variant="h1" sx={{ mt: 2, fontSize: { xs: "1.55rem", sm: "2rem" }, lineHeight: 1.25 }}>{paper.title}</Typography>
    <Typography color="text.secondary" sx={{ mt: 1 }}>{paper.first_author || "作者信息待确认"}{paper.corresponding_author ? ` · 通讯作者 ${paper.corresponding_author}` : ""}{paper.primary_affiliation ? ` · ${paper.primary_affiliation}` : ""} · {paper.source.toUpperCase()} · {formatDate(paper.published_at)}</Typography>
    {authors && <Typography variant="body2" color="text.secondary" sx={{ mt: .75, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{authors}</Typography>}
    <Box sx={{ mt: 2, fontSize: ".95rem" }}><MarkdownMessage content={paper.summary || paper.abstract || "暂无论文简介。"} /></Box>
    <Divider sx={{ my: 2.5 }} /><Stack direction="row" spacing={1.25} alignItems="flex-start"><AutoAwesomeRounded color="primary" sx={{ mt: .2 }} /><Box><Typography variant="subtitle2">Agent 评语</Typography><Box sx={{ mt: .5 }}><MarkdownMessage content={paper.agent_comment || paper.recommendation_reason || "暂无 Agent 评语。"} /></Box></Box></Stack>
    <Stack divider={<Divider flexItem />} spacing={2.25} sx={{ mt: 3 }}>{analysis.map(([title, value]) => <Box key={title}><Typography variant="h3">{title}</Typography><Box color="text.secondary" sx={{ mt: .75 }}><MarkdownMessage content={value || ""} /></Box></Box>)}</Stack>
    <Divider sx={{ my: 3 }} /><Stack direction="row" spacing={1} alignItems="center"><NotesRounded color="primary" /><Typography variant="h3">阅读批注与笔记</Typography><Chip size="small" label={`${annotations.length} 条`} /></Stack>
    {!annotations.length ? <Typography color="text.secondary" sx={{ mt: 1.5 }}>这篇论文暂时没有公开批注。</Typography> : <Stack spacing={1.5} sx={{ mt: 1.5 }}>{annotations.map((note) => <Box key={note.id} sx={{ pl: 1.5, borderLeft: 4, borderColor: note.color || "primary.light" }}><Typography variant="caption" color="text.secondary">第 {note.page_number} 页</Typography>{note.selected_text && <Typography component="blockquote" sx={{ m: 0, mt: .5, color: "text.secondary", fontFamily: '"Times New Roman", Times, serif', fontStyle: "italic" }}>{note.selected_text}</Typography>}{note.content && <Box sx={{ mt: .75 }}><MarkdownMessage content={note.content} /></Box>}</Box>)}</Stack>}
  </CardContent></Card>;
}
