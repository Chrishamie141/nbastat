import Image from "next/image";
import { StatusPill } from "./SocialStatus";

const fmt = (value) => value ? new Date(value).toLocaleString() : "Not scheduled";

export default function SocialQueue({ items, busy, onPreview, onCaption, onMedia, onPublish, onReschedule, onCancel }) {
  if (!items.length) return <p className="rounded-2xl bg-white/[.04] p-5 text-sm text-slate-400">No posts are waiting. Skipped opportunities remain available below for an explanation.</p>;
  return <div className="grid gap-4 lg:grid-cols-2">
    {items.map((item) => <article key={item.opportunity_id} className="overflow-hidden rounded-2xl border border-white/10 bg-white/[.035]">
      {item.previewUrl ? <div className="relative aspect-video bg-slate-950"><Image src={item.previewUrl} alt={`${item.content_type} preview`} fill sizes="(max-width: 1024px) 100vw, 50vw" unoptimized className="object-cover" /></div> : <div className="flex aspect-[3/1] items-center justify-center bg-gradient-to-br from-slate-950 to-emerald-950/40 text-xs font-black uppercase tracking-[.22em] text-slate-500">Media {item.media_status || "not generated"}</div>}
      <div className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-black">{item.matchup}</p><StatusPill status={item.status} /></div>
        <p className="mt-1 text-xs font-black uppercase tracking-wider text-cyan-300">{item.content_type?.replaceAll("_", " ")} / Score {item.score}</p>
        <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">{item.content || "Caption will be created from verified evidence when this item is processed."}</p>
        <p className="mt-3 text-xs text-slate-500">{fmt(item.scheduled_at)}</p>
        <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer font-bold">Why this score</summary><ul className="mt-2 list-disc space-y-1 pl-4">{item.score_reasons?.map((reason) => <li key={reason}>{reason}</li>)}</ul></details>
        <div className="mt-4 flex flex-wrap gap-2">
          {item.post_id && <button onClick={() => onPreview(item.post_id)} className="btn btn-glass px-3 py-2 text-xs">Preview</button>}
          {item.post_id && <button onClick={() => onCaption(item.post_id)} disabled={busy} className="btn btn-glass px-3 py-2 text-xs">Regenerate caption</button>}
          {item.post_id && <button onClick={() => onMedia(item.post_id, "image")} disabled={busy} className="btn btn-glass px-3 py-2 text-xs">Regenerate image</button>}
          {item.post_id && <button onClick={() => onMedia(item.post_id, "video")} disabled={busy} className="btn btn-glass px-3 py-2 text-xs">Regenerate video</button>}
          {item.post_id && <button onClick={() => onPublish(item.post_id)} disabled={busy} className="btn btn-primary px-3 py-2 text-xs">Post Now</button>}
          <button onClick={() => onReschedule(item.opportunity_id)} disabled={busy} className="btn btn-glass px-3 py-2 text-xs">Reschedule +1h</button>
          <button onClick={() => onCancel(item.opportunity_id)} disabled={busy} className="rounded-xl border border-rose-400/30 px-3 py-2 text-xs font-bold text-rose-200">Cancel / Skip</button>
        </div>
      </div>
    </article>)}
  </div>;
}
