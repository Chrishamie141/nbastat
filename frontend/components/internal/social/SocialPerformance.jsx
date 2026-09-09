import { BarChart3 } from "lucide-react";
import GlowCard from "@/components/ui/GlowCard";

const unavailable = "Not available from current X API access";
const metric = (value) => value == null ? unavailable : Number(value).toLocaleString();
const rate = (value) => value == null ? unavailable : `${(Number(value) * 100).toFixed(2)}%`;

function Breakdown({ title, rows = [] }) {
  return <div>
    <p className="text-xs font-black uppercase tracking-wider text-slate-500">{title}</p>
    <div className="mt-2 space-y-2">
      {rows.length ? rows.map((row) => <div key={row.name} className="rounded-xl bg-white/[.04] p-3 text-sm">
        <div className="flex items-center justify-between gap-3"><span className="font-bold">{row.name.replaceAll("_", " ")}</span><span className="text-xs text-slate-500">n={row.sampleSize} · {row.sampleStatus}</span></div>
        <p className="mt-1 text-xs text-slate-400">Avg impressions: {metric(row.averageImpressions)} · Engagement: {rate(row.engagementRate)}</p>
      </div>) : <p className="text-sm text-slate-500">No real X metric samples yet.</p>}
    </div>
  </div>;
}

export default function SocialPerformance({ analytics = {}, busy, onRefresh }) {
  const totals = analytics.totals || {};
  return <GlowCard className="p-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><BarChart3 className="text-cyan-300" /><div><p className="text-xs font-black uppercase tracking-wider text-cyan-300">Real X analytics</p><h2 className="text-2xl font-black">Performance</h2></div></div><button onClick={onRefresh} disabled={busy} className="btn btn-glass px-4 py-2">Refresh X metrics</button></div>
    <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {[['Impressions', 'impressions'], ['Likes', 'likes'], ['Replies', 'replies'], ['Reposts', 'reposts'], ['Profile clicks', 'profile_clicks'], ['URL clicks', 'url_clicks'], ['Followers', 'followers'], ['Follower growth', 'followerGrowth']].map(([label, key]) => <div key={key} className="rounded-xl bg-white/[.04] p-4"><p className="text-xs font-black uppercase text-slate-500">{label}</p><p className="mt-2 text-sm font-black">{metric(key === 'followerGrowth' ? analytics.followerGrowth : totals[key])}</p></div>)}
    </div>
    <div className="mt-6 grid gap-6 lg:grid-cols-3"><Breakdown title="By post type" rows={analytics.byPostType} /><Breakdown title="By media type" rows={analytics.byMediaType} /><Breakdown title="By visual template" rows={analytics.byTemplate} /></div>
    <div className="mt-6"><p className="text-xs font-black uppercase tracking-wider text-slate-500">Best-performing posts</p><div className="mt-2 flex flex-wrap gap-2">{analytics.bestPerformingPosts?.length ? analytics.bestPerformingPosts.map((post) => <span key={post.postId} className="rounded-xl bg-white/[.04] px-3 py-2 text-xs">{post.postId.slice(0, 8)} · {metric(post.impressions)} impressions · {rate(post.engagementRate)}</span>) : <span className="text-sm text-slate-500">No impression data is available.</span>}</div></div>
  </GlowCard>;
}
