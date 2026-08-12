import { Tab, Tabs } from "@mui/material";
import { Link } from "react-router-dom";

export function BriefTabs({ value }: { value: "today" | "archive" }) {
  return (
    <Tabs value={value} aria-label="简报分类" sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, py: 0 } }}>
      <Tab component={Link} to="/" value="today" label="今日简报" />
      <Tab component={Link} to="/briefs" value="archive" label="往期简报" />
    </Tabs>
  );
}
