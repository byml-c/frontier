import AutoStoriesRounded from "@mui/icons-material/AutoStoriesRounded";
import DescriptionRounded from "@mui/icons-material/DescriptionRounded";
import HubRounded from "@mui/icons-material/HubRounded";
import MenuRounded from "@mui/icons-material/MenuRounded";
import PsychologyRounded from "@mui/icons-material/PsychologyRounded";
import SettingsRounded from "@mui/icons-material/SettingsRounded";
import StorageRounded from "@mui/icons-material/StorageRounded";
import TaskAltRounded from "@mui/icons-material/TaskAltRounded";
import { Box, Button, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Stack, Toolbar, Typography, useMediaQuery, useTheme } from "@mui/material";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { templateSurfaces } from "../theme";
import { AgentPanel } from "./AgentPanel";
import { type PageHeaderContent, usePageHeaderContent } from "./PageHeaderContext";
import { PageHeaderProvider } from "./PageHeaderProvider";

const navItems = [
  { to: "/", label: "简报", icon: DescriptionRounded, active: (path: string) => path === "/" || path.startsWith("/briefs") },
  { to: "/graph", label: "研究图谱", icon: HubRounded },
  { to: "/library", label: "论文", icon: AutoStoriesRounded },
  { to: "/memory", label: "记忆", icon: StorageRounded },
  { to: "/tasks", label: "任务", icon: TaskAltRounded },
  { to: "/settings", label: "设置", icon: SettingsRounded },
];

function routeHeader(pathname: string): PageHeaderContent | null {
  if (pathname === "/") return { title: "研究简报" };
  if (pathname === "/briefs" || pathname.startsWith("/briefs/")) return { title: "研究简报" };
  if (pathname === "/graph" || pathname.startsWith("/graph/")) return { title: "研究图谱" };
  if (pathname === "/library") return { title: "论文库" };
  if (pathname.startsWith("/paper/")) return { title: "论文阅读" };
  if (pathname === "/memory") return { title: "研究记忆" };
  if (pathname === "/tasks") return { title: "任务中心" };
  if (pathname === "/settings") return { title: "运行设置" };
  return null;
}

export function Shell() {
  return <PageHeaderProvider><ShellLayout /></PageHeaderProvider>;
}

function ShellLayout() {
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down("lg"));
  const { pathname } = useLocation();
  const pageHeader = usePageHeaderContent() ?? routeHeader(pathname);
  const readerRoute = pathname.startsWith("/paper/");
  const [menuOpen, setMenuOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentWidth, setAgentWidth] = useState(() => Number(localStorage.getItem("frontier-agent-width")) || 480);
  useEffect(() => { const openAgent = () => setAgentOpen(true); window.addEventListener("frontier:open-agent", openAgent); return () => window.removeEventListener("frontier:open-agent", openAgent); }, []);
  const updateWidth = (value: number) => { setAgentWidth(value); localStorage.setItem("frontier-agent-width", String(value)); };
  const navigation = <List sx={{ p: 1.5 }}>{navItems.map(({ to, label, icon: Icon, active }) => <NavLink to={to} key={to} style={{ color: "inherit", textDecoration: "none" }} onClick={() => setMenuOpen(false)}><ListItemButton selected={active ? active(pathname) : pathname === to || pathname.startsWith(`${to}/`)}><ListItemIcon sx={{ minWidth: 38 }}><Icon fontSize="small" /></ListItemIcon><ListItemText primary={label} /></ListItemButton></NavLink>)}</List>;

  return <Box sx={readerRoute ? { height: "100dvh", display: "flex", flexDirection: "column", overflow: "hidden" } : { minHeight: "100vh" }}>
    <Box component="header" sx={{ width: "100%", bgcolor: "background.paper", borderBottom: 1, borderColor: "divider" }}>
      <Box sx={{ px: { xs: 1.5, md: 3 } }}>
        <Toolbar variant="dense" disableGutters sx={{ gap: 1, minHeight: { xs: 52, md: 56 } }}>
          {mobile && <IconButton size="small" onClick={() => setMenuOpen(true)} aria-label="打开导航" sx={{ width: 36, height: 36 }}><MenuRounded /></IconButton>}
          <Button component={NavLink} to="/" color="inherit" startIcon={<Box sx={{ width: 32, height: 32, bgcolor: "primary.main", color: "white", borderRadius: 1, display: "grid", placeItems: "center", fontWeight: 800 }}>F</Box>} sx={{ mr: { xs: "auto", lg: 2 }, px: 1, py: 0, height: 36, fontSize: 18, fontWeight: 750 }}>Frontier</Button>
          {!mobile && <Stack direction="row" spacing={0} sx={{ flex: 1 }}>{navItems.map(({ to, label, active }) => { const selected = active ? active(pathname) : pathname === to || pathname.startsWith(`${to}/`); return <NavLink to={to} key={to} style={{ color: "inherit", textDecoration: "none" }}><Button size="small" color={selected ? "primary" : "inherit"} variant="text" sx={{ px: 1.5, py: 1, height: 36, minWidth: "auto", fontSize: "1rem", bgcolor: selected ? templateSurfaces.selection.backgroundColor : "transparent" }}>{label}</Button></NavLink>; })}</Stack>}
          <Button size="small" variant="outlined" startIcon={<PsychologyRounded />} onClick={() => setAgentOpen(true)} sx={{ px: 1.5, py: 1, height: 36, fontSize: "1rem" }}><Box component="span" sx={{ display: { xs: "none", sm: "inline" } }}>研究助手</Box></Button>
        </Toolbar>
        {pageHeader && <Box component="section" sx={{ minHeight: { xs: 52, md: 56 }, py: 1, borderTop: 1, borderColor: "divider", display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)", alignItems: "center", columnGap: 1.5 }}>
          <Box sx={{ minWidth: 0, overflow: "hidden" }}>
            {pageHeader.eyebrow && <Typography variant="caption" color="text.secondary" noWrap display="block" sx={{ mb: .15 }}>{pageHeader.eyebrow}</Typography>}
            <Typography noWrap sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", fontSize: "1.05rem", lineHeight: 1.35, fontWeight: 650 }}>{pageHeader.title}</Typography>
          </Box>
          <Box sx={{ justifySelf: "center", minWidth: 0 }}>{pageHeader.tabs}</Box>
          {pageHeader.actions && <Stack direction="row" spacing={1} sx={{ justifySelf: "end", minWidth: 0 }}>{pageHeader.actions}</Stack>}
        </Box>}
      </Box>
    </Box>
    <Drawer open={menuOpen} onClose={() => setMenuOpen(false)} PaperProps={{ sx: { width: 280 } }}><Box sx={{ px: 2.5, py: 2 }}><Typography variant="h3">Frontier</Typography><Typography variant="caption" color="text.secondary">个人研究工作台</Typography></Box>{navigation}</Drawer>
    <Box component="main" sx={{ flex: readerRoute ? 1 : undefined, minHeight: readerRoute ? 0 : undefined, "& > .MuiContainer-root:has(> [data-page-header-anchor])": { pt: { xs: 2, md: 2.5 } }, ...(readerRoute ? { "& > *": { height: "100% !important" } } : {}) }}><Outlet /></Box>
    <AgentPanel open={agentOpen} onClose={() => setAgentOpen(false)} width={agentWidth} onWidthChange={updateWidth} />
  </Box>;
}
