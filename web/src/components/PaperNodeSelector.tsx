import CloseRounded from "@mui/icons-material/CloseRounded";
import {
  Autocomplete,
  Box,
  Chip,
  IconButton,
  Stack,
  TextField,
  Typography,
  createFilterOptions,
} from "@mui/material";
import { useState } from "react";
import type {
  ConfirmedNodeLink,
  NewConfirmedNode,
  ResearchNode,
} from "../types";
import { canCreateExactName, normalizeExactName } from "./exactName";

type NodeOption = ResearchNode & { inputValue?: string };
const filterNodeOptions = createFilterOptions<NodeOption>({
  stringify: (option) => `${option.name} ${option.core_question}`,
  trim: true,
});

const normalizeNodeName = normalizeExactName;

function canCreateNode(
  input: string,
  nodes: ResearchNode[],
  newNodes: NewConfirmedNode[],
): boolean {
  return canCreateExactName(
    input,
    nodes.map((node) => node.name),
    newNodes.map((node) => node.name),
  );
}

export function PaperNodeSelector({
  nodes,
  selectedLinks,
  newNodes,
  onSelectedLinksChange,
  onNewNodesChange,
}: {
  nodes: ResearchNode[];
  selectedLinks: ConfirmedNodeLink[];
  newNodes: NewConfirmedNode[];
  onSelectedLinksChange: (value: ConfirmedNodeLink[]) => void;
  onNewNodesChange: (value: NewConfirmedNode[]) => void;
}) {
  const [inputValue, setInputValue] = useState("");
  const selectedIds = new Set(selectedLinks.map((item) => item.node_id));

  const updateNewNode = (index: number, changes: Partial<NewConfirmedNode>) => {
    onNewNodesChange(
      newNodes.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...changes } : item,
      ),
    );
  };

  return (
    <Stack spacing={1.25} sx={{ mt: 1.5 }}>
      {selectedLinks.map((link) => {
        const node = nodes.find((item) => item.id === link.node_id);
        return (
          <Box key={link.node_id} sx={{ p: 1.25, border: 1, borderColor: "divider", borderRadius: 1 }}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
              <Chip label={node?.name ?? link.node_id} color="primary" />
              <TextField
                size="small"
                required
                fullWidth
                label="论文与节点的关系"
                value={link.reason}
                onChange={(event) => onSelectedLinksChange(
                  selectedLinks.map((item) => item.node_id === link.node_id
                    ? { ...item, reason: event.target.value }
                    : item),
                )}
              />
              <IconButton
                size="small"
                aria-label={`移除研究节点 ${node?.name ?? link.node_id}`}
                onClick={() => onSelectedLinksChange(
                  selectedLinks.filter((item) => item.node_id !== link.node_id),
                )}
              >
                <CloseRounded fontSize="small" />
              </IconButton>
            </Stack>
          </Box>
        );
      })}

      {newNodes.map((node, index) => (
        <Box
          key={`${normalizeNodeName(node.name)}-${index}`}
          sx={{ position: "relative", p: 1.5, pr: 5, border: 1, borderColor: "divider", borderRadius: 1 }}
        >
          <Typography variant="subtitle2" sx={{ mb: 1 }}>新节点：{node.name}</Typography>
          <Stack spacing={1}>
            <TextField
              size="small"
              required
              label="核心研究问题"
              value={node.core_question}
              onChange={(event) => updateNewNode(index, { core_question: event.target.value })}
            />
            <TextField
              size="small"
              required
              label="论文与节点的关系"
              value={node.reason ?? ""}
              onChange={(event) => updateNewNode(index, { reason: event.target.value })}
            />
            <TextField
              size="small"
              label="领域（可选）"
              value={node.domain ?? ""}
              onChange={(event) => updateNewNode(index, { domain: event.target.value })}
            />
          </Stack>
          <IconButton
            size="small"
            aria-label={`移除新节点 ${node.name}`}
            onClick={() => onNewNodesChange(newNodes.filter((_, itemIndex) => itemIndex !== index))}
            sx={{ position: "absolute", top: 8, right: 8 }}
          >
            <CloseRounded fontSize="small" />
          </IconButton>
        </Box>
      ))}

      <Autocomplete<NodeOption, false, false, false>
        value={null}
        inputValue={inputValue}
        options={nodes.filter(
          (node) =>
            !selectedIds.has(node.id)
            && !newNodes.some(
              (item) => normalizeNodeName(item.name) === normalizeNodeName(node.name),
            ),
        )}
        filterOptions={(options, params) => {
          const filtered = filterNodeOptions(options, params);
          const input = params.inputValue.trim();
          if (canCreateNode(input, nodes, newNodes)) {
            filtered.push({
              id: `new:${input}`,
              name: `新建节点“${input}”`,
              core_question: "",
              paper_count: 0,
              inputValue: input,
            });
          }
          return filtered;
        }}
        getOptionLabel={(option) => option.inputValue || option.name}
        isOptionEqualToValue={(option, value) => option.id === value.id}
        onInputChange={(_, value) => setInputValue(value)}
        onChange={(_, option) => {
          if (!option) return;
          if (option.inputValue) {
            onNewNodesChange([
              ...newNodes,
              {
                name: option.inputValue.trim(),
                core_question: "",
                reason: "",
                is_key: false,
              },
            ]);
          } else {
            onSelectedLinksChange([
              ...selectedLinks,
              { node_id: option.id, reason: "", is_key: false },
            ]);
          }
          setInputValue("");
        }}
        renderOption={(props, option) => (
          <li {...props} key={option.id}>
            <Box>
              <Typography variant="body2">{option.name}</Typography>
              {!option.inputValue && (
                <Typography variant="caption" color="text.secondary">
                  {option.core_question}
                </Typography>
              )}
            </Box>
          </li>
        )}
        renderInput={(params) => (
          <TextField
            {...params}
            size="small"
            label="搜索或新建研究节点"
            placeholder="输入节点名称"
          />
        )}
        noOptionsText={inputValue.trim() ? "没有匹配节点" : "没有可选节点"}
        openOnFocus
      />
    </Stack>
  );
}
