export function normalizeProviderStatus(value){
  const fallback={status:'unavailable',configuredProviders:[]};
  if(value==null)return fallback;
  if(typeof value==='string')return{status:normalizeStatus(value),configuredProviders:[]};
  if(Array.isArray(value))return{status:value.length?'partial_live':'unavailable',configuredProviders:value.map(String)};
  if(typeof value==='object')return{status:normalizeStatus(value.status||value.dataMode),configuredProviders:Array.isArray(value.configuredProviders)?value.configuredProviders.map(String):[]};
  return fallback;
}
export function normalizeStatus(status){const s=String(status||'').toLowerCase().replace(/\s+/g,'_');if(['live','partial_live','sample','unavailable'].includes(s))return s;if(s.includes('partial'))return'partial_live';if(s.includes('live'))return'live';if(s.includes('sample')||s.includes('fallback'))return'sample';return'unavailable'}
export function providerStatusLabel(value){const p=normalizeProviderStatus(value);return{live:'Live data',partial_live:'Partial live data',sample:'Sample data',unavailable:'Data unavailable'}[p.status]}
export function readableProviderName(name){return String(name||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase())}
export function safeText(value,fallback='—'){if(value==null||value==='')return fallback;if(typeof value==='string'||typeof value==='number'||typeof value==='boolean')return String(value);if(Array.isArray(value))return value.map(v=>safeText(v,'')).filter(Boolean).join(', ')||fallback;try{return JSON.stringify(value)}catch{return fallback}}
