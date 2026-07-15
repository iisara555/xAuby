import Link from "next/link";

export default function HomePage() {
  return (
    <main className="site-shell">
      <header className="site-dock" aria-label="xAuby navigation">
        <Link className="site-brand" href="/">
          <span>xAuby</span>
          <small>Alternative Store of Value</small>
        </Link>
        <nav>
          <a href="/research-platform.html#research">Research</a>
          <Link className="site-console-link" href="/app">Open Console</Link>
        </nav>
      </header>
      <iframe
        className="site-frame"
        src="/research-platform.html"
        title="xAuby Gold Trading Research Platform"
        loading="eager"
      />
    </main>
  );
}
