export interface CalendarEvent {
  symbol: string;
  type: "earnings" | "ex_dividend";
  date: string; // YYYY-MM-DD
  has_active_position?: boolean;
}

export interface CalendarResponse {
  events: CalendarEvent[];
  error?: string;
}

export interface CalendarRefreshResponse {
  updated?: number;
  errors?: number;
  symbols_processed?: number;
  calendar_container_available?: boolean;
  error?: string;
}
