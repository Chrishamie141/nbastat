const LABELS = {
  automation_enabled: "Social automation enabled", dry_run: "Dry run", auto_publish: "Auto publish",
  ai_copy_enabled: "AI captions", ai_images_enabled: "AI images", video_enabled: "Video",
  result_receipts_enabled: "Result receipts", line_movement_enabled: "Line movement posts",
  daily_recap_enabled: "Daily recap", weekly_recap_enabled: "Weekly recap",
  engagement_questions_enabled: "Engagement questions", external_auto_replies_enabled: "External auto replies",
  live_reactions_enabled: "Live reactions",
};

export default function SocialSettings({ settings, onChange, busy }) {
  const toggles = Object.keys(LABELS);
  const numbers = [
    ["opportunity_score_threshold", "Score threshold"], ["min_post_interval_minutes", "Minimum minutes between posts"],
    ["max_posts_per_day", "Maximum posts/day"], ["max_images_per_day", "Maximum images/day"],
    ["max_videos_per_day", "Maximum videos/day"], ["max_result_receipts_per_day", "Maximum receipts/day"],
  ];
  return <div className="space-y-5">
    <div className="grid gap-3 sm:grid-cols-2">
      {toggles.map((key) => <label key={key} className="flex items-center justify-between rounded-xl bg-white/[.04] p-3 text-sm font-bold">
        {LABELS[key]}<input type="checkbox" checked={Boolean(settings[key])} onChange={(event) => onChange({ [key]: event.target.checked })} disabled={busy} className="h-5 w-5 accent-emerald-400" />
      </label>)}
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      {numbers.map(([key, label]) => <label key={key} className="text-xs font-black uppercase tracking-wider text-slate-400">{label}<input type="number" value={settings[key] ?? ""} onChange={(event) => onChange({ [key]: Number(event.target.value) })} disabled={busy} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-white" /></label>)}
      <label className="text-xs font-black uppercase tracking-wider text-slate-400">Image quality<select value={settings.image_quality || "high"} onChange={(event) => onChange({ image_quality: event.target.value })} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-white"><option>low</option><option>medium</option><option>high</option></select></label>
    </div>
    <p className="text-xs text-slate-500">Secrets stay in the deployment environment and are never shown here. External replies remain approval-only unless explicitly enabled.</p>
  </div>;
}
