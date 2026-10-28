import { Box } from "@mui/material";
import { splitTextMatches } from "./textMatches";

export function HighlightText({ text, terms = [] }: { text: string; terms?: readonly string[] }) {
  return <>{splitTextMatches(text, terms).map((part, index) => part.matched
    ? <Box component="mark" key={index} sx={{ bgcolor: "warning.main", color: "warning.contrastText", borderRadius: .5, px: .125 }}>{part.text}</Box>
    : part.text)}</>;
}
