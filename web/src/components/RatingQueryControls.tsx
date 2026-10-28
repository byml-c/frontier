import { MenuItem, TextField } from "@mui/material";
import { RATING_SOURCES, ratingOptions, type PaperOrder, type RatingQuery, type RatingSource } from "./paperQuery";

export function RatingQueryControls({ value, onChange, defaultSortLabel = "默认顺序" }: {
  value: RatingQuery; onChange: (value: RatingQuery) => void; defaultSortLabel?: string;
}) {
  return <>
    <TextField select size="small" label="评分来源" value={value.ratingSource} onChange={(event) => onChange({ ...value, ratingSource: event.target.value as RatingSource })} sx={{ minWidth: 160, flex: 1 }}>
      {RATING_SOURCES.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
    </TextField>
    <TextField select size="small" label="评分级别" value={value.ratingLevel} onChange={(event) => onChange({ ...value, ratingLevel: event.target.value })} sx={{ minWidth: 150, flex: 1 }}>
      <MenuItem value="">全部级别</MenuItem>
      <MenuItem value="-1">未评分</MenuItem>
      {ratingOptions(value.ratingSource).map((option, index) => <MenuItem key={option.value} value={String(index)}>{option.label}</MenuItem>)}
    </TextField>
    <TextField select size="small" label="排序" value={value.sort} onChange={(event) => onChange({ ...value, sort: event.target.value as PaperOrder })} sx={{ minWidth: 170, flex: 1 }}>
      <MenuItem value="default">{defaultSortLabel}</MenuItem>
      <MenuItem value="rating_desc">评分从高到低</MenuItem>
      <MenuItem value="rating_asc">评分从低到高</MenuItem>
      <MenuItem value="published_desc">发表时间从新到旧</MenuItem>
      <MenuItem value="published_asc">发表时间从旧到新</MenuItem>
      <MenuItem value="title">标题顺序</MenuItem>
    </TextField>
  </>;
}
