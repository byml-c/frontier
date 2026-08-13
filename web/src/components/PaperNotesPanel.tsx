import CheckRounded from "@mui/icons-material/CheckRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import EditRounded from "@mui/icons-material/EditRounded";
import NoteAltRounded from "@mui/icons-material/NoteAltRounded";
import { Alert, Box, Button, Chip, Divider, IconButton, Paper, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { useEffect, useState, type KeyboardEvent } from "react";
import type { Annotation } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";
import { noteEditorKeyboardAction } from "./noteEditorKeyboard";

const HIGHLIGHT_COLORS = ["#F3D76B", "#A8D8A8", "#9CCCF4", "#F3B1C8", "#D4B5F5"];
const DEFAULT_HIGHLIGHT_COLOR = "#F3D76B";

type PaperNotesPanelProps = {
  notes: Annotation[];
  editingId: string | null;
  saving: boolean;
  saveError?: string;
  onJump: (page: number) => void;
  onEdit: (note: Annotation) => void;
  onCancelEdit: () => void;
  onSave: (note: Annotation, content: string, color: string) => void;
  onDelete: (note: Annotation) => void;
};

export function PaperNotesPanel({ notes, editingId, saving, saveError, onJump, onEdit, onCancelEdit, onSave, onDelete }: PaperNotesPanelProps) {
  return (
    <Box sx={{ p: { xs: 1, sm: 2 }, overflowY: "auto" }}>
      {!notes.length ? (
        <Stack alignItems="center" spacing={1} sx={{ py: 8, color: "text.secondary" }}>
          <NoteAltRounded />
          <Typography>还没有笔记</Typography>
          <Typography variant="body2">选中原文后可以高亮或记录想法。</Typography>
        </Stack>
      ) : (
        <Stack spacing={1.5}>
          {notes.map((note) => (
            <PaperNoteCard
              key={note.id}
              note={note}
              editing={editingId === note.id}
              saving={saving && editingId === note.id}
              saveError={editingId === note.id ? saveError : undefined}
              onJump={onJump}
              onEdit={onEdit}
              onCancelEdit={onCancelEdit}
              onSave={onSave}
              onDelete={onDelete}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}

function PaperNoteCard({ note, editing, saving, saveError, onJump, onEdit, onCancelEdit, onSave, onDelete }: {
  note: Annotation;
  editing: boolean;
  saving: boolean;
  saveError?: string;
  onJump: (page: number) => void;
  onEdit: (note: Annotation) => void;
  onCancelEdit: () => void;
  onSave: (note: Annotation, content: string, color: string) => void;
  onDelete: (note: Annotation) => void;
}) {
  const [content, setContent] = useState(note.content || "");
  const [color, setColor] = useState(note.color || DEFAULT_HIGHLIGHT_COLOR);

  useEffect(() => {
    if (!editing) return;
    setContent(note.content || "");
    setColor(note.color || DEFAULT_HIGHLIGHT_COLOR);
  }, [editing, note.color, note.content]);

  function handleEditorKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const action = noteEditorKeyboardAction({
      key: event.key,
      metaKey: event.metaKey,
      ctrlKey: event.ctrlKey,
      isComposing: event.nativeEvent.isComposing,
    });
    if (!action || saving) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "cancel") onCancelEdit();
    else onSave(note, content, color);
  }

  return (
    <Paper
      variant="outlined"
      sx={{
        p: { xs: 1.25, sm: 1.75 },
        cursor: editing ? "default" : "pointer",
        borderLeft: `4px solid ${editing ? color : note.color || DEFAULT_HIGHLIGHT_COLOR}`,
      }}
      onClick={editing ? undefined : (event) => {
        if ((event.target as Element).closest("a, button, input, textarea")) return;
        onJump(note.page_number);
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Chip size="small" label={`第 ${note.page_number} 页`} />
        <Stack direction="row">
          {!editing && (
            <Tooltip title="编辑笔记和高亮颜色">
              <IconButton size="small" aria-label="编辑笔记" onClick={(event) => { event.stopPropagation(); onEdit(note); }}>
                <EditRounded />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="删除笔记和高亮">
            <IconButton size="small" color="error" aria-label="删除笔记和高亮" onClick={(event) => { event.stopPropagation(); onDelete(note); }}>
              <DeleteOutlineRounded />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>
      {note.selected_text && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
          {note.selected_text}
        </Typography>
      )}
      <Divider sx={{ my: 1 }} />
      {editing ? (
        <Box onClick={(event) => event.stopPropagation()}>
          <Stack direction="row" spacing={0.75} sx={{ mb: 1 }}>
            {HIGHLIGHT_COLORS.map((value) => (
              <Tooltip title={value} key={value}>
                <IconButton
                  aria-label={`高亮颜色 ${value}`}
                  onClick={() => setColor(value)}
                  sx={{ width: 30, height: 30, bgcolor: value, border: color === value ? 2 : 1, borderColor: color === value ? "primary.main" : "divider", "&:hover": { bgcolor: value } }}
                >
                  {color === value && <CheckRounded fontSize="small" />}
                </IconButton>
              </Tooltip>
            ))}
          </Stack>
          <TextField
            autoFocus
            fullWidth
            multiline
            minRows={5}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            onKeyDown={handleEditorKeyDown}
            placeholder="使用 Markdown 记录想法，也可以保持空白"
            helperText="Ctrl/⌘ + Enter 保存，Esc 取消"
          />
          {saveError && <Alert severity="error" sx={{ mt: 1 }}>{saveError}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 1 }}>
            <Button onClick={onCancelEdit} disabled={saving}>取消</Button>
            <Button variant="contained" onClick={() => onSave(note, content, color)} disabled={saving}>保存</Button>
          </Stack>
        </Box>
      ) : note.content ? (
        <MarkdownMessage content={note.content} />
      ) : (
        <Typography color="text.secondary" fontStyle="italic">未添加笔记</Typography>
      )}
    </Paper>
  );
}
