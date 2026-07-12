/** Per-platform verification result. `confidence` is normalized to 0..1. */
export type PlatformResult = {
  link: string;
  status: string;
  confidence: number;
};

export type PlatformKey = "instagram" | "x" | "facebook" | "youtube" | "tiktok";

export type ResultRow = {
  name: string;
  wikipediaUrl: string;
  platforms: Record<PlatformKey, PlatformResult>;
  /** Overall confidence across resolved platforms, normalized to 0..1. */
  confidence: number;
};
