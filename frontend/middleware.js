import {NextResponse} from 'next/server';
import {CANONICAL_ORIGIN} from './lib/site-url';

const AUTH_PATHS = new Set(['/login','/register','/forgot-password','/reset-password','/setup']);

export function middleware(request){
  const url=request.nextUrl.clone();
  let changed=false;
  if(process.env.VERCEL_ENV==='production'&&url.origin!==CANONICAL_ORIGIN){
    const canonical=new URL(CANONICAL_ORIGIN);url.protocol=canonical.protocol;url.hostname=canonical.hostname;url.port='';changed=true;
  }
  const normalizedPath=url.pathname.toLowerCase().replace(/\/+$/,'')||'/';
  if(AUTH_PATHS.has(normalizedPath)){
    if(url.pathname!==normalizedPath){url.pathname=normalizedPath;changed=true}
    for(const key of [...url.searchParams.keys()]){if(key!=='next'){url.searchParams.delete(key);changed=true}}
    if(url.searchParams.has('next')){
      const raw=url.searchParams.get('next');
      try{const next=new URL(raw,'https://smartbetsports.com');const clean=next.origin==='https://smartbetsports.com'?next.pathname:'/dashboard';if(raw!==clean){url.searchParams.set('next',clean);changed=true}}catch{url.searchParams.delete('next');changed=true}
    }
  }
  if(normalizedPath==='/internal/operations'){
    url.pathname='/command-center';changed=true;
  }
  return changed?NextResponse.redirect(url,308):NextResponse.next();
}

export const config={matcher:['/((?!_next/static|_next/image|favicon.ico).*)']};
