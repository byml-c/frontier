import ArchiveRounded from "@mui/icons-material/ArchiveRounded";
import AutoStoriesRounded from "@mui/icons-material/AutoStoriesRounded";
import DescriptionRounded from "@mui/icons-material/DescriptionRounded";
import HubRounded from "@mui/icons-material/HubRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import StorageRounded from "@mui/icons-material/StorageRounded";
import TaskAltRounded from "@mui/icons-material/TaskAltRounded";
import { AppBar, Box, Button, Container, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Stack, Toolbar, Typography, useMediaQuery, useTheme } from "@mui/material";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { templateSurfaces } from "../theme";
import { AgentPanel } from "./AgentPanel";

const navItems = [
  { to: "/", label: "今日简报", icon: DescriptionRounded, end: true },
  { to: "/briefs", label: "往期简报", icon: ArchiveRounded },
  { to: "/graph", label: "研究图谱", icon: HubRounded },
  { to: "/library", label: "论文库", icon: AutoStoriesRounded },
  { to: "/memory", label: "记忆", icon: StorageRounded },
  { to: "/tasks", label: "任务", icon: TaskAltRounded },
  { to: "/settings", label: "设置", icon: SettingsRounded },
];

export function Shell() {
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down("lg"));
  const [menuOpen, setMenuOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentWidth, setAgentWidth] = useState(() => Number(localStorage.getItem("frontier-agent-width")) || 480);
  useEffect(() => { const openAgent = () => setAgentOpen(true); window.addEventListener("frontier:open-agent", openAgent); return () => window.removeEventListener("frontier:open-agent", openAgent); }, []);
  const updateWidth = (value: number) => { setAgentWidth(value); localStorage.setItem("frontier-agent-width", String(value)); };
  const navigation = <List sx={{ p: 1.5 }}>{navItems.map(({ to, label, icon: Icon, end }) => <NavLink to={to} end={end} key={to} style={{ color: "inherit", textDecoration: "none" }} onClick={() => setMenuOpen(false)}>{({ isActive }) => <ListItemButton selected={isActive}><ListItemIcon sx={{ minWidth: 38 }}><Icon fontSize="small" /></ListItemIcon><ListItemText primary={label} /></ListItemButton>}</NavLink>)}</List>;
  return <Box sx={{ minHeight: "100vh" }}>
    <AppBar position="sticky" color="transparent" elevation={0} sx={{ top: 0, width: "100%", pt: { xs: 1, md: 3.5 }, pb: { xs: 1.75, md: 2.25 }, bgcolor: "transparent", backgroundImage: "none", pointerEvents: "none" }}><Container maxWidth="lg" sx={{ pointerEvents: "auto" }}><Toolbar variant="dense" disableGutters sx={{ ...templateSurfaces.navigation, gap: 1 }}>
      {mobile && <IconButton size="small" onClick={() => setMenuOpen(true)} aria-label="打开导航" sx={{ width: 36, height: 36 }}><MenuRounded /></IconButton>}
      <Button component={NavLink} to="/" color="inherit" startIcon={<Box sx={{ width: 32, height: 32, bgcolor: "primary.main", color: "white", borderRadius: 1, display: "grid", placeItems: "center", fontWeight: 800 }}>F</Box>} sx={{ mr: { xs: "auto", lg: 2 }, px: 1, py: 0, height: 36, fontSize: 18, fontWeight: 750 }}>Frontier</Button>
      {!mobile && <Stack direction="row" spacing={0} sx={{ flex: 1 }}>{navItems.map(({ to, label, end }) => <NavLink to={to} end={end} key={to} style={{ color: "inherit", textDecoration: "none" }}>{({ isActive }) => <Button size="small" color={isActive ? "primary" : "inherit"} variant="text" sx={{ px: 1.5, py: 1, height: 36, minWidth: "auto", fontSize: "1rem", bgcolor: isActive ? templateSurfaces.selection.backgroundColor : "transparent" }}>{label}</Button>}</NavLink>)}</Stack>}
      <Button size="small" variant="outlined" startIcon={<PsychologyRounded />} onClick={() => setAgentOpen(true)} sx={{ px: 1.5, py: 1, height: 36, fontSize: "1rem" }}><Box component="span" sx={{ display: { xs: "none", sm: "inline" } }}>研究助手</Box></Button>
    </Toolbar></Container></AppBar>
    <Drawer open={menuOpen} onClose={() => setMenuOpen(false)} PaperProps={{ sx: { width: 280 } }}><Box sx={{ px: 2.5, py: 2 }}><Typography variant="h3">Frontier</Typography><Typography variant="caption" color="text.secondary">个人研究工作台</Typography></Box>{navigation}</Drawer>
    <Box component="main"><Outlet /></Box>
    <AgentPanel open={agentOpen} onClose={() => setAgentOpen(false)} width={agentWidth} onWidthChange={updateWidth} />
  </Box>;
}
