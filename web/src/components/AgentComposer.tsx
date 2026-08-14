import SendRounded from "@mui/icons-material/SendRounded";
import StopRounded from "@mui/icons-material/StopRounded";
import { Box, CircularProgress, IconButton, InputBase, Tooltip } from "@mui/material";
import { forwardRef, memo, useCallback, useImperativeHandle, useRef, useState, type KeyboardEventHandler } from "react";
import type { AgentRuntimeState, Conversation } from "../types";
import { AgentRuntimeStatus } from "./AgentRuntimeStatus";

type Props = {
  onKeyDown?: KeyboardEventHandler<HTMLTextAreaElement | HTMLInputElement>;
  placeholder: string;
  active: boolean;
  cancelling: boolean;
  stopDisabled: boolean;
  sendDisabled?: boolean;
  onStop: () => void;
  conversation?: Conversation;
  liveState?: AgentRuntimeState;
};

export type AgentComposerHandle = {
  getValue: () => string;
  setValue: (value: string) => void;
  clear: () => void;
};

/**
 * Shared two-level composer. Draft state deliberately lives here so typing does
 * not re-render the conversation history (especially Markdown and KaTeX).
 */
const AgentComposerImpl = forwardRef<AgentComposerHandle, Props>(function AgentComposer({ onKeyDown, placeholder, active, cancelling, stopDisabled, sendDisabled = false, onStop, conversation, liveState }, ref) {
  const [value, setValue] = useState("");
  const valueRef = useRef("");
  const updateValue = useCallback((nextValue: string) => {
    valueRef.current = nextValue;
    setValue(nextValue);
  }, []);

  useImperativeHandle(ref, () => ({
    getValue: () => valueRef.current,
    setValue: updateValue,
    clear: () => updateValue(""),
  }), [updateValue]);

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
        onChange={(event) => updateValue(event.target.value)}
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
                disabled={sendDisabled || !value.trim()}
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
});

export const AgentComposer = memo(AgentComposerImpl);
