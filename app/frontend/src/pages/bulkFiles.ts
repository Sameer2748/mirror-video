// Files the bulk form already matched, handed to the status page so uploads start there.

export const VIDEO_EXTS = ["mp4", "mov", "mkv", "m4v"];
export const AUDIO_EXTS = ["mp3", "wav", "m4a", "aac"];
export const SCRIPT_EXTS = ["docx", "doc", "txt"];

const NOT_EPISODE = new Set([360, 480, 720, 1080, 1440, 2160, 1920, 1280, 3840, 4096]);

/** Episode number in a file name. `11.mp4` still works; so does `TAB_FR_S01E11.mp4`.
 *  Pass the typed range as `allowed` so `S01E11` is 11, not season 1. */
function nameStem(name: string): string {
  let base = (name.split("/").pop() || name).trim();
  if (base.includes(".")) base = base.replace(/\.[^.]+$/, "");
  return base.replace(/[\s._-]+/g, " ").trim().toLowerCase();
}

export function episodeFromName(name: string, allowed?: Iterable<number>): number | null {
  const stem = nameStem(name);
  const hits: { n: number; kind: "season_ep" | "marked" | "bare" }[] = [];
  for (const m of stem.matchAll(/s\d{1,2}e0*([1-9]\d{0,2})(?!\d)/gi)) hits.push({ n: Number(m[1]), kind: "season_ep" });
  for (const m of stem.matchAll(/(?:^|[^a-z0-9])(?:ep(?:isode)?|épisode|puntata|folge|eo|e|#)[\s._-]*0*([1-9]\d{0,2})(?!\d)/gi)) {
    hits.push({ n: Number(m[1]), kind: "marked" });
  }
  const bareSrc = stem.replace(/s\d{1,2}e0*[1-9]\d{0,2}(?!\d)/gi, " ");
  for (const m of bareSrc.matchAll(/(?:^|\D)0*([1-9]\d{0,2})(?!\d)/g)) {
    const n = Number(m[1]);
    if (NOT_EPISODE.has(n) || (n >= 1900 && n <= 2099)) continue;
    hits.push({ n, kind: "bare" });
  }
  const want = allowed ? new Set([...allowed].map(Number)) : null;
  const keep = want ? hits.filter((h) => want.has(h.n)) : hits;
  if (!keep.length) return null;
  for (const kind of ["season_ep", "marked", "bare"] as const) {
    const ns = [...new Set(keep.filter((h) => h.kind === kind).map((h) => h.n))];
    if (ns.length === 1) return ns[0];
    if (ns.length > 1) return null;
  }
  const ns = [...new Set(keep.map((h) => h.n))];
  return ns.length === 1 ? ns[0] : null;
}

export function matchFolder(files: File[], exts: string[], nums?: Iterable<number>): Map<number, File> {
  const rank = new Map(exts.map((e, i) => [e, i]));
  const list = nums ? [...nums] : [];
  const allowed = list.length ? list : undefined;
  const byNum = new Map<number, File>();
  const exact = new Set<number>();
  for (const f of files) {
    const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || "";
    const name = (rel.split("/").pop() || f.name.split("/").pop() || f.name).trim();
    const ext = (name.split(".").pop() || "").trim().toLowerCase();
    if (!rank.has(ext)) continue;
    const n = episodeFromName(name, allowed);
    if (n == null) continue;
    const stem = name.replace(/\.[^.]+$/, "").toLowerCase();
    const isExact = [String(n), String(n).padStart(2, "0"), String(n).padStart(3, "0"), `ep${n}`, `ep${String(n).padStart(2, "0")}`].includes(stem);
    const prev = byNum.get(n);
    if (!prev) { byNum.set(n, f); if (isExact) exact.add(n); continue; }
    if (exact.has(n) && !isExact) continue;
    if (isExact && !exact.has(n)) { byNum.set(n, f); exact.add(n); continue; }
    const prevExt = (prev.name.split(".").pop() || "").toLowerCase();
    if ((rank.get(ext) ?? 99) < (rank.get(prevExt) ?? 99)) byNum.set(n, f);
  }
  return byNum;
}

/** Only the episode numbers the user typed (e.g. 32–33). Extra 31.mp4 / 34.mp4 stay on disk. */
export function keepRange<T>(map: Map<number, T>, nums: Iterable<number>): Map<number, T> {
  const want = new Set(nums);
  return new Map([...map].filter(([n]) => want.has(n)));
}

export function folderUse(files: File[], exts: string[], nums: number[]): { used: number; skipped: number } {
  if (!files.length || !nums.length) return { used: 0, skipped: files.length };
  const inRange = keepRange(matchFolder(files, exts, nums), nums);
  return { used: inRange.size, skipped: Math.max(0, files.length - inRange.size) };
}

export function folderLabel(files: File[]): string {
  const rel = (files[0] as File & { webkitRelativePath?: string }).webkitRelativePath || files[0]?.name || "";
  return rel.split("/")[0] || `${files.length} files`;
}

export type BulkFileMaps = {
  video: Map<number, File>;
  audio: Map<number, File>;
  script: Map<number, File>;
};

const pending = new Map<number, BulkFileMaps>();
export function stashBulkFiles(bid: number, files: BulkFileMaps) { pending.set(bid, files); }
export function takeBulkFiles(bid: number): BulkFileMaps | undefined {
  const x = pending.get(bid); pending.delete(bid); return x;
}
