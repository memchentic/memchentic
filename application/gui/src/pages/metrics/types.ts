export type MetricRange = "7d" | "30d" | "90d" | "all" | "custom";

export type MemoryMetrics = {
  range: MetricRange;
  window: {
    from: string | null;
    to: string | null;
    range: MetricRange;
  };
  generated_at: string;
  totals: {
    memories: number;
    entries: number;
    guides: number;
    expiring_soon: number;
    spaces: number;
    created_in_window: number;
    accesses_in_window: number;
    unique_memories_accessed: number;
    never_accessed: number;
  };
  created_per_day: { day: string; entries: number; guides: number }[];
  accessed_per_day: { day: string; count: number }[];
  by_kind: { kind: string; count: number }[];
  by_type: { type: string; count: number }[];
  by_space: { space_id: string | null; name: string; count: number }[];
  by_project: { project_id: string; name: string; count: number }[];
  top_tags: { tag: string; count: number }[];
  top_creators: {
    user_id: string;
    email: string | null;
    display_name: string | null;
    count: number;
  }[];
  top_accessed: {
    memory_id: string;
    title: string;
    kind: string;
    type: string | null;
    access_count: number;
    space_name: string;
  }[];
  accesses_by_source: { source: string; count: number }[];
};

export type MetricsFilters = {
  range: "7d" | "30d" | "90d" | "all";
  from: string;
  to: string;
  custom: boolean;
  spaceScope: "" | "personal" | "group";
  kind: "" | "entry" | "guide";
  types: string[];
  tags: string[];
  q: string;
  queryDraft: string;
};
