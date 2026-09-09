import Image from "next/image";
import GlowCard from "@/components/ui/GlowCard";
import { StatusPill } from "./SocialStatus";

const EMPTY = "--";
const fmt = (value) => value ? new Date(value).toLocaleString() : EMPTY;
const metric = (value) => value == null ? "Unavailable" : Number(value).toLocaleString();

export default function SocialHistory({ items = [] }) {
  return <GlowCard className="p-6">
    <p className="text-xs font-black uppercase tracking-wider text-cyan-300">X Post History</p>
    <h2 className="mt-1 text-2xl font-black">Published and attempted content</h2>
    <div className="mt-5 overflow-x-auto"><table className="w-full min-w-[1100px] text-left text-sm">
      <thead className="text-xs uppercase tracking-wider text-slate-500"><tr><th className="pb-3">Media</th><th>Type / matchup</th><th>Caption</th><th>Status</th><th>Published</th><th>Impressions</th><th>Likes / replies / reposts</th><th>Engagement</th><th>X</th></tr></thead>
      <tbody>{items.map((post) => <tr key={post.post_id} className="border-t border-white/10">
        <td className="py-3 pr-3">{post.thumbnailUrl && post.mediaIndicator === "image" ? <div className="relative h-12 w-20 overflow-hidden rounded-lg"><Image src={post.thumbnailUrl} alt="Social post thumbnail" fill sizes="80px" unoptimized className="object-cover" /></div> : <span className="text-xs uppercase text-slate-500">{post.mediaIndicator || "text"}</span>}</td>
        <td className="py-4 pr-4"><p className="font-bold">{post.post_type?.replaceAll("_", " ")}</p><p className="mt-1 text-xs text-slate-500">{post.matchup}</p></td>
        <td className="max-w-sm py-4 pr-5 text-slate-300"><span className="line-clamp-2">{post.content}</span></td>
        <td><StatusPill status={post.status} /></td><td>{fmt(post.published_at)}</td><td>{metric(post.impressions)}</td>
        <td>{metric(post.likes)} / {metric(post.replies)} / {metric(post.reposts)}</td>
        <td>{post.engagementRate == null ? "Unavailable" : `${(post.engagementRate * 100).toFixed(2)}%`}</td>
        <td>{post.xUrl ? <a href={post.xUrl} target="_blank" rel="noreferrer" className="font-bold text-cyan-300">Open</a> : EMPTY}</td>
      </tr>)}</tbody>
    </table></div>
  </GlowCard>;
}
