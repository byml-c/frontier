import { Autocomplete, Chip, Stack, TextField, createFilterOptions } from "@mui/material";
import { useState } from "react";
import type { Tag } from "../types";

type TagOption = Tag & { inputValue?: string };
const filterTagOptions = createFilterOptions<TagOption>({
  stringify: (option) => option.name,
  trim: true,
});

export function PaperTagSelector({
  tags,
  selectedIds,
  newTags,
  onSelectedIdsChange,
  onNewTagsChange,
  onEdit,
}: {
  tags: Tag[];
  selectedIds: string[];
  newTags: string[];
  onSelectedIdsChange: (ids: string[]) => void;
  onNewTagsChange: (names: string[]) => void;
  onEdit?: () => void;
}) {
  const [inputValue, setInputValue] = useState("");
  const changeSelected = (ids: string[]) => {
    onEdit?.();
    onSelectedIdsChange(ids);
  };
  const changeNew = (names: string[]) => {
    onEdit?.();
    onNewTagsChange(names);
  };

  return (
    <>
      {(selectedIds.length > 0 || newTags.length > 0) && (
        <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1.5 }}>
          {tags.filter((tag) => selectedIds.includes(tag.id)).map((tag) => (
            <Chip
              key={tag.id}
              label={tag.name}
              color="primary"
              onDelete={() => changeSelected(selectedIds.filter((id) => id !== tag.id))}
            />
          ))}
          {newTags.map((name) => (
            <Chip
              key={name}
              label={name}
              color="primary"
              onDelete={() => changeNew(newTags.filter((tag) => tag !== name))}
            />
          ))}
        </Stack>
      )}
      <Autocomplete<TagOption, false, false, false>
        sx={{ mt: 1.5, maxWidth: 420 }}
        value={null}
        inputValue={inputValue}
        options={tags.filter(
          (tag) =>
            !selectedIds.includes(tag.id)
            && !newTags.some((name) => name.toLowerCase() === tag.name.toLowerCase()),
        )}
        filterOptions={(options, params) => {
          const filtered = filterTagOptions(options, params);
          const input = params.inputValue.trim();
          const hasExistingMatch = tags.some((tag) =>
            tag.name.toLowerCase().includes(input.toLowerCase()),
          );
          const alreadyNew = newTags.some((name) => name.toLowerCase() === input.toLowerCase());
          if (input && !hasExistingMatch && !alreadyNew) {
            filtered.push({ id: `new:${input}`, name: `新建标签“${input}”`, inputValue: input });
          }
          return filtered;
        }}
        getOptionLabel={(option) => option.inputValue || option.name}
        isOptionEqualToValue={(option, value) => option.id === value.id}
        noOptionsText={inputValue.trim() ? "没有匹配的已有标签" : "没有可选标签"}
        onInputChange={(_, value) => setInputValue(value)}
        onChange={(_, option) => {
          if (!option) return;
          if (option.inputValue) changeNew([...newTags, option.inputValue]);
          else changeSelected(selectedIds.includes(option.id) ? selectedIds : [...selectedIds, option.id]);
          setInputValue("");
        }}
        renderOption={(props, option) => <li {...props} key={option.id}>{option.name}</li>}
        renderInput={(params) => (
          <TextField {...params} size="small" label="搜索标签" placeholder="输入标签名称" />
        )}
        openOnFocus
      />
    </>
  );
}
