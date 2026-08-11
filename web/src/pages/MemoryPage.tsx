import EditRounded from "@mui/icons-material/EditRounded";
import LightbulbRounded from "@mui/icons-material/LightbulbRounded";
import PersonRounded from "@mui/icons-material/PersonRounded";
import PowerSettingsNewRounded from "@mui/icons-material/PowerSettingsNewRounded";
import { Box, Button, Card, CardActions, CardContent, Chip, Container, Grid, IconButton, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Empty, ErrorState, Loading } from "../components/States";
import type { Memory } from "../types";

export function MemoryPage() {
  const queryClient = useQueryClient(); const [editing, setEditing] = useState<Memory | null>(null);
  const query = useQuery({ queryKey: ["memories"], queryFn: () => api<Memory[]>("/api/memories") });
  const update = useMutation({ mutationFn: ({ id, payload }: { id: string; payload: Partial<Memory> }) => api(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify(payload) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memories"] }) });
  const groups = query.data?.reduce<Record<string, Memory[]>>((acc, item) => { (acc[item.kind] ??= []).push(item); return acc; }, {}) ?? {};
  return <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}><PageHeader title="研究记忆" description="查看兴趣画像和 Agent 积累的工作经验。每次任务仍会显式读取所需记忆。" />{query.isLoading ? <Loading /> : query.error ? <ErrorState error={query.error} /> : !query.data?.length ? <Empty title="暂无研究记忆" body="兴趣反馈和可复用经验会逐步积累在这里。" /> : <Grid container spacing={3}><Grid size={{ xs: 12, md: 6 }}><MemoryGroup title="兴趣画像" icon={<PersonRounded />} items={groups.interest_profile ?? []} onEdit={setEditing} onToggle={(item) => update.mutate({ id: item.id, payload: { active: !item.active } })} /></Grid><Grid size={{ xs: 12, md: 6 }}><MemoryGroup title="工作经验" icon={<LightbulbRounded />} items={groups.agent_experience ?? []} onEdit={setEditing} onToggle={(item) => update.mutate({ id: item.id, payload: { active: !item.active } })} /></Grid></Grid>}<MemoryEditor memory={editing} onClose={() => setEditing(null)} onSave={(content) => { if (editing) update.mutate({ id: editing.id, payload: { content } }, { onSuccess: () => setEditing(null) }); }} /></Container>;
}
function MemoryGroup({ title, icon, items, onEdit, onToggle }: { title: string; icon: React.ReactNode; items: Memory[]; onEdit: (item: Memory) => void; onToggle: (item: Memory) => void }) { return <Stack spacing={1.5}><Stack direction="row" alignItems="center" spacing={1}><Box color="primary.main">{icon}</Box><Typography variant="h2">{title}</Typography><Chip size="small" label={items.length} /></Stack>{items.map((item) => <Card key={item.id} sx={{ opacity: item.active ? 1 : .58 }}><CardContent><Stack direction="row" justifyContent="space-between"><Box><Chip size="small" label={item.source || "系统"} /><Typography variant="h3" sx={{ mt: 1.5 }}>{item.title}</Typography></Box><Chip size="small" color={item.active ? "success" : "default"} label={item.active ? "启用" : "停用"} /></Stack><Typography color="text.secondary" sx={{ mt: 1.25, whiteSpace: "pre-wrap" }}>{item.content}</Typography></CardContent><CardActions sx={{ justifyContent: "flex-end" }}><IconButton onClick={() => onEdit(item)} aria-label="编辑"><EditRounded /></IconButton><IconButton onClick={() => onToggle(item)} aria-label={item.active ? "停用" : "启用"}><PowerSettingsNewRounded /></IconButton></CardActions></Card>)}</Stack>; }
function MemoryEditor({ memory, onClose, onSave }: { memory: Memory | null; onClose: () => void; onSave: (content: string) => void }) { const [content, setContent] = useState(""); useEffect(() => setContent(memory?.content || ""), [memory]); return <Modal open={!!memory} onClose={onClose} title={memory?.title || "编辑记忆"}><TextField fullWidth multiline minRows={9} value={content} onChange={(event) => setContent(event.target.value)} /><Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2 }}><Button onClick={onClose}>取消</Button><Button variant="contained" onClick={() => onSave(content)}>保存</Button></Stack></Modal>; }
