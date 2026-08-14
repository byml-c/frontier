import { Popover } from "@mui/material";
import { DateCalendar, LocalizationProvider, PickerDay } from "@mui/x-date-pickers";
import type { PickerDayProps } from "@mui/x-date-pickers/PickerDay";
import { AdapterDayjs } from "@mui/x-date-pickers/AdapterDayjs";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";

type MarkedDayProps = PickerDayProps & {
  availableDates?: ReadonlySet<string>;
};

function MarkedDay({ availableDates, day, outsideCurrentMonth, ...props }: MarkedDayProps) {
  const marked = !outsideCurrentMonth && Boolean(availableDates?.has(day.format("YYYY-MM-DD")));
  return (
    <PickerDay
      {...props}
      day={day}
      outsideCurrentMonth={outsideCurrentMonth}
      sx={{
        position: "relative",
        ...(marked && {
          "&::after": {
            content: '""',
            position: "absolute",
            left: "50%",
            bottom: 2,
            width: 4,
            height: 4,
            borderRadius: "50%",
            bgcolor: props.selected ? "common.white" : "primary.main",
            transform: "translateX(-50%)",
          },
        }),
      }}
    />
  );
}

export function DailyCalendar({
  anchorEl,
  date,
  availableDates,
  onClose,
  onSelect,
}: {
  anchorEl: HTMLElement | null;
  date?: string;
  availableDates: string[];
  onClose: () => void;
  onSelect: (date: string) => void;
}) {
  const value = date && dayjs(date).isValid() ? dayjs(date) : dayjs();
  const markedDates = new Set(availableDates);

  return (
    <Popover
      anchorEl={anchorEl}
      open={Boolean(anchorEl)}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { mt: 1, borderRadius: 2, overflow: "hidden" } } }}
    >
      <LocalizationProvider dateAdapter={AdapterDayjs} adapterLocale="zh-cn">
        <DateCalendar
          value={value}
          onChange={(nextDate) => {
            if (nextDate?.isValid()) onSelect(nextDate.format("YYYY-MM-DD"));
          }}
          slots={{ day: MarkedDay }}
          slotProps={{ day: { availableDates: markedDates } as unknown as MarkedDayProps }}
          sx={{ width: { xs: 304, sm: 320 }, maxWidth: "calc(100vw - 24px)" }}
        />
      </LocalizationProvider>
    </Popover>
  );
}
