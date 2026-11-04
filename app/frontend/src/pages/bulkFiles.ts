// Files the bulk form already matched, handed to the status page so uploads start there.

export const VIDEO_EXTS = ["mp4", "mov", "mkv", "m4v"];
export const AUDIO_EXTS = ["mp3", "wav", "m4a", "aac"];
export const SCRIPT_EXTS = ["docx", "doc", "txt"];

export function episodeFromName(name: string): number | null {
  const base = name.split("/").pop() || name;
  const stem = base.replace(/\.[^.]+$/, "");
  const m = stem.match(/^(?:ep)?0*([1-9]\d*)$/i);
  return m ? Number(m[1]) : null;
}

export function matchFolder(files: File[], exts: string[]): Map<number, File> {
  const rank = new Map(exts.map((e, i) => [e, i]));
  const byNum = new Map<number, File>();
  for (const f of files) {
    const name = f.name.split("/").pop() || f.name;
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (!rank.has(ext)) continue;
    const n = episodeFromName(name);
    if (n == null) continue;
    const prev = byNum.get(n);
    if (!prev) { byNum.set(n, f); continue; }
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
  const inRange = keepRange(matchFolder(files, exts), nums);
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
