// Form der Server-Daten (webapp/view.py: summary / detail)

export type Status = "running" | "endspurt" | "hits_out" | "upcoming" | "ended";

export interface Banner {
  id: number;
  title: string;
  headline?: string;
  category?: string;
  price: number;
  remaining: number;
  total: number;
  per_day?: number | null;
  image?: string;
  buy_url?: string;
  end?: string | null;
  end_ts?: number | null;
  ended_at?: number | null;
  status: Status;
  ev?: number | null;
  ev_pct?: number | null;
  ev_uncertain?: boolean;
  ev_from_site?: boolean;
  hits_open?: number | null;
  hits_total?: number | null;
  tracked_hits?: boolean;
  cost_to_hit?: number | null;
  unsure?: boolean;
  pool_value?: number | null;
  all_packs_cost?: number | null;
  min_value?: number | null;
  rank?: string | null;
  ranks?: string[];
  min_charge?: number | null;
  password?: boolean;
  ship_cards?: number | null;
  ship_value?: number | null;
  ship_counted?: number | null;
  ship_players?: number | null;
  converted?: number | null;
  converted_max_cards?: number | null;
  out_total?: number | null;
  left_value?: number | null;
  left_per_pack?: number | null;
  undecided?: number | null;
  archived?: boolean;
  store?: boolean;
  out?: { name: string; value: number; via: string }[];
  out_unsure?: number;
  conditions?: string | null;
}

export interface Hit {
  rank: number;
  tier: string;
  key: string;
  name: string;
  value: number;
  image?: string;
  state: "open" | "pulled" | "unsure" | "maybe";
  note?: string | null;
  odds?: number;
  medal_user?: string | null;
  origin?: { via: string; user?: string; name?: string | null; at?: number; shipped_at?: number; pulled_on?: string };
}

export interface Card {
  id: string;
  name: string;
  value: number;
  copies: number;
  image?: string;
  hit?: boolean;
  pulled: number;
  unsure?: string | null;
  share: number;
}

export interface TimelineEvent {
  kind: "pack" | "out";
  t: number | null;
  old?: number;
  new?: number;
  before?: boolean;
  converted?: number;
  ship_cards?: number;
  ship_value?: number;
  players?: number;
  packs?: number | null;
  explain: { icon: string; text: string }[];
}

export interface Shipment {
  t: number | null;
  cards: number;
  value: number;
  players?: number;
  kind?: string;
  explain: { icon: string; text: string }[];
}

export interface BannerDetail extends Banner {
  hits: Hit[];
  cards: Card[];
  share_above_price?: number | null;
  history: { t: number; packs: number }[];
  ev_history: { t: number; ev: number }[];
  pack_timeline: TimelineEvent[];
  shipments: Shipment[];
}

export interface Me {
  user_id: string;
  name?: string;
  admin?: boolean;
  sync?: import("./api").LockInfo;
}
