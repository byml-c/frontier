import type { SxProps, Theme } from "@mui/material/styles";

const baseReaderControlSx: SxProps<Theme> = {
  flexShrink: 0,
  border: 1,
  borderColor: "divider",
  bgcolor: "background.paper",
  color: "text.primary",
  transition: (theme) => theme.transitions.create(["background-color", "border-color", "color"], { duration: theme.transitions.duration.shortest }),
  "&:hover": {
    borderColor: "grey.400",
    bgcolor: "#F5F7FA",
    color: "text.primary",
  },
  "&.Mui-disabled": {
    borderColor: "divider",
    bgcolor: "background.paper",
    color: "action.disabled",
  },
};

export const readerControlSx: SxProps<Theme> = {
  ...baseReaderControlSx,
  width: 36,
  height: 36,
};

export const compactReaderControlSx: SxProps<Theme> = {
  ...baseReaderControlSx,
  width: 30,
  height: 30,
  p: 0.5,
};

export const floatingReaderControlSx: SxProps<Theme> = {
  ...baseReaderControlSx,
  width: 42,
  height: 42,
  borderColor: "primary.main",
  color: "primary.main",
  boxShadow: 3,
  "&:hover": {
    borderColor: "primary.main",
    bgcolor: "#E3F2FD",
    color: "primary.main",
  },
};

export const readerSearchFieldSx: SxProps<Theme> = {
  bgcolor: "background.paper",
  "& .MuiOutlinedInput-root": {
    height: 36,
    bgcolor: "background.paper",
    "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
    "&:hover .MuiOutlinedInput-notchedOutline": { borderColor: "grey.400" },
    "&.Mui-focused .MuiOutlinedInput-notchedOutline": { borderColor: "primary.main" },
  },
};

export const readerSelectSx: SxProps<Theme> = {
  height: 36,
  bgcolor: "background.paper",
  "& .MuiOutlinedInput-notchedOutline": { borderColor: "divider" },
  "&:hover .MuiOutlinedInput-notchedOutline": { borderColor: "grey.400" },
  "&.Mui-focused .MuiOutlinedInput-notchedOutline": { borderColor: "primary.main" },
};
