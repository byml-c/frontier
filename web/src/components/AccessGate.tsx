import LockRounded from "@mui/icons-material/LockRounded";
import { Alert, Box, Button, Card, CardContent, CircularProgress, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api, jsonBody } from "../api";
import { Shell } from "./Shell";
import { Loading } from "./States";

type AuthStatus = { trusted: boolean; protection_enabled: boolean };

function useAuthStatus() {
  return useQuery({ queryKey: ["auth-status"], queryFn: () => api<AuthStatus>("/api/auth/status"), staleTime: 60_000, retry: false });
}

export function ProtectedAccess() {
  const status = useAuthStatus();
  if (status.isLoading) return <Loading label="正在验证设备" />;
  if (status.data?.trusted) return <Outlet />;
  return <LoginPage />;
}

export function DailyAccessLayout() {
  const status = useAuthStatus();
  if (status.data?.trusted) return <Shell />;
  return <Outlet />;
}

function LoginPage() {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api<AuthStatus>("/api/auth/login", jsonBody({ password })),
    onSuccess: () => queryClient.setQueryData(["auth-status"], { trusted: true, protection_enabled: true }),
  });
  function submit(event: FormEvent) { event.preventDefault(); if (password) login.mutate(); }
  return <Box sx={{ minHeight: "100dvh", display: "grid", placeItems: "center", bgcolor: "background.default", px: 2 }}>
    <Card sx={{ width: "min(420px, 100%)" }}><CardContent sx={{ p: { xs: 3, sm: 4 } }}><Stack spacing={2.5}>
      <Box sx={{ width: 44, height: 44, borderRadius: 2, display: "grid", placeItems: "center", bgcolor: "primary.main", color: "primary.contrastText" }}><LockRounded /></Box>
      <Box><Typography variant="h2">验证此设备</Typography><Typography color="text.secondary" sx={{ mt: .75 }}>输入访问密码后，这台设备将在有效期内自动保持登录。</Typography></Box>
      <Box component="form" onSubmit={submit}><TextField fullWidth autoFocus type="password" label="访问密码" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />{login.error && <Alert severity="error" sx={{ mt: 1.5 }}>{login.error.message}</Alert>}<Button fullWidth variant="contained" type="submit" disabled={!password || login.isPending} sx={{ mt: 2, height: 44 }}>{login.isPending ? <CircularProgress size={20} color="inherit" /> : "验证并信任此设备"}</Button></Box>
      <Button component={Link} to="/daily" color="inherit">查看公开每日精选</Button>
    </Stack></CardContent></Card>
  </Box>;
}
