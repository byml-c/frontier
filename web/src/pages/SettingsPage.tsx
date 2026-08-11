import SaveRounded from "@mui/icons-material/SaveRounded";
import { Alert, Button, Card, CardContent, Container, Divider, FormControl, InputLabel, MenuItem, Select, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, Loading } from "../components/States";
import type { Setting } from "../types";

const LABELS: Record<string, { label: string; help: string }> = {
  daily_hour: { label: "日报启动小时", help: "北京时间，默认 05:00" }, daily_minute: { label: "日报启动分钟", help: "保存后重新加载运行计划" }, abstract_limit: { label: "摘要阅读上限", help: "每期最多阅读的摘要数量" }, fulltext_limit: { label: "全文阅读上限", help: "进入推荐判断前完整阅读的论文数量" }, brief_limit: { label: "简报论文上限", help: "每期简报的论文总数" }, featured_limit: { label: "重点推荐上限", help: "重点推荐数量必须小于简报总量" }, candidate_lookback_days: { label: "候选时间窗口", help: "时间衰减参与排序，不作为硬性截止" }, log_level: { label: "日志级别", help: "控制服务器记录的详细程度" },
};
export function SettingsPage() {
  const queryClient = useQueryClient(); const query = useQuery({ queryKey: ["settings"], queryFn: () => api<Setting[]>("/api/settings") }); const [values, setValues] = useState<Record<string, unknown>>({});
  useEffect(() => { if (query.data) setValues(Object.fromEntries(query.data.map((item) => [item.key, item.value_json]))); }, [query.data]);
  const save = useMutation({ mutationFn: () => api("/api/settings", { method: "PUT", body: JSON.stringify(values) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }) });
  return <Container maxWidth="md" sx={{ py: { xs: 4, md: 6 } }}><PageHeader title="运行设置" description="管理日报范围和日志级别。模型地址、凭据与 Codex 状态仍由服务器配置维护。" actions={<Button variant="contained" startIcon={<SaveRounded />} onClick={() => save.mutate()} disabled={save.isPending}>保存设置</Button>} />{query.isLoading ? <Loading /> : query.error ? <ErrorState error={query.error} /> : <Card><CardContent sx={{ p: 0 }}><Stack divider={<Divider />}>{query.data?.filter((item) => LABELS[item.key]).map((item) => <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" spacing={2} key={item.key} sx={{ p: 2.5 }}><div><Typography fontWeight={700}>{LABELS[item.key].label}</Typography><Typography variant="body2" color="text.secondary">{LABELS[item.key].help}</Typography></div>{typeof item.value_json === "number" ? <TextField size="small" type="number" value={Number(values[item.key] ?? item.value_json)} onChange={(event) => setValues({ ...values, [item.key]: Number(event.target.value) })} sx={{ width: { xs: "100%", sm: 150 } }} /> : <FormControl size="small" sx={{ width: { xs: "100%", sm: 180 } }}><InputLabel>日志级别</InputLabel><Select label="日志级别" value={String(values[item.key] ?? item.value_json)} onChange={(event) => setValues({ ...values, [item.key]: event.target.value })}>{["DEBUG", "INFO", "WARNING", "ERROR"].map((value) => <MenuItem value={value} key={value}>{value}</MenuItem>)}</Select></FormControl>}</Stack>)}</Stack></CardContent></Card>}{save.isSuccess && <Alert severity="success" sx={{ mt: 2 }}>设置已保存</Alert>}{save.error && <Alert severity="error" sx={{ mt: 2 }}>{save.error.message}</Alert>}</Container>;
}
