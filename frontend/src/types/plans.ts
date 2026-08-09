export interface PlanAgentNote {
  note?: string;
  timestamp?: string;
  alert_level?: string;
  conditions_met?: boolean;
  recommended_status_change?: string;
}

export interface Plan {
  id: string;
  symbol: string;
  title: string;
  plan_type: string;
  priority: string;
  status: string;
  objective?: string;
  conditions?: string;
  agent_notes?: PlanAgentNote[];
  created_at?: string;
  updated_at?: string;
}

export const PLAN_TYPES = [
  { value: "sell_put", label: "Sell Put (CSP)" },
  { value: "sell_call", label: "Sell Call (CC)" },
  { value: "buy_shares", label: "Buy Shares" },
  { value: "sell_shares", label: "Sell Shares" },
  { value: "roll", label: "Roll Position" },
  { value: "close", label: "Close Position" },
  { value: "other", label: "Other" },
] as const;

export const PLAN_PRIORITIES = [
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "low", label: "Low" },
] as const;
