import Link from 'next/link';
import GlowCard from '@/components/ui/GlowCard';
import {
  normalizeProviderStatus,
  providerStatusLabel,
  readableProviderName,
  safeText,
} from '@/lib/normalizeApiData';

export default function AnalysisResult({ result, onRestart }) {
  const normalizedResult = result || {};
  const providerStatus = normalizeProviderStatus(normalizedResult.providerStatus);
  return (
    <GlowCard className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="tag w-fit">
            {providerStatusLabel(normalizedResult.providerStatus)}
            {providerStatus.configuredProviders.length
              ? ` · ${providerStatus.configuredProviders.map(readableProviderName).join(', ')}`
              : ''}
          </p>
          <h2 className="mt-4 text-3xl font-black tracking-[-.04em]">
            {safeText(normalizedResult.sport, 'Sport')} · {safeText(normalizedResult.action, 'Analysis')}
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Generated: {safeText(normalizedResult.generatedAt, 'unknown')} · Save: {safeText(normalizedResult.saveStatus, 'not saved')}
          </p>
          {normalizedResult.confidenceContext ? (
            <p className="mt-2 max-w-2xl text-sm text-amber-100">{safeText(normalizedResult.confidenceContext)}</p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <button onClick={onRestart} className="btn btn-glass">Start Another Analysis</button>
          <Link href="/dashboard" className="btn btn-primary">Return to Dashboard</Link>
        </div>
      </div>
      <ResultBody result={normalizedResult} />
    </GlowCard>
  );
}

function ResultBody({ result }) {
  if (result.error) {
    return (
      <div className="mt-6 rounded-2xl border border-red-400/30 bg-red-500/10 p-4 text-red-100">
        <p>{safeText(result.error, 'Analysis data could not be displayed safely.')}</p>
      </div>
    );
  }
  const items = result.items || result.legs || result.predictions || [];
  if (!Array.isArray(items) || !items.length) {
    return <div className="mt-6 rounded-2xl bg-white/5 p-6 text-gray-300">{safeText(result.message, 'No result rows were returned.')}</div>;
  }
  return (
    <div className="mt-6 grid gap-3">
      {items.map((item, index) => (
        <div key={item.id || `${item.player || item.summary || item.prediction || 'result'}-${index}`} className="rounded-2xl border border-white/10 bg-white/[.04] p-4">
          <div className="flex flex-wrap justify-between gap-3">
            <strong>{safeText(item.player || item.summary || item.prediction, `Result ${index + 1}`)}</strong>
            <span className="text-violet-200">{item.confidence ? `${Number(item.confidence).toFixed(0)}%` : safeText(item.result_status, '')}</span>
          </div>
          <div className="mt-2 grid gap-2 text-sm text-gray-400 md:grid-cols-4">
            {Object.entries(item || {}).filter(([key]) => !['player', 'summary'].includes(key)).slice(0, 8).map(([key, value]) => (
              <span key={key}><b className="text-gray-300">{key}: </b>{safeText(value)}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
