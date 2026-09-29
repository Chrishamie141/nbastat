import Link from "next/link";

export default function NotFound() {
  return <main className="mx-auto min-h-[65vh] max-w-2xl px-6 py-20">
    <p className="text-cyan-300">404</p><h1 className="mt-2 text-3xl font-bold">Page not found</h1>
    <p className="mt-4 text-slate-300">This link may be out of date. Choose a current page from the menu or return home.</p>
    <Link href="/" className="btn btn-primary mt-6">Back to home</Link>
  </main>;
}
