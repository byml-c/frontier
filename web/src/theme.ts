import { createTheme } from "@mui/material/styles";

export const templateSurfaces = {
  card: {
    backgroundColor: "#FFFFFF",
    backgroundImage: "none",
    border: "1px solid rgba(194, 201, 214, .4)",
    borderRadius: "8px",
    boxShadow: "none",
    padding: "16px",
  },
  selection: {
    backgroundColor: "rgba(218, 222, 231, .3)",
  },
  navigation: {
    padding: "8px 12px",
    border: "1px solid rgba(194, 201, 214, .4)",
    borderRadius: "16px",
    backgroundColor: "rgba(252, 252, 252, .4)",
    backgroundImage: "none",
    boxShadow: "0 4px 16px rgba(9, 11, 17, .07), 0 8px 16px -5px rgba(19, 23, 32, .07)",
    backdropFilter: "blur(24px)",
    WebkitBackdropFilter: "blur(24px)",
  },
  assistantMessage: {
    backgroundColor: "rgba(241, 244, 248, .72)",
    backgroundImage: "linear-gradient(135deg, rgba(255,255,255,.72), rgba(232,236,242,.48))",
    border: "1px solid rgba(194, 201, 214, .46)",
    boxShadow: "none",
    backdropFilter: "blur(18px) saturate(135%)",
    WebkitBackdropFilter: "blur(18px) saturate(135%)",
  },
} as const;

export const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: "hsl(210, 100%, 45%)", light: "hsl(210, 100%, 70%)", dark: "hsl(210, 100%, 38%)", contrastText: "#fff" },
    secondary: { main: "hsl(210, 14%, 87%)", light: "hsl(210, 14%, 90%)", dark: "hsl(210, 14%, 61%)", contrastText: "hsl(210, 14%, 22%)" },
    background: { default: "#FFFFFF", paper: "#FFFFFF" },
    text: { primary: "hsl(215, 15%, 12%)", secondary: "hsl(215, 15%, 22%)" },
    divider: "hsl(215, 15%, 92%)",
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: 'Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
    h1: { fontSize: "clamp(1.75rem, 3vw, 2.5rem)", lineHeight: 1.2, fontWeight: 700, letterSpacing: "-0.02em" },
    h2: { fontSize: "1.35rem", lineHeight: 1.35, fontWeight: 700, letterSpacing: "-0.01em" },
    h3: { fontSize: "1.05rem", lineHeight: 1.4, fontWeight: 650 },
    button: { textTransform: "none", fontWeight: 500 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          minHeight: "100vh",
          scrollbarColor: "hsl(215, 15%, 75%) transparent",
          backgroundColor: "#FFFFFF",
          backgroundImage: "radial-gradient(ellipse 80% 50% at 50% -20%, hsl(210 100% 90%), transparent)",
          backgroundAttachment: "fixed",
        },
        "::selection": { backgroundColor: "hsl(210, 100%, 90%)", color: "hsl(210, 100%, 30%)" },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { borderRadius: 8 } },
    },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: {
          ...templateSurfaces.card,
          transition: "background-color .2s ease, border-color .2s ease",
          "&:has(.MuiCardActionArea-root):hover": {
            backgroundColor: "hsl(210, 100%, 98%)",
            borderColor: "rgba(117, 127, 145, .45)",
          },
        },
      },
    },
    MuiCardContent: { styleOverrides: { root: { padding: 0, "&:last-child": { paddingBottom: 0 } } } },
    MuiCardActions: { styleOverrides: { root: { padding: "8px 0 0" } } },
    MuiCardActionArea: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          transition: "none",
          "&:hover": { backgroundColor: "transparent" },
        },
        focusHighlight: { display: "none" },
      },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 500 } } },
    MuiDialog: { styleOverrides: { paper: { backgroundImage: "none", border: "1px solid", borderColor: "hsl(215, 15%, 92%)" } } },
    MuiDrawer: { styleOverrides: { paper: { backgroundColor: "#FFFFFF", backgroundImage: "none" } } },
    MuiOutlinedInput: { styleOverrides: { root: { backgroundColor: "rgba(255, 255, 255, .8)" } } },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          "&.Mui-selected": { backgroundColor: templateSurfaces.selection.backgroundColor },
          "&.Mui-selected:hover": { backgroundColor: "rgba(218, 222, 231, .45)" },
        },
      },
    },
  },
});
