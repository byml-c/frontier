import { MenuItem, TextField } from "@mui/material";
import { RATING_SOURCES, type PaperOrder, type RatingQuery, type RatingSource } from "./paperQuery";

export function RatingQueryControls({ value, onChange, defaultSortLabel = "默认顺序" }: {
  value: RatingQuery; onChange: (value: RatingQuery) => void; defaultSortLabel?: string;
}) {
  return <>
    <TextField select size="small" label="评分来源" value={value.ratingSource} onChange={(event) => onChange({ ...value, ratingSource: event.target.value as RatingSource })} sx={{ minWidth: 130 }}>
      {RATING_SOURCES.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
    </TextField>
    <TextField select size="small" label="评分状态" value={value.rated} onChange={(event) => onChange({ ...value, rated: event.target.value })} sx={{ minWidth: 120 }}>
      <MenuItem value="">全部</MenuItem><MenuItem value="true">已评分</MenuItem><MenuItem value="false">未评分</MenuItem>
    </TextField>
    {(["scoreMin", "scoreMax"] as const).map((key) => <TextField key={key} size="small" type="number"
      label={key === "scoreMin" ? "最低分" : "最高分"} value={value[key]} sx={{ minWidth: 100 }}
      slotProps={{ htmlInput: { min: 0, max: 1, step: "any" } }}
      onChange={(event) => onChange({ ...value, [key]: event.target.value })} />)}
    <TextField select size="small" label="排序" value={value.sort} onChange={(event) => onChange({ ...value, sort: event.target.value as PaperOrder })} sx={{ minWidth: 160 }}>
      <MenuItem value="default">{defaultSortLabel}</MenuItem><MenuItem value="rating_desc">评分从高到低</MenuItem>
      <MenuItem value="rating_asc">评分从低到高</MenuItem><MenuItem value="published_desc">发表时间从新到旧</MenuItem>
      <MenuItem value="published_asc">发表时间从旧到新</MenuItem><MenuItem value="title">标题顺序</MenuItem>
    </TextField>
  </>;
}
