import SendRounded from "@mui/icons-material/SendRounded";
import StopRounded from "@mui/icons-material/StopRounded";
import { Box, CircularProgress, IconButton, InputBase, Tooltip } from "@mui/material";
import type { KeyboardEventHandler } from "react";
import type { AgentRuntimeState, Conversation } from "../types";
import { AgentRuntimeStatus } from "./AgentRuntimeStatus";

type Props = {
  value: string;
  onChange: (value: string) => void;
  onKeyDown?: KeyboardEventHandler<HTMLTextAreaElement | HTMLInputElement>;
  placeholder: string;
  active: boolean;
  cancelling: boolean;
  stopDisabled: boolean;
  onStop: () => void;
  conversation?: Conversation;
  liveState?: AgentRuntimeState;
};

/** Shared two-level composer: transparent editor above, runtime and action below. */
export function AgentComposer({ value, onChange, onKeyDown, placeholder, active, cancelling, stopDisabled, onStop, conversation, liveState }: Props) {
  return (
    <Box
      sx={{
        overflow: "hidden",
        border: 1,
        borderColor: "divider",
        borderRadius: 2.5,
        bgcolor: "background.paper",
        px: "15px",
        py: "10px",
        transition: "border-color .16s ease, box-shadow .16s ease",
        "&:focus-within": {
          borderColor: "primary.main",
          boxShadow: "0 0 0 1px rgba(25, 118, 210, 0.14)",
        },
      }}
    >
      <InputBase
        fullWidth
        multiline
        minRows={2}
        maxRows={6}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        inputProps={{ "aria-label": placeholder }}
        sx={{
          display: "block",
          p: 0,
          bgcolor: "transparent",
          "& textarea": { lineHeight: 1.5 },
        }}
      />
      <Box sx={{ height: 30, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1 }}>
        <AgentRuntimeStatus conversation={conversation} liveState={liveState} />
        {active ? (
          <Tooltip title="停止 Agent">
            <span>
              <IconButton
                type="button"
                color="error"
                aria-label="停止 Agent"
                onClick={onStop}
                disabled={stopDisabled}
                sx={{
                  width: 30,
                  height: 30,
                  flex: "0 0 auto",
                  p: 0,
                  display: "grid",
                  placeItems: "center",
                  border: 2,
                  borderColor: "error.main",
                  bgcolor: "background.paper",
                  "& .MuiSvgIcon-root": { display: "block" },
                  "&:hover": { bgcolor: "rgba(211, 47, 47, 0.06)" },
                }}
              >
                {cancelling ? <CircularProgress size={16} /> : <StopRounded sx={{ fontSize: 18 }} />}
              </IconButton>
            </span>
          </Tooltip>
        ) : (
          <Tooltip title="发送">
            <span>
              <IconButton
                type="submit"
                color="primary"
                aria-label="发送"
                disabled={!value.trim()}
                sx={{
                  width: 30,
                  height: 30,
                  flex: "0 0 auto",
                  p: 0,
                  display: "grid",
                  placeItems: "center",
                  border: 2,
                  borderColor: "primary.main",
                  bgcolor: "background.paper",
                  "& .MuiSvgIcon-root": { display: "block" },
                  "&:hover": { bgcolor: "rgba(25, 118, 210, 0.06)" },
                  "&.Mui-disabled": { borderColor: "action.disabled", bgcolor: "background.paper" },
                }}
              >
                <SendRounded sx={{ fontSize: 18 }} />
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
}
