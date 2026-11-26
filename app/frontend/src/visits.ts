import { Job } from "./api";

export type VisitDef = { id: string; title: string; steps: string[]; machine?: string };

/** Display order = how the factory actually runs. Pipeline job `ord` is not this list. */
export const VISITS: VisitDef[] = [
  { id: "fetch", title: "Get the files", steps: ["fetch"] },
  { id: "ingest", title: "Check files", steps: ["ingest"] },
  { id: "lip", title: "Lip scan", steps: ["lip_gpu", "lip_mesh", "lip_merge"], machine: "8 CPUs, from ingest" },
  { id: "match", title: "Whisper → matching", steps: ["mksrt_dub", "mksrt_src", "words_dub", "words_src", "sentences_src", "analyze", "merged", "proxy", "sharpness", "translate"], machine: "one CPU" },
  { id: "decisions", title: "Check matching", steps: ["decisions"] },
  { id: "cut", title: "Sheet → library", steps: ["sheet", "manifest", "lipsync", "groups", "library", "captions"], machine: "one CPU, after matching" },
  { id: "facemap", title: "Face map", steps: ["facemap"], machine: "GPU, after library" },
  { id: "scene", title: "Scene edit → fast trim", steps: ["scene_edit", "use_scene", "fast_trim"], machine: "one CPU, after face map" },
  { id: "review", title: "Ready for review", steps: ["review"] },
  { id: "tail", title: "Render → check", steps: ["dynsrt", "render", "verify", "bookends", "trans_plan", "transitions", "subs_dub", "subs_en", "scene_sheet", "qc", "check"], machine: "one CPU, after review" },
  { id: "publish", title: "Publish", steps: ["publish"] },
  { id: "cleanup", title: "Cleanup", steps: ["cleanup"] },
  { id: "notes", title: "Run notes", steps: ["notes"] },
];

const BY_STEP: Record<string, VisitDef> = {};
for (const v of VISITS) for (const s of v.steps) BY_STEP[s] = v;

export type VisitGroup = {
  def: VisitDef;
  jobs: Job[];
  status: string;
  live: Job | null;
  done: number;
};

function rank(status: string) {
  return ({ running: 0, needs_input: 1, failed: 2, waiting: 3, blocked: 4, cancelled: 5, done: 6, skipped: 7 } as Record<string, number>)[status] ?? 9;
}

export function groupJobs(jobs: Job[]): { groups: VisitGroup[]; rest: Job[] } {
  const byStep = new Map<string, Job>();
  for (const j of jobs) {
    const prev = byStep.get(j.step);
    if (!prev) { byStep.set(j.step, j); continue; }
    if (["done", "skipped"].includes(prev.status)) continue;
    if (["done", "skipped"].includes(j.status)) byStep.set(j.step, j);
  }
  const groups: VisitGroup[] = [];
  const used = new Set<string>();
  for (const def of VISITS) {
    const members = def.steps.map((s) => byStep.get(s)).filter((j): j is Job => !!j);
    if (!members.length) continue;
    members.forEach((j) => used.add(j.step));
    const live = [...members].sort((a, b) => rank(a.status) - rank(b.status))[0];
    let status = "waiting";
    if (members.some((j) => j.status === "running")) status = "running";
    else if (members.some((j) => j.status === "needs_input")) status = "needs_input";
    else if (members.some((j) => j.status === "failed")) status = "failed";
    else if (members.every((j) => ["done", "skipped"].includes(j.status))) status = "done";
    else if (members.some((j) => j.status === "blocked")) status = "blocked";
    else if (members.every((j) => j.status === "cancelled")) status = "cancelled";
    else if (members.some((j) => j.status === "waiting")) status = "waiting";
    else status = live.status;
    groups.push({
      def,
      jobs: members,
      status,
      live: ["running", "needs_input", "failed"].includes(status) ? members.find((j) => j.status === status) || live : live,
      done: members.filter((j) => ["done", "skipped"].includes(j.status)).length,
    });
  }
  const rest = jobs.filter((j) => !used.has(j.step)).sort((a, b) => a.ord - b.ord);
  return { groups, rest };
}

export function liveLabel(jobs: Job[]) {
  const { groups } = groupJobs(jobs);
  const g = groups.find((x) => ["running", "needs_input", "failed"].includes(x.status))
    || groups.find((x) => x.status === "waiting");
  if (!g) return "";
  const inner = g.live && g.jobs.length > 1 ? g.live.label : "";
  const msg = g.live?.message || "";
  const head = inner && inner !== g.def.title ? `${g.def.title} · ${inner}` : g.def.title;
  return msg ? `${head} — ${msg}` : head;
}
